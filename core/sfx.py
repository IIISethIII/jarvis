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

    def play_thinking(self):
        """Plays the thinking sound looped for up to 10 seconds."""
        if "thinking" in self.sounds:
            self.audio_manager.stop_playback()
            # 10 loops of the thinking sound, pushed as separate chunks 
            # so they can be cleared from the queue if the tool finishes early!
            for _ in range(10):
                self.audio_manager.play_chunk(self.sounds["thinking"], is_tts=False)
