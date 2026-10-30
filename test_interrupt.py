import os
import sys
import time
import queue
from core.audio import AudioManager
from core.wake import WakeWordDetector

def main():
    print("========================================")
    print(" JARVIS V3 - WAKE WORD INTERRUPT TEST")
    print("========================================")
    
    pcm_path = "/home/pi/jarvis_v3/hey_jarvis.pcm"
    if not os.path.exists(pcm_path):
        print(f"Error: Could not find {pcm_path}")
        sys.exit(1)
        
    print("[Boot] Loading Wake Word Detector...")
    detector = WakeWordDetector(model_name="hey_jarvis")
    
    print("[Boot] Initializing AudioManager...")
    audio_manager = AudioManager()
    audio_manager.set_wake_detector(detector)
    
    # We don't call start_mic() because we want to push data manually
    print("\n[Test] Reading raw PCM audio from test file...")
    with open(pcm_path, "rb") as f:
        pcm_data = f.read()
        
    print(f"[Test] Loaded {len(pcm_data)} bytes of 'Hey Jarvis' audio.")
    
    print("\n[Action] AI starts speaking (putting 10 seconds of dummy audio into speaker queue)...")
    # 24kHz, 16-bit mono = 48,000 bytes per second.
    # 10 seconds = 480,000 bytes of zeros
    dummy_audio = b'\x00' * 480000 
    audio_manager.play_chunk(dummy_audio)
    
    time.sleep(0.5) # Let the playback thread start pulling
    
    if not audio_manager.is_playing:
        print("[Error] Playback did not start.")
        audio_manager.stop()
        sys.exit(1)
        
    print("[Verify] Speaker is playing: True. Queue size:", audio_manager.playback_queue.qsize())
    
    print("\n[Action] Simulating user interrupting with 'Hey Jarvis' into the mic...")
    # Push the PCM chunks into the mic queue manually
    CHUNK_SIZE = 512
    for i in range(0, len(pcm_data), CHUNK_SIZE):
        chunk = pcm_data[i:i+CHUNK_SIZE]
        if len(chunk) < CHUNK_SIZE:
            chunk += b'\x00' * (CHUNK_SIZE - len(chunk))
        audio_manager.audio_queue.put(chunk)
        
    print("[Verify] Mic queue now contains chunks.")
    
    print("\n[Action] Calling get_audio_chunk() as if we are the LLM loop...")
    # This should drop the chunks, pass them to wake_detector, and trigger the interrupt
    result = audio_manager.get_audio_chunk(timeout=1)
    
    print(f"[Verify] get_audio_chunk() returned: {result} (Expected: None)")
    
    time.sleep(0.5) # Give the flush a moment
    
    print(f"[Verify] Speaker is playing: {audio_manager.is_playing} (Expected: False or emptying soon)")
    print(f"[Verify] Speaker queue size: {audio_manager.playback_queue.qsize()} (Expected: 0)")
    
    if audio_manager.playback_queue.qsize() == 0:
        print("\n✅ Test Passed: The wake word successfully interrupted playback!")
    else:
        print("\n❌ Test Failed: The playback queue was not flushed.")
        
    audio_manager.stop()

if __name__ == "__main__":
    main()
