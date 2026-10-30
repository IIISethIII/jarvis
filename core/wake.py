import numpy as np
import openwakeword
from openwakeword.model import Model

import time

class WakeWordDetector:
    def __init__(self, model_name="hey_jarvis"):
        self.last_trigger_time = 0
        # Get the absolute paths to all pre-trained models bundled in the library
        pretrained_paths = openwakeword.get_pretrained_model_paths()
        
        # Find the path that matches our requested model name
        target_path = next((p for p in pretrained_paths if model_name in p), None)
        
        if not target_path:
            print(f"[WakeWord] Model '{model_name}' not found. Falling back to 'hey_jarvis'.")
            target_path = next((p for p in pretrained_paths if "hey_jarvis" in p), None)

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
            if score > 0.5: # 0.5 confidence threshold
                # Reset the model state so it doesn't rapidly double-trigger
                self.model.reset()
                
                import time
                if time.time() - self.last_trigger_time > 2.0:
                    self.last_trigger_time = time.time()
                    return True
                else:
                    return False
                
        return False
