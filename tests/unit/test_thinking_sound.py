import queue
import time
import unittest
from unittest import mock

import numpy as np

from core import audio
from core.llm import GeminiLiveProvider


def pcm(value, seconds):
    return np.full(int(seconds * 24000), value, dtype=np.int16).tobytes()


class FadeTest(unittest.TestCase):
    def test_fade_out_keeps_short_faded_tail(self):
        am = object.__new__(audio.AudioManager)  # skip __init__: no audio devices
        am.playback_queue, am.is_playing = queue.Queue(), False
        am.play_chunk(pcm(10000, 1.0), is_tts=False)
        am.fade_out()
        tail = b"".join(chunk for chunk, _ in am.playback_queue.queue)
        samples = np.frombuffer(tail, dtype=np.int16)
        self.assertEqual(len(samples), int(audio.FADE_SECONDS * 24000))
        self.assertGreater(samples[0], 9000)
        self.assertLess(abs(samples[-1]), 100)


class DelayedThinkingTest(unittest.TestCase):
    def setUp(self):
        self.p = object.__new__(GeminiLiveProvider)
        self.p.thinking_delay = 0.8
        self.sfx = mock.Mock()

    def test_not_before_delay(self):
        state = {'thinking_at': time.time() + 0.5, 'speaking_since': None, 'is_thinking': False}
        self.p._start_thinking_if_due(state, self.sfx)
        self.sfx.play_thinking.assert_not_called()

    def test_starts_when_due(self):
        state = {'thinking_at': time.time() - 0.1, 'speaking_since': None, 'is_thinking': False}
        self.p._start_thinking_if_due(state, self.sfx)
        self.sfx.play_thinking.assert_called_once()
        self.assertTrue(state['is_thinking'])
        self.assertIsNone(state['thinking_at'])

    def test_waits_while_user_speaks(self):
        state = {'thinking_at': time.time() - 0.1, 'speaking_since': time.time(), 'is_thinking': False}
        self.p._start_thinking_if_due(state, self.sfx)
        self.sfx.play_thinking.assert_not_called()


if __name__ == "__main__":
    unittest.main()
