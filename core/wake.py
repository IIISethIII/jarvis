import os
import numpy as np
import openwakeword
from openwakeword.model import Model

from core import wakewords

import time

THRESHOLD = float(os.getenv("JARVIS_WAKE_THRESHOLD", "0.5"))
# Scores between this and the threshold are logged as near misses, to tune the threshold from real data
NEAR_MISS = 0.6 * THRESHOLD

class WakeWordDetector:
    def __init__(self, model_name=None):
        model_name = model_name or os.getenv("JARVIS_WAKE_MODEL", "hey_jarvis")
        self.last_trigger_time = 0
        self.near_miss_peak = 0.0
        # Your own model in data/wakewords/ or one that comes with openWakeWord (core/wakewords.py)
        target_path = wakewords.find(model_name)
        if not target_path:
            print(f"[WakeWord] Model '{model_name}' not found. Falling back to 'hey_jarvis'.", flush=True)
            target_path = wakewords.find("hey_jarvis") or next(
                (p for p in openwakeword.get_pretrained_model_paths() if "hey_jarvis" in p), None)
        print(f"[WakeWord] Model: {target_path}", flush=True)

        try:
            # The library infers the framework from the file extension in 0.4.0
            self.model = Model(wakeword_model_paths=[target_path])
            self.target_model = model_name
            
            # PERFORMANCE PATCH: Reduce openwakeword's massive internal buffer
            # openwakeword stores 10s (160000 elements) but only ever uses the last ~2000!
            # list(deque) on 160000 elements kills the Pi's CPU. Shrink it to 4000.
            from collections import deque
            if hasattr(self.model, 'preprocessor') and hasattr(self.model.preprocessor, 'raw_data_buffer'):
                old_data = self.model.preprocessor.raw_data_buffer
                self.model.preprocessor.raw_data_buffer = deque(old_data, maxlen=4000)
                
        except Exception as e:
            print(f"[WakeWord] Fatal error loading model: {e}")
            raise
            
    def reset(self):
        """Clears the internal model state and resets the trigger timer."""
        self.model.reset()
        self.last_trigger_time = time.time()
        self.near_miss_peak = 0.0

    def process_chunk(self, pcm_data):
        """
        Takes raw 16kHz PCM bytes from the microphone queue, converts it,
        and feeds it to the openwakeword model.
        Returns True if the wake word was detected in this chunk.
        """
        if not pcm_data:
            return False
            
        # Convert raw bytes to int16 numpy array
        audio_data = np.frombuffer(pcm_data, dtype=np.int16)
        
        # Feed to model
        prediction = self.model.predict(audio_data)
        
        # Check scores for any loaded model
        for mdl, score in prediction.items():
            if score > THRESHOLD:
                # Reset the model state so it doesn't rapidly double-trigger
                self.model.reset()
                self.near_miss_peak = 0.0

                if time.time() - self.last_trigger_time > 2.0:
                    self.last_trigger_time = time.time()
                    print(f"[WakeWord] Detected (score {score:.2f})", flush=True)
                    return True
                else:
                    return False
            if score >= NEAR_MISS:
                self.near_miss_peak = max(self.near_miss_peak, score)
            elif self.near_miss_peak:
                print(f"[WakeWord] Near miss (peak score {self.near_miss_peak:.2f}, threshold {THRESHOLD})", flush=True)
                self.near_miss_peak = 0.0

        return False
