"""Local VAD bookkeeping (core/vad.py) with made-up probabilities, no Silero model needed."""
import contextlib
import io
import unittest

from core import vad


class FakeModel:
    def predict(self, x, frame_size):
        return 0.0


def run(probs, t0=1000.0):
    """Feeds one probability per 32 ms frame starting at t0, returns the VAD and its log."""
    v = vad.LocalVAD(model=FakeModel())
    v._reset(t0)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for i, p in enumerate(probs):
            v._frame(t0 + (i + 1) * vad.FRAME_S, p)
        v._finish()
    return v, out.getvalue()


SECOND = round(1 / vad.FRAME_S)  # frames per second (31)


class LocalVADTest(unittest.TestCase):
    def test_short_pause_stays_one_segment(self):
        _, log = run([0.9] * SECOND + [0.1] * (SECOND // 2) + [0.9] * SECOND + [0.0] * SECOND)
        self.assertEqual(log.count("Local speech"), 1)

    def test_long_pause_splits_segments(self):
        _, log = run([0.9] * SECOND + [0.1] * (2 * SECOND) + [0.9] * SECOND + [0.0] * SECOND)
        self.assertEqual(log.count("Local speech"), 2)

    def test_blip_not_logged(self):
        _, log = run([0.0] * SECOND + [0.9] * 3 + [0.0] * (2 * SECOND))
        self.assertNotIn("Local speech", log)
        self.assertIn("[VAD] Session:", log)

    def test_window_and_silence(self):
        t0 = 1000.0
        v, _ = run([0.9] * SECOND + [0.0] * (3 * SECOND), t0)
        share, peak, gap = v.window(t0, t0 + 4)
        self.assertAlmostEqual(share, 0.25, places=2)
        self.assertEqual(peak, 0.9)
        self.assertAlmostEqual(gap, 3.0, delta=0.1)
        self.assertAlmostEqual(v.silence(t0 + 0.5), 3.0, delta=0.1)  # speech went on until 1 s
        self.assertAlmostEqual(v.silence(t0 + 2.0), 2.0, delta=0.1)  # counted from `since`
        self.assertIsNone(v.silence(t0 + 10))  # nothing processed after that yet

    def test_no_frames_in_window(self):
        v, _ = run([0.9] * SECOND)
        self.assertIsNone(v.window(2000.0, 2001.0))


if __name__ == "__main__":
    unittest.main()
