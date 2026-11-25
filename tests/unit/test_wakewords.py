"""core/wakewords.py: your own models in data/wakewords/ and openWakeWord's, by name, without importing it.

Pure standard library:  python -m unittest discover tests/unit
"""
import os
import shutil
import tempfile
import unittest

from core import wakewords


def touch(folder, *names):
    os.makedirs(folder, exist_ok=True)
    for n in names:
        open(os.path.join(folder, n), "w").close()


class WakewordsTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.bundled = os.path.join(self.dir, "oww")
        touch(self.bundled, "hey_jarvis_v0.1.onnx", "alexa_v0.1.onnx", "alexa_v0.1.tflite", "melspectrogram.onnx",
              "embedding_model.onnx", "silero_vad.onnx", "timer_v0.1.onnx", "weather_v0.1.onnx")
        touch(os.path.join(self.dir, "data", "wakewords"), "hey_jarvis.onnx", "computer_v2.onnx", "README.txt")
        os.environ["JARVIS_DATA_DIR"] = os.path.join(self.dir, "data")
        self.old_bundled = wakewords.bundled_dir
        wakewords.bundled_dir = lambda: self.bundled

    def tearDown(self):
        wakewords.bundled_dir = self.old_bundled
        os.environ.pop("JARVIS_DATA_DIR", None)
        shutil.rmtree(self.dir)

    def test_lists_wake_words_only_own_first(self):
        models = wakewords.available()
        self.assertEqual([(n, o) for n, _, o in models],
                         [("computer", "own"), ("hey_jarvis", "own"), ("alexa", "openWakeWord")])
        self.assertTrue(models[2][1].endswith("alexa_v0.1.onnx"))

    def test_find(self):
        own = os.path.join(self.dir, "data", "wakewords", "hey_jarvis.onnx")
        self.assertEqual(wakewords.find("hey_jarvis"), own)  # your own file wins over the bundled one
        self.assertEqual(wakewords.find("jarvis"), own)  # part of a name, like the old lookup
        self.assertTrue(wakewords.find("alexa").endswith("alexa_v0.1.onnx"))
        self.assertEqual(wakewords.find(own), own)  # a path
        self.assertIsNone(wakewords.find("timer"))
        self.assertIsNone(wakewords.find("nothing"))
        self.assertIsNone(wakewords.find(""))

    def test_without_openwakeword(self):
        wakewords.bundled_dir = lambda: None
        self.assertEqual([n for n, _, _ in wakewords.available()], ["computer", "hey_jarvis"])
        shutil.rmtree(os.path.join(self.dir, "data"))
        self.assertEqual(wakewords.available(), [])


if __name__ == "__main__":
    unittest.main()
