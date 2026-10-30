import time
import sys
import numpy as np
from core.audio import AudioManager
from core.hardware import BonnetHardware
from core.wake import WakeWordDetector

def main():
    print("========================================")
    print(" JARVIS V3 - PHASE 2: WAKE WORD & LEDS")
    print("========================================")
    
    print("Initializing Hardware LEDs...")
    hw = BonnetHardware()
    hw.turn_off()
    
    print("Loading openWakeWord model (this takes a few seconds)...")
    detector = WakeWordDetector("hey_jarvis")
    
    print("Initializing Audio Manager...")
    am = AudioManager()
    am.start_mic()
    
    print("\n[READY] Listening for 'Hey Jarvis'...")
    print("Say the wake word to trigger the LEDs. Press Ctrl+C to stop.\n")
    
    try:
        while True:
            chunk = am.get_audio_chunk(timeout=1.0)
            
            if chunk:
                # Feed the raw audio straight into the Wake Word detector
                detected = detector.process_chunk(chunk)
                
                if detected:
                    print("\n>>>>> WAKE WORD DETECTED! <<<<<\n")
                    
                    # Flash the AIY Bonnet LEDs Blue
                    hw.set_color("blue", blink=True)
                    
                    # Play a success beep through the speaker (16kHz sine wave)
                    t = np.linspace(0, 0.2, int(16000 * 0.2), endpoint=False)
                    beep = (np.sin(2 * np.pi * 880 * t) * 10000).astype(np.int16)
                    am.play_chunk(beep.tobytes())
                    
                    # Wait a second to enjoy the blink
                    time.sleep(1.0)
                    
                    # Turn off LEDs and go back to listening
                    hw.turn_off()
                    
                    # Flush the audio queue to throw away the end of your sentence / the beep echo
                    am.flush()
                    print("\n[READY] Listening again...")
                    
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        hw.turn_off()
        am.stop()
        print("Cleaned up resources. Exiting.")
        sys.exit(0)

if __name__ == "__main__":
    main()
