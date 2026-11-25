"""Thinking models: the level falls back when a model rejects it, and the session waits for background reasoning.

Needs the runtime dependencies (google-genai):  venv/bin/python -m unittest tests.unit.test_thinking
"""
import time
import unittest
from types import SimpleNamespace

from core import llm
from core.llm import GeminiLiveProvider, thinking_fallback

AUDIO = SimpleNamespace(is_playing=False, last_speech_time=0.0)


class FakeSfx:
    def __init__(self):
        self.calls = []

    def play_thinking(self, **kwargs):
        self.calls.append(kwargs)


def provider():
    p = object.__new__(GeminiLiveProvider)  # skip __init__: no API keys, no VAD model
    p.idle_timeout, p.speech_grace, p.max_speech, p.end_grace, p.answer_wait = 15.0, 6.0, 20.0, 2.0, 8.0
    return p


def state(**kw):
    now = time.time()
    s = {'last_activity': now - 30, 'last_user_speech': now - 40, 'speaking_since': None, 'end_requested': False,
         'awaiting_answer': None, 'background_since': None, 'is_thinking': False, 'tool_running': False}
    s.update(kw)
    return s


class ThinkingFallbackTest(unittest.TestCase):
    # the messages the Live API sent when connecting (2026-11-25)
    def test_model_without_thinking(self):
        err = "1007 None. Thinking level is not supported for this model."
        self.assertEqual(thinking_fallback(err, "low"), "")

    def test_model_that_needs_a_level(self):
        err = "1007 None. Thinking level must be specified for this model."
        self.assertEqual(thinking_fallback(err, ""), "low")

    def test_level_the_model_lacks(self):
        err = "1007 None. Thinking level MINIMAL is not supported for this model. Please retry with other thinking level."
        self.assertEqual(thinking_fallback(err, "minimal"), "low")

    def test_other_errors(self):
        self.assertIsNone(thinking_fallback("429 RESOURCE_EXHAUSTED quota", "low"))
        self.assertIsNone(thinking_fallback("1008 Requested entity was not found", ""))

    def test_config(self):
        self.assertIsNone(llm.live_config("p", [], "").thinking_config)
        self.assertEqual(str(llm.live_config("p", [], "high").thinking_config.thinking_level.value), "HIGH")


class BackgroundReasoningTest(unittest.TestCase):
    def test_heads_up_then_answer_later(self):
        p, sfx = provider(), FakeSfx()
        s = state()
        self.assertIsNotNone(p._close_reason(s, AUDIO), "30 s quiet closes without background work")
        p._track_background(s, False, True, "IN_PROGRESS", sfx)
        self.assertIsNotNone(s['background_since'])
        self.assertIsNone(p._close_reason(s, AUDIO), "the answer is still coming")
        self.assertEqual(len(sfx.calls), 1)
        self.assertTrue(sfx.calls[0]["after_speech"], "behind the spoken heads-up")
        p._track_background(s, False, True, "IN_PROGRESS", sfx)  # a second heads-up: no second sound
        self.assertEqual(len(sfx.calls), 1)
        p._track_background(s, False, True, "IDLE", sfx)
        self.assertIsNone(s['background_since'])
        self.assertIsNotNone(p._close_reason(s, AUDIO))

    def test_capped(self):
        p = provider()
        s = state(background_since=time.time() - llm.BACKGROUND_MAX_S - 1)
        self.assertIsNotNone(p._close_reason(s, AUDIO))

    def test_interrupt_ends_waiting(self):
        p = provider()
        s = state(background_since=time.time())
        p._track_background(s, True, False, None, None)
        self.assertIsNone(s['background_since'])

    def test_older_sdk_without_status_changes_nothing(self):
        p = provider()
        s = state()
        p._track_background(s, False, True, None, FakeSfx())
        self.assertIsNone(s['background_since'])

    def test_status_on_server_content_or_message(self):
        enum = SimpleNamespace(value="IN_PROGRESS")
        get = GeminiLiveProvider._interaction_status
        self.assertEqual(get(SimpleNamespace(), SimpleNamespace(interaction_status=enum)), "IN_PROGRESS")
        self.assertEqual(get(SimpleNamespace(interaction_status="IDLE"), SimpleNamespace()), "IDLE")
        self.assertIsNone(get(SimpleNamespace(), SimpleNamespace()))


if __name__ == "__main__":
    unittest.main()
