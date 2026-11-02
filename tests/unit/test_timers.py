import datetime
import json
import os
import queue
import tempfile
import time
import unittest

from core import timers
from core.timers import TimerManager


class FakeAudio:
    def __init__(self):
        self.playback_queue = queue.Queue()
        self.stopped = 0

    def play_chunk(self, pcm_data, is_tts=True):
        self.playback_queue.put((pcm_data, is_tts))

    def stop_playback(self):
        self.stopped += 1
        with self.playback_queue.mutex:
            self.playback_queue.queue.clear()


class FakeHardware:
    def __init__(self):
        self.pulsing = False

    def is_pulsing(self):
        return self.pulsing

    def start_pulse(self, r, g, b):
        self.pulsing = True

    def turn_off(self):
        self.pulsing = False


class TimerManagerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "timers.json")
        self.tm = TimerManager(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_set_timer_and_persist(self):
        self.assertIn("10 Min.", self.tm.set_timer(600, "Nudeln"))
        self.tm.set_timer(60)
        reloaded = TimerManager(self.path)
        self.assertEqual([t["label"] for t in reloaded._timers], ["Timer", "Nudeln"])
        self.assertEqual(reloaded._next_id, 3)

    def test_rejects_invalid_input(self):
        self.assertTrue(self.tm.set_timer(0).startswith("Fehler"))
        self.assertTrue(self.tm.set_alarm("25:00").startswith("Fehler"))
        self.assertTrue(self.tm.set_alarm("7:00", "2000-01-01").startswith("Fehler"))
        self.assertEqual(self.tm._timers, [])

    def test_alarm_rolls_over_to_next_day(self):
        past = datetime.datetime.now() - datetime.timedelta(minutes=5)
        result = self.tm.set_alarm(f"{past:%H:%M}")
        due = datetime.datetime.fromtimestamp(self.tm._timers[0]["due"])
        self.assertGreater(due, datetime.datetime.now())
        self.assertEqual(due.date(), past.date() + datetime.timedelta(days=1))
        self.assertIn("Wecker #1", result)

    def test_alarm_accepts_dot_and_hour_only(self):
        self.tm.set_alarm("6.30")
        self.tm.set_alarm("7")
        clocks = sorted(f"{datetime.datetime.fromtimestamp(t['due']):%H:%M}" for t in self.tm._timers)
        self.assertEqual(clocks, ["06:30", "07:00"])

    def test_cancel(self):
        self.assertIn("Kein passender", self.tm.cancel())
        self.tm.set_timer(60, "Nudeln")
        self.tm.set_timer(120, "Tee")
        self.assertIn("Mehrere Timer", self.tm.cancel())
        self.assertIn("'Tee'", self.tm.cancel(label="tee"))
        self.assertIn("'Nudeln'", self.tm.cancel())  # the only one left
        self.tm.set_timer(60)
        self.tm.set_timer(60)
        self.assertIn("2 gelöscht", self.tm.cancel("all"))
        self.assertEqual(self.tm.list_timers(), "Keine Timer oder Wecker aktiv.")

    def test_drops_timers_missed_long_ago(self):
        now = time.time()
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"next_id": 3, "timers": [
                {"id": "1", "kind": "alarm", "label": "alt", "due": now - timers.MAX_LATE - 60},
                {"id": "2", "kind": "timer", "label": "knapp", "due": now - 30},
            ]}, f)
        tm = TimerManager(self.path)
        self.assertEqual([t["label"] for t in tm._timers], ["knapp"])

    def test_fires_rings_and_stops(self):
        audio, hardware, rang = FakeAudio(), FakeHardware(), []
        self.tm.start(lambda: audio, hardware, on_ring=lambda: rang.append(True))
        self.tm.set_timer(1, "kurz")
        deadline = time.time() + 3
        while audio.playback_queue.empty() and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(self.tm.is_ringing)
        self.assertEqual(rang, [True])
        self.assertEqual(self.tm._timers, [])
        time.sleep(1.2)
        self.assertTrue(hardware.pulsing)
        pcm, is_tts = audio.playback_queue.get_nowait()
        self.assertFalse(is_tts)
        self.assertEqual(len(pcm), timers.PIECE_BYTES)

        self.assertTrue(self.tm.stop_ringing("test"))
        self.assertFalse(self.tm.is_ringing)
        self.assertFalse(hardware.pulsing)
        self.assertFalse(self.tm.stop_ringing("test"))
        time.sleep(0.3)
        self.assertTrue(audio.playback_queue.empty())


if __name__ == "__main__":
    unittest.main()
