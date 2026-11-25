"""core/envfile.py reads .env like python-dotenv and edits single keys without touching other lines.

Pure standard library:  python -m unittest discover tests/unit
(the comparison with python-dotenv runs only where it is installed, e.g. on the Pi with venv/bin/python)
"""
import os
import shutil
import stat
import tempfile
import unittest

from core import envfile

SAMPLE = """# Gemini
GEMINI_KEYS=key1,key2
export HA_URL=http://ha.local:8123
QUOTED="a b # not a comment"
SINGLE='x'
INLINE=value # comment
EMPTY=

# Optional
#JARVIS_IDLE_TIMEOUT=15
#JARVIS_SFX_PEAK=0.7
"""

TRICKY = ["plain", "with space", "has#hash", "dollar $HOME $X", "quote's", 'double"q', "back\\slash",
          " lead and trail ", "ünïcödé", "a=b", "x,y,z", "", "'", "\\'"]


class EnvFileTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, ".env")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(SAMPLE)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def text(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_read(self):
        self.assertEqual(envfile.read(self.path), {
            "GEMINI_KEYS": "key1,key2", "HA_URL": "http://ha.local:8123", "QUOTED": "a b # not a comment",
            "SINGLE": "x", "INLINE": "value", "EMPTY": "",
        })
        self.assertEqual(envfile.read(os.path.join(self.dir, "missing")), {})

    def test_replace_in_place(self):
        self.assertEqual(envfile.update({"GEMINI_KEYS": "new"}, self.path), {"GEMINI_KEYS"})
        self.assertEqual(self.text(), SAMPLE.replace("GEMINI_KEYS=key1,key2", "GEMINI_KEYS=new"))

    def test_new_key_takes_commented_line(self):
        envfile.update({"JARVIS_IDLE_TIMEOUT": "20"}, self.path)
        self.assertEqual(self.text(), SAMPLE.replace("#JARVIS_IDLE_TIMEOUT=15", "JARVIS_IDLE_TIMEOUT=20"))

    def test_unknown_key_is_appended(self):
        envfile.update({"NEW_KEY": "1"}, self.path)
        self.assertEqual(self.text(), SAMPLE + "NEW_KEY=1\n")

    def test_remove_comments_out(self):
        envfile.update({"HA_URL": None}, self.path)
        self.assertIn("#export HA_URL=http://ha.local:8123\n", self.text())
        self.assertNotIn("HA_URL", envfile.read(self.path))
        # setting it again uses the commented line
        envfile.update({"HA_URL": "http://x"}, self.path)
        self.assertEqual(self.text().count("HA_URL"), 1)
        self.assertEqual(envfile.read(self.path)["HA_URL"], "http://x")

    def test_no_change_no_write(self):
        before = os.stat(self.path).st_mtime_ns
        self.assertEqual(envfile.update({"GEMINI_KEYS": "key1,key2", "MISSING": None}, self.path), set())
        self.assertEqual(os.stat(self.path).st_mtime_ns, before)

    def test_duplicates_collapse(self):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write("GEMINI_KEYS=dup\n")
        envfile.update({"GEMINI_KEYS": "one"}, self.path)
        self.assertEqual(self.text().count("GEMINI_KEYS="), 1)

    def test_round_trip(self):
        for value in TRICKY:
            with self.subTest(value=value):
                envfile.update({"TRICKY": value}, self.path)
                self.assertEqual(envfile.read(self.path)["TRICKY"], value)

    def test_private_and_atomic(self):
        os.chmod(self.path, 0o664)
        envfile.update({"GEMINI_KEYS": "x"}, self.path)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)
        self.assertEqual([f for f in os.listdir(self.dir) if f != ".env"], [])

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            envfile.update({"A": "two\nlines"}, self.path)
        with self.assertRaises(ValueError):
            envfile.update({"BAD KEY": "x"}, self.path)
        with self.assertRaises(ValueError):
            envfile.update({"A": "${HOME}"}, self.path)

    def test_reload(self):
        keys = ["GEMINI_KEYS", "JARVIS_IDLE_TIMEOUT", "FROM_SYSTEMD"]
        saved = {k: os.environ.get(k) for k in keys}
        try:
            os.environ.update({"GEMINI_KEYS": "key1,key2", "FROM_SYSTEMD": "x"})
            os.environ.pop("JARVIS_IDLE_TIMEOUT", None)
            base = {"FROM_SYSTEMD": "x"}
            envfile.update({"JARVIS_IDLE_TIMEOUT": "20", "GEMINI_KEYS": None, "FROM_SYSTEMD": "from .env"}, self.path)
            changed = envfile.reload(keys, base, self.path)
            self.assertEqual(sorted(changed), ["GEMINI_KEYS", "JARVIS_IDLE_TIMEOUT"])
            self.assertEqual(os.environ["JARVIS_IDLE_TIMEOUT"], "20")
            self.assertNotIn("GEMINI_KEYS", os.environ, "removed from .env -> default again")
            self.assertEqual(os.environ["FROM_SYSTEMD"], "x", "the real environment wins, like load_dotenv")
            self.assertEqual(envfile.reload(keys, base, self.path), [])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_same_as_python_dotenv(self):
        try:
            from dotenv import dotenv_values
        except ImportError:
            self.skipTest("python-dotenv not installed")
        for value in TRICKY:
            envfile.update({"TRICKY": value}, self.path)
        expected = dict(dotenv_values(self.path))
        self.assertEqual(envfile.read(self.path), expected)
        for value in TRICKY:
            with self.subTest(value=value):
                envfile.update({"TRICKY": value}, self.path)
                self.assertEqual(dotenv_values(self.path)["TRICKY"], value)


if __name__ == "__main__":
    unittest.main()
