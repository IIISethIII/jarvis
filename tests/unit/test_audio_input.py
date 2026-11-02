"""Mic input: high-pass filter against mains hum, wake word near-miss logging.

Needs the runtime dependencies (pyaudio, openwakeword), e.g. on the Pi:
    venv/bin/python -m unittest discover tests/unit
"""
import contextlib
import io
import unittest

import numpy as np

from core.audio import CHUNK, RATE, HighPassFilter
from core.wake import NEAR_MISS, THRESHOLD, WakeWordDetector


def sine(freq, seconds=2.0, amplitude=5000.0):
    t = np.arange(int(RATE * seconds)) / RATE
    return amplitude * np.sin(2 * np.pi * freq * t)


def run_filter(signal, cutoff=150):
    """Feeds the signal through the filter in mic-sized chunks, like the audio worker does."""
    hp = HighPassFilter(cutoff)
    pcm = np.rint(signal).astype(np.int16)
    out = [hp.process(pcm[i:i + CHUNK].tobytes()) for i in range(0, len(pcm), CHUNK)]
    return np.frombuffer(b"".join(out), dtype=np.int16).astype(float)


def gain_db(signal, filtered):
    steady = slice(RATE // 2, None)  # skip the filter's warm-up
    return 20 * np.log10(filtered[steady].std() / signal[steady].std())


class HighPassFilterTest(unittest.TestCase):
    def test_removes_mains_hum(self):
        for freq in (50, 60, 100):
            with self.subTest(freq=freq):
                self.assertLess(gain_db(sine(freq), run_filter(sine(freq))), -40)

    def test_keeps_speech_band(self):
        for freq in (250, 1000, 4000):
            with self.subTest(freq=freq):
                self.assertAlmostEqual(gain_db(sine(freq), run_filter(sine(freq))), 0, delta=0.5)

    def test_chunks_join_without_artifacts(self):
        # Block-wise filtering must equal filtering the whole signal at once (delayed by the FIR's group delay).
        # The square wave overshoots full scale after filtering: it has to clip, not wrap around.
        signal = np.sign(sine(1000)) * 32767 + sine(50)
        signal = np.clip(signal, -32768, 32767)
        hp = HighPassFilter(150)
        expected = np.clip(np.rint(np.convolve(np.rint(signal), hp.kernel)[:len(signal)]), -32768, 32767)
        filtered = run_filter(signal)
        self.assertEqual(len(filtered), len(signal))
        np.testing.assert_allclose(filtered, expected, atol=1)


class FakeModel:
    def __init__(self, scores):
        self.scores = list(scores)

    def predict(self, audio):
        return {"hey_jarvis": self.scores.pop(0)}

    def reset(self):
        pass


def detector(scores):
    wd = WakeWordDetector.__new__(WakeWordDetector)  # skip loading the real model
    wd.model = FakeModel(scores)
    wd.last_trigger_time = 0
    wd.near_miss_peak = 0.0
    return wd


def feed(wd, n):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        results = [wd.process_chunk(b"\0\0" * CHUNK) for _ in range(n)]
    return results, out.getvalue()


class WakeNearMissTest(unittest.TestCase):
    def test_near_miss_logs_peak_once(self):
        low, mid = NEAR_MISS / 2, (NEAR_MISS + THRESHOLD) / 2
        wd = detector([low, mid, mid + 0.01, mid, low, low])
        results, log = feed(wd, 6)
        self.assertEqual(results, [False] * 6)
        self.assertEqual(log.count("Near miss"), 1)
        self.assertIn(f"peak score {mid + 0.01:.2f}", log)

    def test_detection_clears_near_miss(self):
        mid = (NEAR_MISS + THRESHOLD) / 2
        wd = detector([mid, THRESHOLD + 0.1, 0.0])
        results, log = feed(wd, 3)
        self.assertEqual(results, [False, True, False])
        self.assertIn("Detected", log)
        self.assertNotIn("Near miss", log)


if __name__ == "__main__":
    unittest.main()
