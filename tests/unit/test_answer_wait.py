"""Session end right after the user spoke: Jarvis waits for Gemini's reaction (JARVIS_ANSWER_WAIT)."""
import time
import unittest
from types import SimpleNamespace

from core.llm import GeminiLiveProvider


def provider():
    p = object.__new__(GeminiLiveProvider)  # skip __init__: no API keys, no VAD model
    p.idle_timeout, p.speech_grace, p.max_speech, p.end_grace, p.answer_wait = 15.0, 6.0, 20.0, 2.0, 8.0
    return p


def state(pause, speech, since_end=0.0, awaiting=True):
    """Jarvis finished speaking, the user waited `pause` s, spoke for `speech` s and stopped `since_end` s ago."""
    now = time.time()
    end = now - since_end
    return {'last_activity': end - speech - pause, 'last_user_speech': end, 'speaking_since': None,
            'end_requested': False, 'awaiting_answer': end if awaiting else None}


AUDIO = SimpleNamespace(is_playing=False, last_speech_time=0.0)


class AnswerWaitTest(unittest.TestCase):
    def test_long_question_after_pause_waits_for_answer(self):
        # 2026-11-25 12:49: 14 s reading pause, 7 s question, closed 0.7 s later before Gemini answered
        self.assertIsNone(provider()._close_reason(state(14, 7, since_end=0.7), AUDIO))

    def test_long_question_without_pause_waits_for_answer(self):
        self.assertIsNone(provider()._close_reason(state(1, 20, since_end=3), AUDIO))

    def test_old_behaviour_closed_these(self):
        # Without the window (Gemini already reacted silently) the same timing closes: background speech
        self.assertIsNotNone(provider()._close_reason(state(14, 7, since_end=0.7, awaiting=False), AUDIO))

    def test_closes_when_gemini_never_reacts(self):
        reason = provider()._close_reason(state(14, 7, since_end=9), AUDIO)
        self.assertIn("no reaction", reason)

    def test_short_exchange_unchanged(self):
        # Quiet for 10 s after a short question that Gemini hasn't reacted to: no close either way
        self.assertIsNone(provider()._close_reason(state(2, 2, since_end=6, awaiting=False), AUDIO))

    def test_cap_stops_endless_background_speech(self):
        # Gemini hasn't spoken for 60 s (> timeout + grace + max speech + wait): the window no longer holds
        s = state(40, 19, since_end=1)
        self.assertIsNotNone(provider()._close_reason(s, AUDIO))


if __name__ == "__main__":
    unittest.main()
