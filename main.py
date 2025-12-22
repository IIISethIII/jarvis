# jarvis/main.py
import struct
import time
import wave
import threading
import pyaudio
import pvporcupine
import pvcobra
from aiy.board import Board
from aiy.leds import Leds, Color, Pattern

from jarvis import config, state
from jarvis.services import system, timer, ha, google, sfx, memory
from jarvis.core import llm

def fade_color(leds, start_color, end_color, duration=0.5):
    """
    Erzeugt einen weichen Farbübergang (Crossfade).
    Dauer ca. 0.5 Sekunden für einen geschmeidigen Effekt.
    """
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

def lower_volume():
    """Senkt die Lautstärke über den regulären Media-Player-Service ab und speichert den vorherigen Wert."""
    volume = None
    try:
        headers = {"Authorization": "Bearer " + config.HA_TOKEN}
        r = ha.session.get(f"{config.HA_URL}/api/states/sensor.hifiberry_plexamp_volume", headers=headers, timeout=2)
        if r.status_code == 200:
            volume = float(r.json()['state'])
        else:
            print(f" [Volume Error] Sensor Antwort: {r.status_code}")
    except Exception as e:
        print(f" [Volume Error] Sensor konnte nicht gelesen werden: {e}")
    
    state.PREVIOUS_VOLUME = volume
    if state.PREVIOUS_VOLUME is not None:
        ha.execute_media_control(command="volume_set", volume_level=state.PREVIOUS_VOLUME/2)


def restore_volume():
    """Stellt die Lautstärke über den regulären Media-Player-Service wieder her."""
    if state.PREVIOUS_VOLUME is not None:
        ha.execute_media_control(command="volume_set", volume_level=state.PREVIOUS_VOLUME)

def main():
    system.init_audio_settings()

    sfx.init()
    
    # Start Timer Thread
    threading.Thread(target=timer.background_timer_check, daemon=True).start()

    try:
        porcupine = pvporcupine.create(access_key=config.PICOVOICE_KEY, keywords=[config.WAKE_WORD])
        cobra = pvcobra.create(access_key=config.PICOVOICE_KEY)
    except Exception as e:
        print(f"Picovoice Init Error: {e}"); return

    with Board() as board, Leds() as leds:
        # Fetch Home Assistant Devices (NEU)
        context_list, device_lookup = ha.fetch_ha_context()
        
        state.HA_CONTEXT = context_list          # Fürs LLM (Status, Attribute)
        state.AVAILABLE_LIGHTS = device_lookup   # Für die Befehls-Suche
        print(f" [HA] Gefundene Geräte: {list(state.AVAILABLE_LIGHTS.keys())}")
        
        # Hardware Button mit visuellem Feedback (Grün)
        def on_button_press():
            if state.ALARM_PROCESS or state.ACTIVE_TIMERS:
                timer.stop_alarm_sound()
                state.LED_LOCKED = True 
                leds.update(Leds.rgb_on(Color.RED))
                time.sleep(1)
                state.LED_LOCKED = False
            else:
                state.LED_LOCKED = True 
                leds.update(Leds.rgb_on(Color.GREEN))
                time.sleep(0.2)
                state.LED_LOCKED = False

        board.button.when_pressed = on_button_press

        pa = pyaudio.PyAudio()
        stream = pa.open(rate=config.RATE, channels=config.CHANNELS, format=pyaudio.paInt16, 
                         input=True, frames_per_buffer=porcupine.frame_length)
        
        print(f"\nJarvis Online | Devices: {len(state.AVAILABLE_LIGHTS)}")
        google.speak_text(leds, "Ich bin jetzt online. Wie kann ich helfen?", stream)
        #google.speak_text_gemini(leds, "Ich bin jetzt online. Wie kann ich helfen?")

        try:
            while True:
                # --- SESSION ACTIVE (Cobra VAD) ---
                if state.session_active():
                    current_brightness = 0.4
                    target_brightness = 0.4
                    leds.update(Leds.rgb_on(Color.blend(Color.PURPLE, Color.BLACK, current_brightness)))

                    leds.update(Leds.rgb_on(config.DIM_PURPLE))
                    frames = []
                    is_speaking = False
                    speech_consecutive = 0
                    start_time = time.time()
                    silence_start = None

                    while True: # VAD Loop
                        pcm = stream.read(cobra.frame_length, exception_on_overflow=False)
                        frames.append(pcm)
                        prob = cobra.process(struct.unpack_from("h" * cobra.frame_length, pcm))

                        if prob > 0.10:
                            target_brightness = 0.85 # goal: bright when speaking
                        else:
                            target_brightness = 0.4 # goal: dim when silent

                        # stepwise adjustment of brightness
                        step = 0.15
                        if current_brightness < target_brightness:
                            current_brightness = min(target_brightness, current_brightness + step)
                        elif current_brightness > target_brightness:
                            current_brightness = max(target_brightness, current_brightness - step)
                        
                        # LED update only if significant change
                        if abs(current_brightness - target_brightness) > 0.01 or step > 0:
                             leds.update(Leds.rgb_on(Color.blend(Color.PURPLE, Color.BLACK, current_brightness)))
                        
                        if prob > 0.10:
                            speech_consecutive += 1

                            if speech_consecutive >= 2: 
                                is_speaking = True
                                silence_start = None
                        else:
                            speech_consecutive = 0

                            if is_speaking:
                                if not silence_start: silence_start = time.time()
                                elif time.time() - silence_start > 1.5: break # Silence detected
                            elif time.time() - start_time > 8.0: break # Timeout

                    if is_speaking:
                        restore_volume()

                        sfx.play_loop(config.SOUND_THINKING)
                        
                        leds.pattern = Pattern.breathe(1000)
                        leds.update(Leds.rgb_pattern(config.DIM_BLUE))

                        if len(frames) > 20:
                            try:
                                new_ctx, new_lookup = ha.fetch_ha_context()
                                if new_ctx: state.HA_CONTEXT = new_ctx
                                if new_lookup: state.AVAILABLE_LIGHTS.update(new_lookup)
                            except: pass

                            # save audio to temp file
                            with wave.open("/tmp/req.wav", 'wb') as wf:
                                wf.setnchannels(config.CHANNELS); wf.setsampwidth(2); wf.setframerate(config.RATE)
                                wf.writeframes(b''.join(frames))
                            
                            response = "Fehler."
                            try:
                                with open("/tmp/req.wav", "rb") as f:
                                    wav_data = f.read()

                                # stt via google
                                user_text = google.transcribe_audio(wav_data)
                                print(f" --> User (STT): \"{user_text}\"")

                                if user_text:
                                    # search relevant memories via RAG
                                    rag_context = memory.retrieve_relevant_memories(user_text)
                                    print(f" --> RAG: {rag_context[:60]}...")

                                    # build final prompt with RAG context
                                    final_prompt = f"ZUSATZWISSEN(RAG):\n{rag_context}\n\nUSER:\n{user_text}"
                                    
                                    # send to LLM
                                    response = llm.ask_gemini(leds, text_prompt=final_prompt, audio_data=None)
                                else:
                                    response = ""

                            except Exception as e:
                                print(f"Processing Error: {e}")
                            finally:
                                sfx.stop_loop()
                            
                            clean_res = response.replace("<SESSION:KEEP>", "").replace("<SESSION:CLOSE>", "").strip()

                            # lower_volume()
                            
                            google.speak_text(leds, clean_res, stream)
                            
                            if "<SESSION:KEEP>" in response:
                                fade_color(leds, config.DIM_BLUE, config.DIM_PURPLE)
                                sfx.play(config.SOUND_WAKE)
                                state.open_session(8)
                            else:
                                state.SESSION_OPEN_UNTIL = 0
                                leds.update(Leds.rgb_off())
                                #restore_volume()
                    else:
                        restore_volume()
                        state.SESSION_OPEN_UNTIL = 0 
                        leds.update(Leds.rgb_off())
                        print(" [Session] Closed (Silence)")

                    continue

                if state.ALARM_PROCESS:
                    leds.pattern = Pattern.breathe(1000)
                    leds.update(Leds.rgb_pattern(config.DIM_BLUE))

                elif state.LED_LOCKED:
                    pass 

                else:
                    leds.update(Leds.rgb_off())
                
                pcm = stream.read(porcupine.frame_length, exception_on_overflow=False)
                if porcupine.process(struct.unpack_from("h" * porcupine.frame_length, pcm)) >= 0:
                    print("\n--> Wake Word Detectedd")
                    sfx.play(config.SOUND_WAKE)
                    lower_volume()

                    if state.ALARM_PROCESS:
                        timer.stop_alarm_sound()
                        state.LED_LOCKED = True 
                        leds.update(Leds.rgb_on(Color.RED))
                        time.sleep(1)
                        state.LED_LOCKED = False
                        google.speak_text(leds, "Wecker gestoppt.", stream)
                        leds.update(Leds.rgb_off())
                        restore_volume()
                        continue
                    
                    state.open_session(8)

        except KeyboardInterrupt: pass
        finally:
            leds.update(Leds.rgb_off())
            stream.close(); pa.terminate(); porcupine.delete(); cobra.delete()

if __name__ == "__main__":
    main()