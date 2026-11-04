import time
import unittest
from types import SimpleNamespace

from core.llm import GeminiLiveProvider


def provider():
    p = object.__new__(GeminiLiveProvider)  # skip __init__: no API keys needed
    p.idle_timeout, p.speech_grace, p.max_speech, p.end_grace = 15.0, 6.0, 20.0, 2.0
    return p


def state(quiet_for, end_requested):
    now = time.time()
    return {'last_activity': now - quiet_for, 'last_user_speech': 0.0, 'speaking_since': None,
            'end_requested': end_requested}


AUDIO = SimpleNamespace(is_playing=False, last_speech_time=0.0)


class EndGraceTest(unittest.TestCase):
    def test_waits_for_grace(self):
        self.assertIsNone(provider()._close_reason(state(1.0, True), AUDIO))

    def test_closes_after_grace(self):
        self.assertIn("end_conversation", provider()._close_reason(state(2.5, True), AUDIO))

    def test_without_request_uses_idle_timeout(self):
        self.assertIsNone(provider()._close_reason(state(2.5, False), AUDIO))

    def test_user_speaking_holds_session(self):
        s = state(5.0, True)
        s['speaking_since'] = time.time() - 1
        self.assertIsNone(provider()._close_reason(s, AUDIO))


if __name__ == "__main__":
    unittest.main()
