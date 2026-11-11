import math
import os

import numpy as np

# SFX are scaled so their loudest sample reaches this fraction of full scale. Speech is not touched.
SFX_PEAK = float(os.getenv("JARVIS_SFX_PEAK", "0.7"))


def normalize_peak(pcm, peak):
    """Scales 24 kHz int16 PCM so its loudest sample is `peak` (0-1) of full scale."""
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    loudest = np.abs(samples).max() if len(samples) else 0
    if loudest == 0:
        return pcm
    return np.clip(samples * (peak * 32767 / loudest), -32768, 32767).astype(np.int16).tobytes()


class SFXManager:
    def __init__(self, audio_manager, assets_dir="assets/sounds"):
        self.audio_manager = audio_manager
        self.sounds = {}
        
        # Load all PCM files into memory
        base_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), assets_dir)
        for sound_name in ["wake.pcm", "sleep.pcm", "thinking.pcm", "success.pcm", "error.pcm"]:
            path = os.path.join(base_path, sound_name)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    self.sounds[sound_name.split(".")[0]] = normalize_peak(f.read(), SFX_PEAK)
            else:
                print(f"[SFX] Warning: Sound {sound_name} not found.")

    def play(self, name):
        """Plays a sound immediately, flushing any currently playing audio."""
        if name in self.sounds:
            self.audio_manager.stop_playback()
            self.audio_manager.play_chunk(self.sounds[name], is_tts=False)

    def play_thinking(self, after_speech=False, seconds=None, fade_in=False):
        """Plays the thinking sound looped for about `seconds` (default 38). after_speech queues it behind speech that is still playing instead of cutting it off. fade_in starts it softly."""
        seconds = seconds or 38
        if "thinking" in self.sounds:
            if not after_speech:
                self.audio_manager.stop_playback()
            # Loops pushed as separate chunks so they can be cleared from the queue if the tool finishes early!
            sound = self.sounds["thinking"]
            loop_seconds = len(sound) / (24000 * 2)
            for i in range(max(1, math.ceil(seconds / loop_seconds))):
                if i == 0 and fade_in:
                    from core.audio import FADE_SECONDS, fade
                    n = int(FADE_SECONDS * 24000) * 2
                    self.audio_manager.play_chunk(fade(sound[:n], 0.0, 1.0) + sound[n:], is_tts=False)
                else:
                    self.audio_manager.play_chunk(sound, is_tts=False)
