import pyaudio
import multiprocessing
import queue

# Constants matched to openWakeWord and Gemini Live API specs
RATE = 16000
CHANNELS = 1
CHUNK = 512

def _audio_worker(audio_q):
    """
    Daemon process that continually reads from the ALSA default mic
    and shoves raw PCM data into a thread-safe queue.
    """
    import logging
    logging.basicConfig(filename='/tmp/audio_worker.log', level=logging.DEBUG)
    logging.info("[Audio Worker] Starting...")
    p = pyaudio.PyAudio()
    try:
        # Note: Because of our .asoundrc, 'input=True' automatically uses
        # the dsnoop buffered PS3 Eye camera microphone array.
        stream = p.open(format=pyaudio.paInt16,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK)
        logging.info("[Audio Worker] Stream opened successfully.")
        while True:
            try:
                # exception_on_overflow=False prevents crashes if the Pi spikes in CPU
                data = stream.read(CHUNK, exception_on_overflow=False)
                audio_q.put(data)
            except Exception as e:
                logging.error(f"[Audio Worker] Error reading stream: {e}")
                break
    except Exception as e:
        logging.error(f"[Audio Worker] Error opening stream: {e}")
    finally:
        try:
            stream.stop_stream()
            stream.close()
        except: pass
        p.terminate()
        logging.info("[Audio Worker] Exited.")

import threading
import os

def get_bonnet_card_index():
    """Dynamically finds the card index for the AIY Voice Bonnet."""
    try:
        with open("/proc/asound/cards", "r") as f:
            lines = f.readlines()
            for line in lines:
                if "aiyvoicebonnet" in line.lower():
                    return int(line.strip().split()[0])
    except Exception as e:
        print(f"[Audio] Error detecting sound card: {e}")
    return None

def init_audio_settings():
    """Sets ALSA hardware settings dynamically for the AIY Voice Bonnet."""
    card_idx = get_bonnet_card_index()
    if card_idx is None:
        print("[Audio] AIY Voice Bonnet not found in /proc/asound/cards")
        return False

    print(f"[Audio] Setting Optimized Hardware Gain for Card {card_idx}...")
    try:
        os.system(f"amixer -c {card_idx} sset 'Mono ADC' 100% > /dev/null 2>&1")
        os.system(f"amixer -c {card_idx} sset 'ADC' 100% > /dev/null 2>&1")
        os.system(f"amixer -c {card_idx} sset 'Mono ADC Boost' 3 > /dev/null 2>&1")
        os.system(f"amixer -c {card_idx} sset 'ADC Boost' 1 > /dev/null 2>&1")
        os.system(f"amixer -c {card_idx} sset 'Speaker' 45% > /dev/null 2>&1")
        os.system(f"amixer -c {card_idx} sset 'Speaker Channel' unmute > /dev/null 2>&1")
        return True
    except Exception as e:
        print(f"[Audio] Error setting ALSA controls: {e}")
        return False

class AudioManager:
    def __init__(self):
        self.audio_queue = multiprocessing.Queue()
        self.audio_proc = None
        self.p = pyaudio.PyAudio()
        
        self.out_stream = self.p.open(
            format=pyaudio.paInt16,
            channels=CHANNELS,
            rate=24000,
            output=True
        )
        
        self.is_playing = False
        self.last_play_time = 0.0
        self.last_speech_time = 0.0  # end of the last spoken (TTS) chunk; SFX don't count
        self.playback_queue = queue.Queue()
        # Start background playback thread
        self.playback_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self.playback_thread.start()

    def start_mic(self):
        """Spawns the microphone reading process."""
        self.audio_proc = multiprocessing.Process(target=_audio_worker, args=(self.audio_queue,), daemon=True)
        self.audio_proc.start()

    def restart_mic(self):
        """Restarts the microphone reading process (Watchdog)."""
        import multiprocessing
        if self.audio_proc and self.audio_proc.is_alive():
            self.audio_proc.terminate()
            self.audio_proc.join(timeout=1.0)
            
        self.flush()
        # Create a brand new queue, as terminating a process while it writes to the queue corrupts its locks
        self.audio_queue = multiprocessing.Queue()
        self.start_mic()

    def _playback_worker(self):
        import time
        while True:
            item = self.playback_queue.get()
            if item is None:
                break
            
            chunk, is_tts = item
            
            if is_tts:
                self.is_playing = True
                
            try:
                self.out_stream.write(chunk)
            except Exception as e:
                print(f"[Playback] Error: {e}")
            if is_tts:
                self.last_speech_time = time.time()

            if self.playback_queue.empty():
                self.last_play_time = time.time()
                self.is_playing = False
                # REMOVED self.flush() because it destroys buffered user speech!

    def set_wake_detector(self, wake_detector):
        self.wake_detector = wake_detector

    def get_audio_chunk(self, timeout=3):
        """Retrieves a chunk from the microphone queue."""
        import time
        if self.is_playing:
            # Drop mic chunks to prevent the AI from hearing itself (Half-Duplex)
            try:
                while not self.audio_queue.empty():
                    self.audio_queue.get_nowait()
            except queue.Empty:
                pass
            return None
            
        try:
            if timeout > 0:
                return self.audio_queue.get(timeout=timeout)
            else:
                return self.audio_queue.get_nowait()
        except queue.Empty:
            return None

    def flush(self):
        """Drains all pending audio chunks from the queue to prevent double-triggering or echo."""
        while not self.audio_queue.empty():
            try:
                self.audio_queue.get_nowait()
            except queue.Empty:
                break

    def play_chunk(self, pcm_data, is_tts=True):
        """Plays raw PCM data through the speaker."""
        self.playback_queue.put((pcm_data, is_tts))

    def stop_playback(self):
        """Immediately stops current playback."""
        with self.playback_queue.mutex:
            self.playback_queue.queue.clear()
            self.is_playing = False

    def stop(self):
        """Cleanly terminates the audio sub-process and PyAudio streams."""
        if self.audio_proc and self.audio_proc.is_alive():
            self.audio_proc.terminate()
            self.audio_proc.join(timeout=1.0)
            
        self.playback_queue.put(None)
        if hasattr(self, 'playback_thread') and self.playback_thread.is_alive():
            self.playback_thread.join(timeout=2.0)
            
        self.out_stream.stop_stream()
        self.out_stream.close()
        self.p.terminate()
