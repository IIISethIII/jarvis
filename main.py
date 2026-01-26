import math
import struct
import time
import wave
import threading
import multiprocessing
import queue
import pyaudio
import pvporcupine
import pvcobra
from aiy.board import Board
from aiy.leds import Leds, Color, Pattern

from jarvis import config, state
from jarvis.services import system, timer, ha, google, sfx, memory
from jarvis.core import llm

# --- WORKER PROCESS: Liest Audio isoliert ---
def audio_worker(output_queue, frame_length, rate, channels):
    """
    Läuft in einem eigenen Prozess. Liest nur Audio und schiebt es in die Queue.
    """
    import pyaudio
    
    # Unterdrücke ALSA Fehlermeldungen
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
                if "jarvis_mic" in info.get('name', ''):
                    return i
            except: pass
        return None

    pa = pyaudio.PyAudio()
    stream = None
    
    try:
        mic_index = get_mic_index(pa)
        stream = pa.open(
            rate=rate,
            channels=channels,
            format=pyaudio.paInt16,
            input=True,
            input_device_index=mic_index,
            frames_per_buffer=frame_length
        )
        
        while True:
            pcm = stream.read(frame_length, exception_on_overflow=False)
            output_queue.put(pcm)
            
    except Exception:
        pass 
    finally:
        try:
            if stream: stream.close()
            pa.terminate()
        except: pass

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
        leds.update(Leds.rgb_on((r, g, b)))
        time.sleep(delay)

def flush_queue(q):
    """Leert die Audio-Queue, damit wir kein Echo der eigenen TTS-Antwort verarbeiten."""
    try:
        while not q.empty():
            q.get_nowait()
    except queue.Empty:
        pass

def main():
    system.init_audio_settings()
    sfx.init() 
    threading.Thread(target=timer.background_timer_check, daemon=True).start()

    # Porcupine Init
    try:
        porcupine = pvporcupine.create(access_key=config.PICOVOICE_KEY, keywords=[config.WAKE_WORD], sensitivities=[0.4])
        cobra = pvcobra.create(access_key=config.PICOVOICE_KEY)
        frame_length = porcupine.frame_length
    except Exception as e:
        print(f"Init Error: {e}"); return

    with Board() as board, Leds() as leds:
        ctx, lookup = ha.fetch_ha_context()
        state.HA_CONTEXT = ctx
        state.AVAILABLE_LIGHTS = lookup
        
        def on_button_press():
            if state.ALARM_PROCESS or state.ACTIVE_TIMERS:
                timer.stop_alarm_sound(); state.LED_LOCKED=True; leds.update(Leds.rgb_on(Color.RED)); time.sleep(1); state.LED_LOCKED=False
            else:
                state.LED_LOCKED=True; leds.update(Leds.rgb_on(Color.GREEN)); time.sleep(0.2); state.LED_LOCKED=False
        board.button.when_pressed = on_button_press

        # --- MULTIPROCESSING SETUP ---
        audio_queue = multiprocessing.Queue(maxsize=50) 
        audio_proc = None

        def start_audio_process():
            p = multiprocessing.Process(target=audio_worker, args=(audio_queue, frame_length, config.RATE, config.CHANNELS))
            p.daemon = True
            p.start()
            return p

        audio_proc = start_audio_process()

        def check_for_interruption():
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
        
        print(f"\nJarvis Online | Devices: {len(state.AVAILABLE_LIGHTS)}")
        google.speak_text(leds, "Ich bin jetzt online.")
        leds.update(Leds.rgb_off())
        flush_queue(audio_queue) # Start clean
        
        last_log_time = time.time()

        mic_fail_count = 0
        
        try:
            while True:
                # 1. AUDIO LESEN (WATCHDOG)
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

                # 2. VAD & Wake Word Logic (Normal)
                if state.session_active():

                    lower_volume()

                    current_brightness = 0.4; target_brightness = 0.4
                    leds.update(Leds.rgb_on(config.DIM_PURPLE))
                    frames = []
                    is_speaking = False; speech_consecutive = 0; start_time = time.time(); silence_start = None
                    
                    while True:
                        try:
                            pcm_vad = audio_queue.get(timeout=1.0)
                        except queue.Empty: break 
                        
                        frames.append(pcm_vad)
                        prob = cobra.process(struct.unpack_from("h" * cobra.frame_length, pcm_vad))
                        
                        if prob > 0.10: target_brightness = 0.85; speech_consecutive += 1
                        else: target_brightness = 0.4; speech_consecutive = 0
                        
                        step = 0.15
                        if current_brightness < target_brightness: current_brightness = min(target_brightness, current_brightness + step)
                        elif current_brightness > target_brightness: current_brightness = max(target_brightness, current_brightness - step)
                        if abs(current_brightness - target_brightness) > 0.01 or step > 0:
                             leds.update(Leds.rgb_on(Color.blend(Color.PURPLE, Color.BLACK, current_brightness)))

                        if speech_consecutive >= 2: is_speaking = True; silence_start = None
                        elif is_speaking:
                             if not silence_start: silence_start = time.time()
                             elif time.time() - silence_start > 1.5: break
                        elif time.time() - start_time > 8.0: break

                        # --- RESTORED: Dynamic HA Context Update ---
                        # Ich habe es auf "Genau bei Frame 25" geändert, damit es nur einmal pro Satz läuft
                        # und nicht das System durch Dauerfeuer blockiert.
                        if len(frames) == 25:
                            def update_ha_bg():
                                try:
                                    # print(" [Debug] Hole HA Kontext...")
                                    new_ctx, new_lookup = ha.fetch_ha_context()
                                    if new_ctx: state.HA_CONTEXT = new_ctx
                                    if new_lookup: state.AVAILABLE_LIGHTS.update(new_lookup)
                                except: pass
                            
                            # Starten als Thread, damit die Audio-Schleife NICHT wartet
                            threading.Thread(target=update_ha_bg, daemon=True).start()

                    if is_speaking:
                        restore_volume()
                        sfx.play_loop(config.SOUND_THINKING)
                        leds.pattern = Pattern.breathe(1000)
                        leds.update(Leds.rgb_pattern(config.DIM_BLUE))
                        
                        with wave.open("/tmp/req.wav", 'wb') as wf:
                            wf.setnchannels(config.CHANNELS); wf.setsampwidth(2); wf.setframerate(config.RATE)
                            wf.writeframes(b''.join(frames))
                        
                        response = "Fehler."
                        try:
                            # 1. Read the raw bytes for the direct Audio-Input to Gemini
                            with open("/tmp/req.wav", "rb") as f: 
                                wav_data = f.read()
                            
                            # 2. Fast transcription for RAG lookup
                            # We still need this to know WHAT to search for in memory
                            user_text = google.transcribe_audio(wav_data)
                            print(f" --> User (Transcribed): {user_text}")

                            if user_text:
                                # 3. Get RAG context based on transcription
                                rag = memory.retrieve_relevant_memories(user_text)
                                
                                # 4. Build the prompt with RAG context
                                # We explicitly tell the model that the user is speaking in the audio part
                                final_prompt = f"ZUSATZWISSEN(RAG):\n{rag}\n\n(Antworte auf die Audio-Eingabe des Users.)"
                                
                                # 5. Call Gemini with BOTH the prompt and the raw audio data
                                response = llm.ask_gemini(leds, text_prompt=final_prompt, audio_data=wav_data)
                            else:
                                # Handle silence/noise: just send the audio and see if Gemini hears something
                                response = llm.ask_gemini(leds, text_prompt="(Der User hat etwas gesagt, aber die Transkription war leer. Hör genau hin.)", audio_data=wav_data)
                                
                        except Exception as e: 
                            print(f" [Main Loop Error] {e}")
                        finally: 
                            sfx.stop_loop()

                        clean_res = response.replace("<SESSION:KEEP>", "").replace("<SESSION:CLOSE>", "").strip()
                        
                        # TTS spricht hier (dauert ein paar Sekunden)
                        was_interrupted = google.speak_text(leds, clean_res, interrupt_check=check_for_interruption)

                        # WICHTIG: Queue leeren, damit wir nicht Jarvis eigenes Echo hören
                        flush_queue(audio_queue)

                        if was_interrupted:
                             # Wenn unterbrochen wurde, verhalten wir uns wie bei einem Wake-Word
                             sfx.play(config.SOUND_WAKE)
                             lower_volume()
                             state.open_session(8)
                             # Wir springen direkt zum Anfang der Schleife, session ist ja noch aktiv
                             continue

                        if "<SESSION:KEEP>" in response:
                            # --- RESTORED: Fade Effect ---
                            fade_color(leds, config.DIM_BLUE, config.DIM_PURPLE)
                            state.open_session(8)
                            sfx.play(config.SOUND_WAKE)
                        else:
                            state.SESSION_OPEN_UNTIL = 0
                            leds.update(Leds.rgb_off())
                    else:
                        restore_volume(); state.SESSION_OPEN_UNTIL = 0; leds.update(Leds.rgb_off())
                    continue

                # --- WAKE WORD ---
                if state.ALARM_PROCESS:
                      leds.pattern = Pattern.breathe(1000); leds.update(Leds.rgb_pattern(config.DIM_BLUE))

                # Heartbeat (alle 10s)
                if time.time() - last_log_time > 10:
                    last_log_time = time.time()

                if porcupine.process(struct.unpack_from("h" * porcupine.frame_length, pcm)) >= 0:
                    print("\n--> Wake Word Detected")
                    sfx.play(config.SOUND_WAKE)
                    lower_volume()
                    if state.ALARM_PROCESS:
                        timer.stop_alarm_sound()
                        google.speak_text(leds, "Wecker gestoppt.")
                        leds.update(Leds.rgb_off())
                        flush_queue(audio_queue) # Auch hier wichtig
                        continue
                    state.open_session(8)

        except KeyboardInterrupt: pass
        finally:
            if audio_proc: audio_proc.terminate()
            porcupine.delete(); cobra.delete()

if __name__ == "__main__":
    main()