"""core/status.py: only the process that called init() writes, readers detect a dead writer.

Pure standard library:  python -m unittest discover tests/unit
"""
import json
import os
import shutil
import tempfile
import unittest

from core import status


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["JARVIS_STATUS_FILE"] = os.path.join(self.dir, "status.json")
        status._state = None

    def tearDown(self):
        status._state = None
        os.environ.pop("JARVIS_STATUS_FILE", None)
        shutil.rmtree(self.dir)

    def test_update_without_init_writes_nothing(self):
        status.update(state="idle")
        status.set_mcp_server("x", True)
        self.assertFalse(os.path.exists(status.path()))
        self.assertEqual(status.read(), {})

    def test_init_and_update(self):
        status.init(test_mode=False)
        data = status.read()
        self.assertEqual(data["state"], "booting")
        self.assertEqual(data["pid"], os.getpid())
        since = data["since"]
        status.update(state="idle", error="boom")
        status.set_mcp_server("gateway", True)
        status.update(alarm=True)
        data = status.read()
        self.assertEqual(data["state"], "idle")
        self.assertGreaterEqual(data["since"], since)
        self.assertEqual(data["error"], "boom")
        self.assertIsNotNone(data["error_at"])
        self.assertEqual(data["mcp"]["servers"], {"gateway": True})
        self.assertTrue(data["alarm"])
        self.assertEqual([f for f in os.listdir(self.dir)], ["status.json"])

    def test_refresh_config_only_touches_session_keys(self):
        status.init(config={"JARVIS_IDLE_TIMEOUT": "old", "JARVIS_VOICE": "old", "HA_URL": "old"})
        status.refresh_config(["JARVIS_IDLE_TIMEOUT", "JARVIS_VOICE"], {"JARVIS_IDLE_TIMEOUT": "new", "HA_URL": "new"})
        self.assertEqual(status.read()["config"], {"JARVIS_IDLE_TIMEOUT": "new", "HA_URL": "old"})

    def test_dead_writer(self):
        with open(status.path(), "w") as f:
            json.dump({"pid": 999999999, "state": "idle"}, f)
        self.assertFalse(status.read()["alive"])

    def test_other_program_with_same_pid(self):
        # this test process is not main.py, so its pid must not count as Jarvis
        with open(status.path(), "w") as f:
            json.dump({"pid": os.getpid(), "state": "idle"}, f)
        if os.path.isdir("/proc/self"):
            self.assertFalse(status.read()["alive"])


if __name__ == "__main__":
    unittest.main()
