import argparse
import time
import asyncio
import os
import math
import random
import struct
import sys

from dotenv import load_dotenv

# The real environment (systemd, shell) before .env: its variables win over .env, also when reloading
BASE_ENV = dict(os.environ)
# Load .env before any core module: several read their settings at import time (audio, wake word, VAD)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from core import envfile, settings_schema, status

GEMINI_KEY_NAMES = ("GEMINI_KEYS",) + settings_schema.BY_KEY["GEMINI_KEYS"].aliases


def wait_for_setup():
    """Without a Gemini key Jarvis can't do anything: wait cheaply (before the heavy imports) until the web
    interface or an editor adds one, then start over in a fresh process."""
    if any(os.getenv(k) for k in GEMINI_KEY_NAMES):
        return
    status.init(state="unconfigured", test_mode="--test" in sys.argv, config={},
                error="No Gemini API key yet: set one on the web interface (http://<this device>/) or in .env.")
    print("[Boot] No Gemini API key (GEMINI_KEYS in .env). Waiting for the setup: open the web interface "
          "(http://<this device>/) or edit .env.", flush=True)
    while True:
        time.sleep(5)
        values = envfile.read()
        # as load_dotenv will see it: a variable of the real environment wins over .env
        if any(BASE_ENV[k] if k in BASE_ENV else values.get(k) for k in GEMINI_KEY_NAMES):
            print("[Boot] Gemini key found, starting.", flush=True)
            if os.getenv("INVOCATION_ID"):
                sys.exit(0)  # under systemd: Restart=always starts a fresh process with the new .env
            os.execv(sys.executable, [sys.executable] + sys.argv)


def reload_session_settings():
    """Re-reads the settings that apply per conversation (applies="session") from .env."""
    keys = settings_schema.session_keys()
    changed = envfile.reload(keys, BASE_ENV)
    if changed:
        print(f"[Config] New values for: {', '.join(changed)}", flush=True)
        status.refresh_config(keys, settings_schema.fingerprint(envfile.read()))


if __name__ == "__main__":
    wait_for_setup()

# Heavy imports (Gemini client, MCP, onnxruntime) only once a key is there
from core.hardware import BonnetHardware  # noqa: E402
from core.audio import AudioManager, init_audio_settings  # noqa: E402
from core.wake import WakeWordDetector  # noqa: E402
from core.mcp import mcp_client  # noqa: E402
from core.learner import scheduler as learner  # noqa: E402
from core.llm import GeminiLiveProvider  # noqa: E402
from core.timers import timer_manager  # noqa: E402
from core import events, ha_events  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="Run in remote test mode (waits for trigger.tmp)")
    args = parser.parse_args()

    print("========================================")
    print(" JARVIS V3 - MAIN DAEMON")
    print("========================================")
    status.init(test_mode=args.test, config=settings_schema.fingerprint(envfile.read()))

    hardware = BonnetHardware()
    hardware.set_color("purple", blink=True)

    print("[Boot] Initializing Audio Hardware...")
    init_audio_settings()

    print("[Boot] Connecting to MCP Servers...")
    mcp_client.start_sync()
    dynamic_tools = mcp_client.get_gemini_tools_sync()
    print(f"[Boot] Found {len(dynamic_tools)} dynamic tools.")
    status.update(mcp={"servers": mcp_client.server_status(), "tools": len(dynamic_tools)})

    audio_manager = AudioManager()
    llm_provider = GeminiLiveProvider()

    def on_button_press():
        # Runs in a gpiozero thread. Half-duplex audio means this is the only way to interrupt Jarvis.
        if timer_manager.stop_ringing("button"):
            return
        if llm_provider.is_active:
            print("\n[Button] Pressed: cancelling session.", flush=True)
            llm_provider.request_cancel()
            audio_manager.stop_playback()
            hardware.flash(255, 0, 0, duration=0.5)
        else:
            print("[Button] Pressed while idle.", flush=True)
            hardware.flash(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))

    hardware.set_button_handler(on_button_press)
    # A firing timer ends a running session so the alarm isn't mixed with speech.
    # The lambda reads audio_manager at call time because the test mode replaces it after each session.
    timer_manager.start(lambda: audio_manager, hardware, on_ring=llm_provider.request_cancel)
    # Maintains the workspace files in the background, only while no session runs
    learner.start(is_busy=lambda: llm_provider.is_active)

    if args.test:
        print("[Boot] Running in TEST mode. Waiting for mock trigger...")
        status.update(state="idle")
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
                    reload_session_settings()
                    status.update(state="session")
                    try:
                        asyncio.run(llm_provider.connect(audio_manager, hardware, dynamic_tools, mock_text=mock_text if mock_text != "trigger" else None))
                    except Exception as e:
                        print(f"Error in LLM loop: {e}")
                        status.update(error=f"Session: {e}")
                    status.update(state="idle")
                    learner.session_ended()
                    
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
        # Home Assistant can start conversations (event 'jarvis_trigger', see core/events.py)
        ha_events.start()

        def run_session(event=None):
            """One conversation, started by the wake word or by an event from Home Assistant."""
            hardware.set_color("white" if event else "purple", blink=False)
            sfx.play("wake")
            reload_session_settings()
            status.update(state="session")
            try:
                asyncio.run(llm_provider.connect(audio_manager, hardware, dynamic_tools, sfx=sfx, event=event))
            except Exception as e:
                print(f"Error in LLM loop: {e}")
                status.update(error=f"Session: {e}")
                sfx.play("error")
            status.update(state="idle")
            learner.session_ended()
            sfx.play("sleep")
            hardware.turn_off()
            audio_manager.flush()
            wake_detector.reset()
            print("\n[Idle] Listening for 'Hey Jarvis'...")

        def handle_trigger(received, data):
            """Starts the conversation an automation asked for, or tells Home Assistant why not."""
            max_age = float(os.getenv("JARVIS_EVENT_MAX_AGE", "120"))
            if time.time() - received > max_age:
                events.reject(data, "expired")  # waited too long behind a conversation
                return
            if timer_manager.is_ringing:
                events.reject(data, "busy")
                return
            try:
                event = events.Event(data, received)
            except ValueError as e:
                print(f"[Event] Invalid trigger: {e}", flush=True)
                events.reject(data, "invalid")
                return
            print(f"\n🚨 Event '{event.kind}' from Home Assistant!", flush=True)
            run_session(event)

        hardware.turn_off()
        sfx.play("success")
        
        try:
            print("[Idle] Listening for 'Hey Jarvis'...")
            status.update(state="idle")
            mic_fail_count = 0
            silence_count = 0
            
            # Watchdog variables for Rapid-Fire ALSA crash
            chunk_time_start = time.time()
            chunk_count = 0

            while True:
                chunk = audio_manager.get_audio_chunk(timeout=1.0)

                trigger = ha_events.take()
                if trigger:
                    handle_trigger(*trigger)
                    chunk_time_start = time.time()
                    chunk_count = silence_count = mic_fail_count = 0
                    continue

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
                    if timer_manager.stop_ringing("wake word"):
                        audio_manager.flush()
                        wake_detector.reset()
                        continue
                    print("\n🚨 Wake Word Detected!", flush=True)
                    run_session()
                    
        except KeyboardInterrupt:
            pass
        finally:
            audio_manager.stop()
            hardware.turn_off()

if __name__ == "__main__":
    main()
