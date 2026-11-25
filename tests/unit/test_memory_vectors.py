"""Archive vectors remember their embedding model; after a model change the old ones are skipped.

Needs numpy:  venv/bin/python -m unittest tests.unit.test_memory_vectors
"""
import base64
import json
import os
import tempfile
import unittest

import numpy as np

from core import memory


def row(h, model=None):
    v = base64.b64encode(np.ones(4, dtype=np.float16).tobytes()).decode()
    r = {"h": h, "v": v}
    if model:
        r["m"] = model
    return json.dumps(r)


class VectorModelTest(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp()
        os.close(fd)
        with open(self.path, "w") as f:
            f.write("\n".join([row("old"), row("tagged", memory.EMBED_MODEL), row("new", "gemini-embedding-2")]) + "\n")
        self.old_file = memory.VECTORS_FILE
        memory.VECTORS_FILE = self.path

    def tearDown(self):
        memory.VECTORS_FILE = self.old_file
        os.environ.pop("JARVIS_EMBED_MODEL", None)
        os.remove(self.path)

    def test_default_model_reads_untagged_rows(self):
        self.assertEqual(sorted(memory._load_vectors()), ["old", "tagged"])

    def test_other_model_skips_them(self):
        os.environ["JARVIS_EMBED_MODEL"] = "gemini-embedding-2"
        self.assertEqual(sorted(memory._load_vectors()), ["new"])


if __name__ == "__main__":
    unittest.main()
