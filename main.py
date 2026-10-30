import argparse
import time
import asyncio
import os
import math
import random
import struct
from core.hardware import BonnetHardware
from core.audio import AudioManager, init_audio_settings
from core.wake import WakeWordDetector
from core.mcp import mcp_client
from core import memory
from core.llm import GeminiLiveProvider

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run in remote test mode (waits for trigger.tmp)")
    args = parser.parse_args()

    print("========================================")
    print(" JARVIS V3 - MAIN DAEMON")
    print("========================================")

    hardware = BonnetHardware()
    hardware.set_color("purple", blink=True)

    print("[Boot] Initializing Audio Hardware...")
    init_audio_settings()

    print("[Boot] Loading Memory...")
    core_mem = memory.get_core_memory()
    
    print("[Boot] Connecting to MCP Servers...")
    mcp_client.start_sync()
    dynamic_tools = mcp_client.get_gemini_tools_sync()
    print(f"[Boot] Found {len(dynamic_tools)} dynamic tools.")

    audio_manager = AudioManager()
    llm_provider = GeminiLiveProvider()

    def on_button_press():
        # Runs in a gpiozero thread. Half-duplex audio means this is the only way to interrupt Jarvis.
        if llm_provider.is_active:
            print("\n[Button] Pressed: cancelling session.", flush=True)
            llm_provider.request_cancel()
            audio_manager.stop_playback()
            hardware.flash(255, 0, 0, duration=0.5)
        else:
            print("[Button] Pressed while idle.", flush=True)
            hardware.flash(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))

    hardware.set_button_handler(on_button_press)

    if args.test:
        print("[Boot] Running in TEST mode. Waiting for mock trigger...")
        trigger_file = "trigger.tmp"
        
        if os.path.exists(trigger_file):
            os.remove(trigger_file)
            
        try:
            while True:
                if os.path.exists(trigger_file):
                    try:
                        with open(trigger_file, "r", encoding="utf-8") as f:
                            mock_text = f.read().strip()
                    except:
                        mock_text = "Hallo"
                        
                    os.remove(trigger_file)
                    print(f"\n🚨 Mock Wake Word Triggered! (Text: {mock_text})")
                    hardware.set_color("purple", blink=False)
                    
                    audio_manager.start_mic()
                    try:
                        asyncio.run(llm_provider.connect(audio_manager, hardware, core_mem, dynamic_tools, mock_text=mock_text if mock_text != "trigger" else None))
                    except Exception as e:
                        print(f"Error in LLM loop: {e}")
                    
                    audio_manager.stop()
                    # Recreate a fresh manager for the next run
                    audio_manager = AudioManager()
                    
                    hardware.turn_off()
                    print("\n[Idle] Waiting for next trigger...", flush=True)
                    
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            hardware.turn_off()
            
    else:
        print("[Boot] Starting Wake Word Detector...")
        wake_detector = WakeWordDetector()
        audio_manager.set_wake_detector(wake_detector)
        audio_manager.start_mic()
        
        from core.sfx import SFXManager
        sfx = SFXManager(audio_manager)
        
        hardware.turn_off()
        sfx.play("success")
        
        try:
            print("[Idle] Listening for 'Hey Jarvis'...")
            mic_fail_count = 0
            silence_count = 0
            
            # Watchdog variables for Rapid-Fire ALSA crash
            chunk_time_start = time.time()
            chunk_count = 0

            while True:
                chunk = audio_manager.get_audio_chunk(timeout=1.0)
                
                if chunk is None:
                    if getattr(audio_manager, 'is_playing', False):
                        mic_fail_count = 0
                        silence_count = 0
                        chunk_time_start = time.time()
                        chunk_count = 0
                    else:
                        mic_fail_count += 1
                        if mic_fail_count >= 3:
                            print(f"[Watchdog] Mic dead (Timeout)! ({mic_fail_count}) Restarting audio process...", flush=True)
                            audio_manager.restart_mic()
                            mic_fail_count = 0
                            silence_count = 0
                            chunk_count = 0
                            chunk_time_start = time.time()
                    continue
                else:
                    mic_fail_count = 0
                    if not getattr(audio_manager, 'is_playing', False):
                        
                        # Rapid-Fire Detection
                        chunk_count += 1
                        if chunk_count >= 100:
                            elapsed = time.time() - chunk_time_start
                            if elapsed < 1.5:  # 100 chunks should take ~3.2 seconds. If <1.5s, ALSA is spamming.
                                print(f"[Watchdog] Mic zombie (Rapid-Fire: 100 chunks in {elapsed:.2f}s)! Restarting audio process...", flush=True)
                                audio_manager.restart_mic()
                                silence_count = 0
                                chunk_count = 0
                                chunk_time_start = time.time()
                                continue
                            
                            # Reset chunk tracker
                            chunk_count = 0
                            chunk_time_start = time.time()

                        # Calculate RMS to detect "zombie" mic producing pure silence
                        count = len(chunk) // 2
                        if count > 0:
                            shorts = struct.unpack(f"{count}h", chunk)
                            sum_squares = sum(s**2 for s in shorts)
                            rms = math.sqrt(sum_squares / count)
                            if rms < 10.0: # Extreme silence threshold
                                silence_count += 1
                                if silence_count >= 100: # ~3 seconds of dead silence chunks
                                    print(f"[Watchdog] Mic zombie (Silence RMS={rms:.2f})! Restarting audio process...", flush=True)
                                    audio_manager.restart_mic()
                                    silence_count = 0
                                    chunk_count = 0
                                    chunk_time_start = time.time()
                            else:
                                silence_count = 0

                        # Queue Backup Detection (Mic Delayed/Dead)
                        try:
                            # qsize() works reliably on Linux (Pi) for multiprocessing.Queue
                            if audio_manager.audio_queue.qsize() > 50:
                                print(f"[Watchdog] Mic queue backed up! (Delayed by >1.5s). Flushing and resetting...", flush=True)
                                audio_manager.flush()
                                wake_detector.reset()
                        except NotImplementedError:
                            pass

                if wake_detector.process_chunk(chunk):
                    print("\n🚨 Wake Word Detected!", flush=True)
                    hardware.set_color("purple", blink=False)
                    sfx.play("wake")
                    
                    try:
                        # Passing sfx to llm_provider
                        asyncio.run(llm_provider.connect(audio_manager, hardware, core_mem, dynamic_tools, sfx=sfx))
                    except Exception as e:
                        print(f"Error in LLM loop: {e}")
                        sfx.play("error")
                    
                    sfx.play("sleep")
                    hardware.turn_off()
                    audio_manager.flush()
                    wake_detector.reset()
                        
                    print("\n[Idle] Listening for 'Hey Jarvis'...")
                    
        except KeyboardInterrupt:
            pass
        finally:
            audio_manager.stop()
            hardware.turn_off()

if __name__ == "__main__":
    main()
