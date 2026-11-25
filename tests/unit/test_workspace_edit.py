"""Hand edits of the workspace (web UI): snapshot + CHANGES.md like a learner run, conflict check, lock, undo.

Pure standard library:  python -m unittest discover tests/unit
"""
import os
import shutil
import tempfile
import threading
import unittest

from core import workspace


class WorkspaceEditTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["JARVIS_WORKSPACE"] = self.dir
        workspace.ensure()
        workspace._write(workspace._path("MEMORY.md"), "# MEMORY\n- Lives in Munich\n")

    def tearDown(self):
        os.environ.pop("JARVIS_WORKSPACE", None)
        shutil.rmtree(self.dir)

    def test_edit_logs_and_undoes(self):
        before = workspace.read_file("MEMORY")
        changed = workspace.edit_by_hand("web", files={"MEMORY": "# MEMORY\n- Lives in Berlin"},
                                         expected={"MEMORY": workspace.text_hash(before)})
        self.assertEqual(len(changed), 1)
        self.assertEqual(workspace.read_file("MEMORY"), "# MEMORY\n- Lives in Berlin\n")
        entry = workspace.history()[0]
        self.assertEqual(entry["source"], "web")
        self.assertIn("MEMORY edited by hand", entry["lines"][0])
        self.assertEqual(workspace.undo_available()["source"], "web")
        workspace.undo_last()
        self.assertEqual(workspace.read_file("MEMORY"), before)

    def test_unchanged_text_writes_nothing(self):
        text = workspace.read_file("MEMORY")
        self.assertEqual(workspace.edit_by_hand("web", files={"MEMORY": text}), [])
        self.assertIsNone(workspace.undo_available())

    def test_conflict(self):
        stale = workspace.text_hash("something else")
        with self.assertRaises(workspace.Conflict):
            workspace.edit_by_hand("web", files={"MEMORY": "x"}, expected={"MEMORY": stale})
        self.assertIn("Munich", workspace.read_file("MEMORY"))

    def test_skills(self):
        text = workspace.skill_text("good-night", "When the user says good night", "1. Lights off")
        workspace.edit_by_hand("web", skills={"good-night": text}, expected={"skill:good-night": None})
        self.assertEqual([s["name"] for s in workspace.list_skills()], ["good-night"])
        # creating it again (expected: must not exist) is a conflict
        with self.assertRaises(workspace.Conflict):
            workspace.edit_by_hand("web", skills={"good-night": text}, expected={"skill:good-night": None})
        with self.assertRaises(ValueError):
            workspace.edit_by_hand("web", skills={"Bad Name": text})
        with self.assertRaises(ValueError):
            workspace.edit_by_hand("web", skills={"empty": workspace.skill_text("empty", "desc", "")})
        workspace.edit_by_hand("web", skills={"good-night": None})
        self.assertEqual(workspace.list_skills(), [])
        self.assertIn("deleted by hand", workspace.history()[0]["lines"][0])

    def test_two_edits_in_one_second_keep_both_snapshots(self):
        workspace.edit_by_hand("web", files={"MEMORY": "# MEMORY\n- one\n"})
        workspace.edit_by_hand("web", files={"MEMORY": "# MEMORY\n- two\n"})
        workspace.undo_last()
        self.assertEqual(workspace.read_file("MEMORY"), "# MEMORY\n- one\n")
        workspace.undo_last()
        self.assertIn("Munich", workspace.read_file("MEMORY"))

    def test_onboarding_queue(self):
        self.assertIsNone(workspace.pending_onboarding())
        workspace.queue_onboarding({"user_name": "Alex", "home": " ", "personality": "calm"})
        job = workspace.pending_onboarding()
        text = workspace.onboarding_text(job["answers"])
        self.assertIn("-> Alex", text)
        self.assertIn("-> calm", text)
        self.assertNotIn("Where do you live", text, "empty answers are left out")
        workspace.clear_onboarding()
        self.assertIsNone(workspace.pending_onboarding())
        workspace.clear_onboarding()  # twice is fine

    def test_lock(self):
        if workspace.fcntl is None:
            self.skipTest("no fcntl")
        held, release = threading.Event(), threading.Event()

        def learner():
            with workspace.lock():
                held.set()
                release.wait(5)

        t = threading.Thread(target=learner)
        t.start()
        held.wait(5)
        try:
            with self.assertRaises(workspace.Busy):
                with workspace.lock(wait=False):
                    pass
        finally:
            release.set()
            t.join()
        with workspace.lock(wait=False):
            pass


if __name__ == "__main__":
    unittest.main()
