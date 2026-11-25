"""The learner scheduler runs setup answers queued by the web UI, retries later on quota errors and survives errors.

Needs the runtime dependencies (python-dotenv):  venv/bin/python -m unittest tests.unit.test_learner_onboarding
"""
import os
import shutil
import tempfile
import unittest

from core import learner, workspace


class OnboardingJobTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["JARVIS_WORKSPACE"] = self.dir
        workspace.ensure()
        self.calls = []
        self.old_run = learner.run
        self.scheduler = learner.Scheduler()

    def tearDown(self):
        learner.run = self.old_run
        os.environ.pop("JARVIS_WORKSPACE", None)
        shutil.rmtree(self.dir)

    def test_runs_and_clears_the_job(self):
        learner.run = lambda mode, text, source=None, models=None: self.calls.append((mode, text, source)) or []
        workspace.queue_onboarding({"user_name": "Alex"})
        self.scheduler._onboarding()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][0], "onboarding")
        self.assertIn("Alex", self.calls[0][1])
        self.assertIsNone(workspace.pending_onboarding())

    def test_quota_error_keeps_the_job_for_later(self):
        def fail(*a, **k):
            self.calls.append(1)
            raise RuntimeError("429 quota")
        learner.run = fail
        workspace.queue_onboarding({"user_name": "Alex"})
        self.scheduler._onboarding()
        self.scheduler._onboarding()  # within the retry pause: not tried again
        self.assertEqual(len(self.calls), 1)
        self.assertIsNotNone(workspace.pending_onboarding())

    def test_unexpected_errors_dont_end_the_thread(self):
        def boom():
            raise KeyError("x")
        self.scheduler._tick = boom
        self.scheduler._wake.set()
        # one loop iteration by hand: _loop catches and logs
        import threading
        t = threading.Thread(target=self.scheduler._loop, daemon=True)
        t.start()
        t.join(0.3)
        self.assertTrue(t.is_alive())


if __name__ == "__main__":
    unittest.main()
