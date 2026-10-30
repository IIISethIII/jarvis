import time
import sys
from core.audio import AudioManager

def main():
    print("========================================")
    print(" JARVIS V3 - PHASE 1: AUDIO ALSA TEST")
    print("========================================")
    print("Initializing Audio Manager...")
    
    am = AudioManager()
    print("Spawning microphone daemon process...")
    am.start_mic()
    
    print("\n[READY] Speak into the microphone!")
    print("You should hear an immediate echo of your voice from the AIY Bonnet.")
    print("Press Ctrl+C to stop the test.\n")
    
    try:
        while True:
            # Pull a 512-frame chunk from the PS3 Eye mic queue
            chunk = am.get_audio_chunk(timeout=1.0)
            
            if chunk:
                am.play_chunk(chunk)
                print(".", end="", flush=True)
            else:
                print("[Warning] Watchdog: No audio received from queue in the last second.")
                
    except KeyboardInterrupt:
        print("\nStopping Audio Manager...")
    finally:
        am.stop()
        print("Cleaned up audio resources. Exiting.")
        sys.exit(0)

if __name__ == "__main__":
    main()
