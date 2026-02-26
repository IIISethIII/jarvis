import asyncio
import datetime
import math
import random
import struct
import time
import wave
import sys
import signal
import threading
import multiprocessing
import queue
import pyaudio
import pvporcupine
import pvcobra
import os
from jarvis.services.hardware import BonnetHardware
from aiy.leds import Color, Pattern
# from aiy.leds import leds

from jarvis import config, state
from jarvis.services import system, timer, ha, google, sfx, memory, routine
from jarvis.core import llm
from jarvis.core.live import JarvisHybridRouter

def audio_worker(output_queue, frame_length, rate, channels):
    import pyaudio
    try:
        from ctypes import CFUNCTYPE, c_char_p, c_int, cdll
        def py_error_handler(filename, line, function, err, fmt): pass
        c_error_handler = CFUNCTYPE(None, c_char_p, c_int, c_char_p, c_int, c_char_p)(py_error_handler)
        asound = cdll.LoadLibrary('libasound.so.2')
        asound.snd_lib_error_set_handler(c_error_handler)
    except: pass

    def get_mic_index(pa):
        for i in range(pa.get_device_count()):
            try:
                info = pa.get_device_info_by_index(i)
                if "jarvis_mic" in info.get('name', ''): return i
            except: pass
        return None

    pa = pyaudio.PyAudio()
    mic_index = get_mic_index(pa)
    stream = pa.open(rate=rate, channels=channels, format=pyaudio.paInt16, input=True, input_device_index=mic_index, frames_per_buffer=frame_length)
    try:
        while True:
            pcm = stream.read(frame_length, exception_on_overflow=False)
            output_queue.put(pcm)
    except: pass 
    finally:
        stream.close(); pa.terminate()

# --- MAIN HELPERS ---
def get_rms(pcm_data):
    count = len(pcm_data) // 2
    shorts = struct.unpack(f"{count}h", pcm_data)
    sum_squares = sum(s**2 for s in shorts)
    return math.sqrt(sum_squares / count) if count > 0 else 0

def lower_volume():
    if state.PREVIOUS_VOLUME is None:
        try:
            headers = {"Authorization": "Bearer " + config.HA_TOKEN}
            r = ha.session.get(f"{config.HA_URL}/api/states/sensor.hifiberry_plexamp_volume", headers=headers, timeout=2)
            if r.status_code == 200: 
                vol = float(r.json()['state'])
                state.PREVIOUS_VOLUME = vol
                ha.execute_media_control("volume_set", volume_level=vol/2)
        except: pass

def restore_volume():
    if state.PREVIOUS_VOLUME is not None:
        ha.execute_media_control("volume_set", volume_level=state.PREVIOUS_VOLUME)
        state.PREVIOUS_VOLUME = None

def fade_color(leds, start_color, end_color, duration=0.5):
    """Restored: Erzeugt einen weichen Farbübergang."""
    steps = 25
    delay = duration / steps
    r1, g1, b1 = start_color
    r2, g2, b2 = end_color
    for i in range(steps + 1):
        factor = i / steps
        r = int(r1 + (r2 - r1) * factor)
        g = int(g1 + (g2 - g1) * factor)
        b = int(b1 + (b2 - b1) * factor)
        leds.update(leds.rgb_on((r, g, b)))
        time.sleep(delay)

def flush_queue(q):
    """Leert die Audio-Queue, damit wir kein Echo der eigenen TTS-Antwort verarbeiten."""
    try:
        while not q.empty():
            q.get_nowait()
    except queue.Empty:
        pass

def main():
    def handle_sigterm(signum, frame):
        print("Received SIGTERM from systemd. Cleaning up...")
        leds.update(leds.rgb_off())
        restore_volume()
        os._exit(0)

    signal.signal(signal.SIGTERM, handle_sigterm)

    system.init_audio_settings()
    sfx.init() 
    threading.Thread(target=timer.background_timer_check, daemon=True).start()
    last_dream_date = None

    # Porcupine Init
    try:
        porcupine = pvporcupine.create(access_key=config.PICOVOICE_KEY, keywords=[config.WAKE_WORD], sensitivities=[0.4])
        # cobra = pvcobra.create(access_key=config.PICOVOICE_KEY)  # Deprecated in favor of Gemini Live API
        frame_length = porcupine.frame_length
    except Exception as e:
        print(f"Init Error: {e}"); return

    # Init Wakeup State
    state.LAST_WAKEUP_DATE = datetime.datetime.now().date()
    # Default: Erste Ausführung geplant in 3h
    state.NEXT_WAKEUP = time.time() + (3 * 60 * 60)

    with BonnetHardware() as hw:
        # 1. Definieren des LED-Proxys (für Kompatibilität mit altem Code)
        class LedProxy:
            def __init__(self):
                self.pattern = None
                self._force_pattern = False

            def update(self, val): 
                is_pattern = bool(self.pattern) or self._force_pattern
                
                if is_pattern:
                    hw.start_pulse(*val)
                elif val == (0,0,0) or val == "":
                    hw.stop_effect()
                else:
                    hw.stop_effect()
                    hw.set_led(*val)
                
                self.pattern = None
                self._force_pattern = False

            def rgb_on(self, color): 
                self._force_pattern = False
                return color
                
            def rgb_off(self): 
                self._force_pattern = False
                return (0,0,0)
                
            def rgb_pattern(self, color): 
                self._force_pattern = True
                return color

        # 2. Button Logik (muss VOR der Zuweisung definiert sein)
        def on_button_press():
            if state.ALARM_PROCESS or state.ACTIVE_TIMERS:
                timer.stop_alarm_sound(); state.LED_LOCKED=True
                leds.update(Color.RED); time.sleep(1); leds.update(leds.rgb_off()); state.LED_LOCKED=False
            elif state.session_active() or state.IS_PROCESSING:
                state.CANCEL_REQUESTED = True
                state.LED_LOCKED=True; leds.update(Color.RED); time.sleep(0.5); leds.update(leds.rgb_off()); state.LED_LOCKED=False
            else:
                rand_color = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                state.LED_LOCKED=True
                leds.update(rand_color)
                time.sleep(0.5)
                leds.update(leds.rgb_off())
                state.LED_LOCKED=False
        
        # Button an Hardware binden
        hw.button.when_pressed = on_button_press
            
        leds = LedProxy()

        ctx, lookup = ha.fetch_ha_context()
        state.HA_CONTEXT = ctx
        state.AVAILABLE_LIGHTS = lookup

        # --- MULTIPROCESSING SETUP ---
        audio_queue = multiprocessing.Queue(maxsize=50) 
        audio_proc = None

        def start_audio_process():
            p = multiprocessing.Process(target=audio_worker, args=(audio_queue, frame_length, config.RATE, config.CHANNELS))
            p.daemon = True
            p.start()
            return p

        audio_proc = start_audio_process()
        
        # Init Hybrid Fast Brain Router
        router = JarvisHybridRouter(leds, audio_queue)

        def check_for_interruption():
            if state.CANCEL_REQUESTED:
                print("\n--> CANCELLATION DETECTED!")
                return True

            try:
                # Wir schauen, ob Audio in der Queue ist
                while not audio_queue.empty():
                    # get_nowait ist wichtig, damit wir hier NICHT blockieren
                    pcm_chunk = audio_queue.get_nowait()
                    
                    # Prüfen auf Wake Word
                    keyword_index = porcupine.process(struct.unpack_from("h" * porcupine.frame_length, pcm_chunk))
                    if keyword_index >= 0:
                        print("\n--> INTERRUPT DETECTED!")
                        return True # Signalisiert: Sofort aufhören zu sprechen!
            except:
                pass
            return False

        from jarvis.core.mcp import mcp_client
        from jarvis.core.tools import FUNCTION_DECLARATIONS

        try:
            print("Verbinde mit Octopus MCP Server...")
            mcp_client.start_sync()
            dynamic_tools = mcp_client.get_gemini_tools_sync()
            FUNCTION_DECLARATIONS.extend(dynamic_tools)
            print(f" [MCP] {len(dynamic_tools)} externe Tools geladen (z.B. {dynamic_tools[0]['name'] if dynamic_tools else 'Keine'})")
        except Exception as e:
            print(f" [MCP Error] Konnte nicht verbinden: {e}")
        
        leds.update(Color.BLUE)
        print(f"\nJarvis Online | Devices: {len(state.AVAILABLE_LIGHTS)}")
        google.speak_text(leds, "Ich bin jetzt online.")
        leds.update(leds.rgb_off())
        flush_queue(audio_queue) # Start clean
        
        last_log_time = time.time()
        last_mailbox_check = time.time()
        last_routine_check = time.time()

        mic_fail_count = 0

        def update_ha_context_bg():
            try:
                # print(" [Debug] Refreshing HA Context...")
                new_ctx, new_lookup = ha.fetch_ha_context()
                if new_ctx: state.HA_CONTEXT = new_ctx
                if new_lookup: state.AVAILABLE_LIGHTS.update(new_lookup)
            except: pass
        
        try:
            while True:
                incoming_text = None
                # --- AUTONOMOUS WAKEUP CHECK ---
                now_ts = time.time()
                now_dt = datetime.datetime.now()
                
                # A) Daily Reset
                if now_dt.date() > state.LAST_WAKEUP_DATE:
                    print(f" [System] 🌅 Neuer Tag! Reset Wakeup Count (Gestern: {state.WAKEUP_COUNT})")
                    state.WAKEUP_COUNT = 0
                    state.LAST_WAKEUP_DATE = now_dt.date()
                
                # B) Check Wakeup Time
                if now_ts >= state.NEXT_WAKEUP:
                    if state.WAKEUP_COUNT < 10:
                        print(f"\n--> ⏰ AUTONOMOUS WAKEUP (Reason: {state.WAKEUP_REASON})")
                        state.WAKEUP_COUNT += 1
                        
                        # Trigger Processing
                        state.IS_PROCESSING = True
                        # lower_volume() - Removed for silent wakeup to prevent dipping
                        
                        # Special Logic: Direkt in die LLM Pipeline springen ohne Audio
                        # Wir simulieren "Text Input" aber markieren es als intern
                        incoming_text = f"INTERNAL_WAKEUP_TRIGGER: {state.WAKEUP_REASON}"
                        
                        # Setze nächsten Default-Wakeup (Fallback)
                        # Wenn die LLM Pipeline 'schedule_wakeup' nutzt, wird diese Variable überschrieben.
                        state.NEXT_WAKEUP = now_ts + (3 * 60 * 60)
                        state.WAKEUP_REASON = "Routine Check"
                    else:
                        # Limit reached
                        if state.NEXT_WAKEUP < now_ts + 3600: # Nur einmal loggen wenn wir drüber rutschen
                            print(" [System] 💤 Daily Wakeup Limit reached. Sleep until morning.")
                            # Schlaf bis morgen 08:00
                            tomorrow_8am = datetime.datetime.combine(now_dt.date() + datetime.timedelta(days=1), datetime.time(8, 0))
                            state.NEXT_WAKEUP = tomorrow_8am.timestamp()
                            state.WAKEUP_REASON = "Morning Start"

                # C) Routine Background Check (every 60s)
                if time.time() - last_routine_check > 60:
                    last_routine_check = time.time()
                    threading.Thread(target=routine.check_background_routine, daemon=True).start()

                # 1. AUDIO LESEN (WATCHDOG)
                # Nur lesen, wenn wir nicht schon einen internen Trigger haben
                if not incoming_text:
                    try:
                        # Prüfe ob Daten da sind
                        pcm = audio_queue.get(timeout=3)
                        mic_fail_count = 0
                    except queue.Empty:
                        mic_fail_count += 1
                        print(f" [Watchdog] Mic tot! ({mic_fail_count}/5) Starte Treiber neu...")
                        
                        # Wenn zu viele Versuche scheitern, den gesamten Dienst neustarten
                        if mic_fail_count >= 5:
                            print(" [System] Kritischer Audio-Fehler. Starte Service neu...")
                            system.restart_service()
                            break # Loop verlassen, damit der Prozess endet
                        
                        if audio_proc.is_alive():
                            audio_proc.terminate()
                            audio_proc.join(timeout=0.1)
                        
                        audio_proc = start_audio_process()
                        time.sleep(3.0) 
                        continue
                else:
                    # Fake PCM für den Fall dass wir durchfallen (sollte nicht passieren da wir verarbeiten)
                    pcm = None

                # MAILBOX CHECK
                if time.time() - last_mailbox_check > 1.5:
                    last_mailbox_check = time.time()
                    try:
                        # Check Input Text (Home Assistant)
                        ha_text = ha.get_input_text_state("input_text.jarvis_chat")
                        if ha_text and len(ha_text) > 1:
                            incoming_text = ha_text
                            ha.clear_input_text("input_text.jarvis_chat")
                            print(f"\n--> 📩 Remote: {incoming_text}")
                            # leds.update(leds.rgb_on(Color.CYAN)) - SILENT
                            flush_queue(audio_queue) 
                        
                        # Check "Internal Wakeup" (wird oben gesetzt)
                        elif incoming_text and "INTERNAL_WAKEUP_TRIGGER" in incoming_text:
                            # NEW: Self-Destruct Logic
                            if "|" in incoming_text:
                                try:
                                    # Format: INTERNAL_WAKEUP_TRIGGER|auto_id|summary
                                    parts = incoming_text.split("|")
                                    if len(parts) >= 3:
                                        auto_id = parts[1]
                                        summary = parts[2]
                                        # OLD: ha.delete_ha_automation(auto_id) -> Jetzt manuell
                                        
                                        # Clean text for LLM, but include ID for deletion recommendation
                                        incoming_text = f"INTERNAL_WAKEUP_TRIGGER: {summary} [Automation ID: {auto_id}]"
                                except Exception as e:
                                    print(f" [Auto-Wakeup Error] {e}")

                            # leds.update(leds.rgb_on(Color.MAGENTA)) - SILENT
                            flush_queue(audio_queue)

                    except: pass

                # 2. VAD & Wake Word Logic (OR Text)
                if state.session_active() or incoming_text:
                    state.IS_PROCESSING = True

                    threading.Thread(target=update_ha_context_bg, daemon=True).start()

                    if not incoming_text:
                        lower_volume()
                    user_text = None
                    wav_data = None

                    if incoming_text:
                        # TEXT MODE (Autonomous or Mailbox)
                        if state.CANCEL_REQUESTED:
                            print(" [System] Cancelled by User.")
                            state.CANCEL_REQUESTED = False
                            state.SESSION_OPEN_UNTIL = 0
                            state.IS_PROCESSING = False
                            leds.update(leds.rgb_off())
                            flush_queue(audio_queue)
                            continue

                        restore_volume()
                        response = "Fehler."
                        try:
                            hybrid_context = memory.get_hybrid_context(incoming_text)
                            final_prompt = f"{hybrid_context}\n\nUSER TEXT INPUT:\n{incoming_text}\n\n(Antworte dem User.)"
                            
                            if state.CANCEL_REQUESTED: 
                                response = "<SILENT>"
                            else:
                                response = llm.ask_gemini(leds, text_prompt=final_prompt, audio_data=None, silent_mode=True)
                            
                            clean_resp = response.replace("<SESSION:KEEP>", "").replace("<SESSION:CLOSE>", "").strip()
                            memory.save_interaction(incoming_text, clean_resp)

                        except Exception as e: 
                            print(f" [Main Loop Error Text Mode] {e}")
                            clean_resp = "Fehler bei der Textverarbeitung."

                        if "<SILENT>" in clean_resp:
                            clean_resp = clean_resp.replace("<SILENT>", "").strip()
                            print(" [Output] <SILENT>")
                        else:
                            if not state.CANCEL_REQUESTED:
                                google.speak_text(leds, clean_resp, interrupt_check=check_for_interruption)

                        ha.set_state("sensor.jarvis_last_response", clean_resp[:250], attributes={"full_text": clean_resp})
                        flush_queue(audio_queue)
                        state.SESSION_OPEN_UNTIL = 0
                        leds.update(leds.rgb_off())
                        state.IS_PROCESSING = False
                        
                    else:
                        # FAST BRAIN AUDIO MODE (Two-Brain Hybrid Architecture)
                        lower_volume()
                        
                        try:
                            # Start Live API WebSockets Router natively
                            asyncio.run(router.start_session())
                        except Exception as e:
                            print(f" [Fast Brain Error] {e}")
                        
                        finally:
                            restore_volume()
                            flush_queue(audio_queue)
                            state.SESSION_OPEN_UNTIL = 0
                            leds.update(leds.rgb_off())
                            state.IS_PROCESSING = False
                    continue

                # --- WAKE WORD ---
                if state.ALARM_PROCESS:
                      leds.pattern = Pattern.breathe(1000); leds.update(leds.rgb_pattern(config.COLOR_ALARM))

                # Heartbeat (alle 10s)
                if time.time() - last_log_time > 10:
                    last_log_time = time.time()

                if porcupine.process(struct.unpack_from("h" * porcupine.frame_length, pcm)) >= 0:
                    print("\n--> Wake Word Detected")
                    sfx.play(config.SOUND_WAKE, volume=1.0)
                    lower_volume()
                    if state.ALARM_PROCESS:
                        timer.stop_alarm_sound()
                        google.speak_text(leds, "Wecker gestoppt.")
                        leds.update(leds.rgb_off())
                        flush_queue(audio_queue) # Auch hier wichtig
                        restore_volume()
                        continue
                    state.open_session(8)

                now = datetime.datetime.now()
                if now.hour == 4 and last_dream_date != now.date():
                    if not state.session_active():
                        print(f" [System] 🌙 Nightly Maintenance (Datum: {now.date()})")
                        threading.Thread(target=memory.dream, daemon=True).start()
                        last_dream_date = now.date()

        except KeyboardInterrupt: pass
        finally:
            if audio_proc: audio_proc.terminate()
            if 'porcupine' in locals(): porcupine.delete()
            if 'cobra' in locals() and cobra: cobra.delete()

if __name__ == "__main__":
    main()
