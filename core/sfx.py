import math
import os

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
                    self.sounds[sound_name.split(".")[0]] = f.read()
            else:
                print(f"[SFX] Warning: Sound {sound_name} not found.")

    def play(self, name):
        """Plays a sound immediately, flushing any currently playing audio."""
        if name in self.sounds:
            self.audio_manager.stop_playback()
            self.audio_manager.play_chunk(self.sounds[name], is_tts=False)

    def play_thinking(self, after_speech=False, seconds=None):
        """Plays the thinking sound looped for about `seconds` (default 38). after_speech queues it behind speech that is still playing instead of cutting it off."""
        seconds = seconds or 38
        if "thinking" in self.sounds:
            if not after_speech:
                self.audio_manager.stop_playback()
            # Loops pushed as separate chunks so they can be cleared from the queue if the tool finishes early!
            loop_seconds = len(self.sounds["thinking"]) / (24000 * 2)
            for _ in range(max(1, math.ceil(seconds / loop_seconds))):
                self.audio_manager.play_chunk(self.sounds["thinking"], is_tts=False)
