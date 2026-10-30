import os
import sys
import time
from core.wake import WakeWordDetector

def main():
    print("========================================")
    print(" JARVIS V3 - WAKE WORD HEADLESS TEST")
    print("========================================")
    
    pcm_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "hey_jarvis.pcm")
    if not os.path.exists(pcm_path):
        print(f"Error: Could not find {pcm_path}")
        sys.exit(1)
        
    print("[Boot] Loading Wake Word Detector...")
    try:
        detector = WakeWordDetector(model_name="hey_jarvis")
    except Exception as e:
        print(f"Failed to load detector: {e}")
        sys.exit(1)
        
    print(f"\n[Test] Reading raw PCM audio from {pcm_path}...")
    
    with open(pcm_path, "rb") as f:
        pcm_data = f.read()
        
    print(f"[Test] Loaded {len(pcm_data)} bytes of audio.")
    
    # Simulate streaming by sending audio in small chunks 
    # openwakeword usually processes chunks of 1280 bytes (80ms at 16kHz)
    CHUNK_SIZE = 1280 
    
    detected = False
    
    for i in range(0, len(pcm_data), CHUNK_SIZE):
        chunk = pcm_data[i:i+CHUNK_SIZE]
        
        # Pad the last chunk with zeros if it's too small
        if len(chunk) < CHUNK_SIZE:
            chunk += b'\x00' * (CHUNK_SIZE - len(chunk))
            
        if detector.process_chunk(chunk):
            print("\n🚨 WAKE WORD DETECTED IN AUDIO STREAM! 🚨")
            print(f"Detection triggered at byte {i} (approx {i/32000:.2f} seconds).")
            detected = True
            break
            
    if not detected:
        print("\n❌ Wake word NOT detected in the audio file.")
        sys.exit(1)
        
    print("\n✅ Test Passed: Headless wake word processing works perfectly.")

if __name__ == "__main__":
    main()
