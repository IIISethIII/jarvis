"""Spoken placeholders ("<no speech>{pause}") are muted, and silence on the first words closes the session early."""
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from core import llm
from core.llm import GeminiLiveProvider


def provider():
    p = object.__new__(GeminiLiveProvider)  # skip __init__: no API keys, no VAD model
    p.idle_timeout, p.speech_grace, p.max_speech, p.end_grace, p.answer_wait = 15.0, 6.0, 20.0, 2.0, 8.0
    return p


def state(**extra):
    return {'last_activity': time.time(), 'last_user_speech': 0.0, 'speaking_since': None, 'end_requested': False,
            'awaiting_answer': None, 'answered': False, 'quick_close': None, 'is_thinking': False,
            **GeminiLiveProvider._answer_gate(), **extra}


class PlaceholderTest(unittest.TestCase):
    def test_detects_placeholders(self):
        for text in ("<no ", " <no speech>{pause}", "{pause}", "[pause]"):
            self.assertTrue(llm.is_placeholder(text), text)

    def test_speech_is_not_a_placeholder(self):
        for text in ("Es ist achtzehn ", " Uhr eins.", "", "Licht ist an <3"):
            self.assertFalse(llm.is_placeholder(text), text)


class AnswerGateTest(unittest.TestCase):
    def setUp(self):
        self.p, self.audio, self.hw = provider(), mock.Mock(), mock.Mock()

    def played(self):
        return [c.args[0] for c in self.audio.play_chunk.call_args_list]

    def test_speech_plays_after_first_transcript(self):
        s = state()
        self.p._answer_audio(s, self.audio, self.hw, b"a")
        self.assertEqual(self.played(), [])
        self.assertTrue(self.p._answer_transcript(s, self.audio, self.hw, "Es ist "))
        self.p._answer_audio(s, self.audio, self.hw, b"b")
        self.assertEqual(self.played(), [b"a", b"b"])
        self.assertTrue(s['turn_speech'] and s['answered'])
        self.hw.set_color.assert_called_with("blue", blink=False)

    def test_placeholder_is_never_played(self):
        # Measured 2026-11-25: "<no " arrives with the first audio chunk, "speech>" right after
        s = state()
        self.p._answer_audio(s, self.audio, self.hw, b"a")
        self.assertFalse(self.p._answer_transcript(s, self.audio, self.hw, "<no "))
        self.p._answer_audio(s, self.audio, self.hw, b"b")
        self.assertFalse(self.p._answer_transcript(s, self.audio, self.hw, "speech>{pause}"))
        self.assertEqual(self.played(), [])
        self.assertFalse(s.get('turn_speech') or s['answered'])

    def test_next_turn_is_checked_again(self):
        s = state()
        self.p._answer_transcript(s, self.audio, self.hw, "<no ")
        self.p._end_answer_turn(s, self.audio, self.hw, play_held=True)
        self.p._answer_audio(s, self.audio, self.hw, b"a")
        self.assertTrue(self.p._answer_transcript(s, self.audio, self.hw, "Hallo"))
        self.assertEqual(self.played(), [b"a"])

    def test_late_transcript_plays_unchecked_then_cuts_a_placeholder(self):
        s = state()
        self.p._answer_audio(s, self.audio, self.hw, b"a")
        s['held_since'] -= llm.HOLD_MAX_S + 0.1
        self.p._release_answer_if_late(s, self.audio, self.hw)
        self.assertEqual(self.played(), [b"a"])
        self.assertFalse(self.p._answer_transcript(s, self.audio, self.hw, "<no speech>"))
        self.audio.stop_playback.assert_called_once()

    def test_turn_end_without_transcript_plays_held_audio(self):
        s = state()
        self.p._answer_audio(s, self.audio, self.hw, b"a")
        self.p._end_answer_turn(s, self.audio, self.hw, play_held=True)
        self.assertEqual(self.played(), [b"a"])

    def test_interruption_drops_held_audio(self):
        s = state()
        self.p._answer_audio(s, self.audio, self.hw, b"a")
        self.p._end_answer_turn(s, self.audio, self.hw, play_held=False)
        self.assertEqual(self.played(), [])


AUDIO = SimpleNamespace(is_playing=False, last_speech_time=0.0)


class QuickCloseTest(unittest.TestCase):
    def test_silence_on_first_words_closes_after_grace(self):
        p, s = provider(), state(last_user_speech=time.time() - 1)
        p._arm_quick_close(s)
        s['quick_close'] -= p.end_grace + 0.5  # longer than end_conversation's grace: a thinking pause after "ehm"
        self.assertIsNone(p._close_reason(s, AUDIO))
        s['quick_close'] -= llm.FIRST_WORDS_GRACE_S
        self.assertIn("first words", p._close_reason(s, AUDIO))

    def test_not_after_jarvis_answered(self):
        p, s = provider(), state(last_user_speech=time.time() - 1, answered=True)
        p._arm_quick_close(s)
        self.assertIsNone(s['quick_close'])

    def test_not_without_user_speech(self):
        p, s = provider(), state()
        p._arm_quick_close(s)
        self.assertIsNone(s['quick_close'])

    def test_user_speaking_holds_session(self):
        p, s = provider(), state(quick_close=time.time() - 5, speaking_since=time.time() - 1)
        self.assertIsNone(p._close_reason(s, AUDIO))


if __name__ == "__main__":
    unittest.main()
