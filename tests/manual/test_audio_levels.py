import pyaudio
import numpy as np
import wave
import time
import argparse

RATE = 16000
CHANNELS = 1
CHUNK = 512

def test_audio(gain=1.0, record_seconds=5, output_file="test_mic.wav"):
    p = pyaudio.PyAudio()
    
    # Check which device it's defaulting to
    try:
        default_device = p.get_default_input_device_info()
        print(f"Using Microphone: {default_device.get('name')} (Index: {default_device.get('index')})")
    except Exception as e:
        print("Warning: Could not determine default device name.")

    print(f"Opening microphone stream (Rate: {RATE}, Channels: {CHANNELS})...")
    stream = p.open(format=pyaudio.paInt16,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK)

    print(f"Recording for {record_seconds} seconds with gain {gain}x...")
    frames = []
    
    start_time = time.time()
    while time.time() - start_time < record_seconds:
        try:
            data = stream.read(CHUNK, exception_on_overflow=False)
            
            # Apply digital gain (as in llm.py)
            audio_array = np.frombuffer(data, dtype=np.int16)
            boosted_array = np.clip(audio_array.astype(np.float32) * gain, -32768, 32767).astype(np.int16)
            
            # Calculate volume (RMS)
            rms = np.sqrt(np.mean(np.square(boosted_array.astype(np.float32))))
            rms_normalized = rms / 32768.0
            
            # Print a visual volume bar
            bar_length = int(rms_normalized * 100) # scale to 100 max
            bar = '#' * bar_length
            print(f"Volume: {rms_normalized:.4f} | {bar}")
            
            frames.append(boosted_array.tobytes())
            
        except Exception as e:
            print(f"Error reading stream: {e}")
            break

    print("Finished recording.")
    
    stream.stop_stream()
    stream.close()
    p.terminate()
    
    print(f"Saving to {output_file}...")
    wf = wave.open(output_file, 'wb')
    wf.setnchannels(CHANNELS)
    wf.setsampwidth(p.get_sample_size(pyaudio.paInt16))
    wf.setframerate(RATE)
    wf.writeframes(b''.join(frames))
    wf.close()
    print(f"Done. Please listen to {output_file} to hear exactly what Gemini hears.")
    
    if gain > 1.0:
        print("\nNote on Gain: If you hear a lot of background hissing or white noise,")
        print("the digital gain is amplifying the noise floor of the PlayStation Eye.")
        print("This noise can confuse the Gemini Live API's Voice Activity Detection.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test PlayStation Eye Audio levels for Gemini")
    parser.add_argument("--gain", type=float, default=4.0, help="Digital gain multiplier (e.g., 1.0, 2.0, 4.0)")
    parser.add_argument("--time", type=int, default=5, help="Record time in seconds")
    parser.add_argument("--out", type=str, default="test_mic.wav", help="Output WAV file name")
    
    args = parser.parse_args()
    test_audio(gain=args.gain, record_seconds=args.time, output_file=args.out)
