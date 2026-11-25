"""Local voice activity detection with Silero (the model ships with openWakeWord, so no extra dependency).

Logging only for now: during a session it prints its own speech segments next to Gemini's server VAD and
warns when Gemini reports speech that the mic doesn't contain. The logs calibrate the threshold before it
drives the session timer. Inference runs in a worker thread (onnxruntime releases the GIL), so the async
audio loop never waits on it, and only during sessions, not next to the wake word.
"""
import os
import queue
import threading
import time
from collections import deque

import numpy as np

ENABLED = os.getenv("JARVIS_LOCAL_VAD", "1") != "0"
THRESHOLD = float(os.getenv("JARVIS_VAD_THRESHOLD", "0.5"))
RATE = 16000
FRAME = 512  # samples per inference: 32 ms, one mic chunk
FRAME_S = FRAME / RATE
HANGOVER_S = 0.8  # a local speech segment ends after this much silence, so short pauses stay inside it
MIN_SEGMENT_S = 0.25  # shorter blips (a cough, a click) are not logged
HISTORY_S = 120  # (time, probability) pairs kept to compare with Gemini's segments


class LocalVAD:
    def __init__(self, model=None):
        """model: anything with predict(int16 array, frame_size) -> speech probability. Default: Silero."""
        self.model = model
        if self.model is None and ENABLED:
            try:
                from openwakeword.vad import VAD
                self.model = VAD()
            except Exception as e:
                print(f"[VAD] Local voice detection off: {e}", flush=True)
        self.available = self.model is not None
        self._lock = threading.Lock()
        self._queue = queue.Queue(maxsize=100)
        self._reset(time.time())
        if self.available:
            threading.Thread(target=self._worker, daemon=True, name="local-vad").start()
            if model is None:
                print(f"[VAD] Local voice detection on (Silero, threshold {THRESHOLD}, logging only)", flush=True)

    # --- called from the session (event loop thread) ---

    def start_session(self):
        self._put(("start", time.time(), None))

    def end_session(self):
        self._put(("end", time.time(), None))

    def feed(self, pcm):
        """Queues int16 mic PCM (whole 512-sample chunks) without blocking. Frames are dropped if inference lags."""
        if not self._put(("audio", time.time(), pcm)):
            with self._lock:
                self.dropped += len(pcm) // (2 * FRAME)

    def window(self, since, until):
        """(share of speech frames, max probability, seconds from the last speech frame to `until`) in [since, until].

        None when no frame was processed in the window (mic dropped during playback, or VAD off).
        """
        with self._lock:
            probs = [p for t, p in self.history if since <= t <= until]
            last = max((t for t, p in self.history if since <= t <= until and p >= THRESHOLD), default=None)
        if not probs:
            return None
        share = sum(p >= THRESHOLD for p in probs) / len(probs)
        return share, max(probs), (until - last) if last is not None else None

    def silence(self, since):
        """Seconds without local speech, counted from `since` or the last speech frame after it,
        up to the newest processed frame. None if no frame after `since` was processed yet."""
        with self._lock:
            if self.latest is None or self.latest < since:
                return None
            return self.latest - max(since, self.last_speech)

    # --- worker thread ---

    def _put(self, item):
        if not self.available:
            return True
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            return False

    def _worker(self):
        while True:
            kind, t, pcm = self._queue.get()
            if kind == "start":
                with self._lock:
                    self._reset(t)
                if hasattr(self.model, "reset_states"):
                    self.model.reset_states()  # Silero is recurrent: don't carry state over from the last session
            elif kind == "end":
                self._finish()
            else:
                x = np.frombuffer(pcm, dtype=np.int16)
                n = len(x) // FRAME
                for i in range(n):
                    began = time.perf_counter()
                    try:
                        prob = float(self.model.predict(x[i * FRAME:(i + 1) * FRAME], frame_size=FRAME))
                    except Exception as e:
                        print(f"[VAD] Inference failed, local voice detection off: {e}", flush=True)
                        self.available = False
                        return
                    self._frame(t - (n - 1 - i) * FRAME_S, prob, (time.perf_counter() - began) * 1000)

    def _reset(self, t0):
        self.t0 = t0
        self.history = deque(maxlen=int(HISTORY_S / FRAME_S))
        self.latest = None  # time of the newest processed frame
        self.last_speech = 0.0
        self.seg_start = None
        self.seg_max = 0.0
        self.frames = self.speech_frames = self.dropped = 0
        self.ms_total = self.ms_max = 0.0

    def _frame(self, t, prob, ms=0.0):
        """Processes one frame's probability: history, stats and local speech segments."""
        with self._lock:
            self.history.append((t, prob))
            self.latest = t
            self.frames += 1
            self.ms_total += ms
            self.ms_max = max(self.ms_max, ms)
            if prob >= THRESHOLD:
                self.speech_frames += 1
                self.last_speech = t
                if self.seg_start is None:
                    self.seg_start, self.seg_max = t - FRAME_S, 0.0
                self.seg_max = max(self.seg_max, prob)
            elif self.seg_start is not None and t - self.last_speech > HANGOVER_S:
                self._end_segment()

    def _end_segment(self):
        duration = self.last_speech - self.seg_start
        if duration >= MIN_SEGMENT_S:
            print(f"[VAD] Local speech {self.seg_start - self.t0:.1f}-{self.last_speech - self.t0:.1f}s "
                  f"({duration:.1f}s, max {self.seg_max:.2f})", flush=True)
        self.seg_start = None

    def _finish(self):
        with self._lock:
            if self.seg_start is not None:
                self._end_segment()
            if self.frames:
                print(f"[VAD] Session: {self.frames} frames ({self.frames * FRAME_S:.0f}s), "
                      f"speech {self.speech_frames / self.frames:.0%}, inference {self.ms_total / self.frames:.1f} ms/frame "
                      f"(max {self.ms_max:.0f}, budget {FRAME_S * 1000:.0f}), dropped {self.dropped}", flush=True)
