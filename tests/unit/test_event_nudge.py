"""An event session asks once more when nobody answers (core/llm.py _nudge_due, _close_reason).

Gemini Live only speaks on input: without the nudge, an unanswered "Who are you?" was followed by silence and the
normal idle timeout. Needs the runtime dependencies:  venv/bin/python -m unittest tests.unit.test_event_nudge
"""
import os
import time
import unittest
from types import SimpleNamespace

from core import events
from core.llm import GeminiLiveProvider


def provider():
    p = object.__new__(GeminiLiveProvider)  # skip __init__: no API keys needed
    p.idle_timeout, p.speech_grace, p.max_speech, p.end_grace, p.answer_wait = 15.0, 6.0, 20.0, 0.0, 8.0
    return p


def state(quiet_for, **extra):
    os.environ.pop("JARVIS_OWNER_CODEWORD", None)
    session = events.EventSession(events.Event({"prompt": "Ask who is there.", "owner": "Paul"}))
    return {'last_activity': time.time() - quiet_for, 'last_user_speech': 0.0, 'speaking_since': None,
            'end_requested': False, 'event_session': session, **extra}


AUDIO = SimpleNamespace(is_playing=False, last_speech_time=0.0)


class NudgeTest(unittest.TestCase):
    def test_waits_past_the_idle_timeout_for_the_nudge(self):
        p = provider()
        s = state(20.0)
        self.assertIsNone(p._close_reason(s, AUDIO), "no close before the follow-up question")
        self.assertFalse(p._nudge_due(s, AUDIO))

    def test_nudge_after_30_seconds_once(self):
        p = provider()
        s = state(events.NO_ANSWER_NUDGE_S + 1)
        self.assertTrue(p._nudge_due(s, AUDIO))
        s['nudged'] = True
        self.assertFalse(p._nudge_due(s, AUDIO), "only once")
        s['last_activity'] = time.time() - 16  # the follow-up was 16 s ago: normal timeout
        self.assertIn("without activity", p._close_reason(s, AUDIO))

    def test_no_nudge_once_someone_spoke(self):
        p = provider()
        s = state(40.0, last_user_speech=time.time() - 40)
        self.assertFalse(p._nudge_due(s, AUDIO))
        self.assertIsNotNone(p._close_reason(s, AUDIO))

    def test_not_while_speaking_or_in_normal_sessions(self):
        p = provider()
        self.assertFalse(p._nudge_due(state(40.0), SimpleNamespace(is_playing=True, last_speech_time=0.0)))
        s = state(40.0, event_session=None)
        self.assertFalse(p._nudge_due(s, AUDIO))
        self.assertIsNotNone(p._close_reason(s, AUDIO), "normal sessions close after the idle timeout as before")

    def test_nudge_text_names_the_owner(self):
        text = state(0)['event_session'].nudge_text()
        self.assertIn("Paul", text)
        self.assertIn(str(events.NO_ANSWER_NUDGE_S), text)


if __name__ == "__main__":
    unittest.main()
