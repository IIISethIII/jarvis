"""Wake word models: the ones that come with openWakeWord and your own in data/wakewords/.

Standard library only: the web UI lists them without importing openWakeWord (onnxruntime, numpy).
A model is named after its file without the version suffix: hey_jarvis_v0.1.onnx -> hey_jarvis.
Only .onnx files: Jarvis runs openWakeWord on onnxruntime (no tflite runtime on the Pi).
"""
import importlib.util
import os
import re

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# files in openWakeWord's model folder that are no wake words: its feature models and two example intent models
NOT_WAKE_WORDS = {"embedding_model", "melspectrogram", "silero_vad", "timer", "weather"}
_VERSION = re.compile(r"_v\d+(\.\d+)*$")


def custom_dir():
    return os.path.join(os.getenv("JARVIS_DATA_DIR") or os.path.join(BASE_DIR, "data"), "wakewords")


def bundled_dir():
    """openWakeWord's model folder, found without importing the package (None if it isn't installed)."""
    try:
        spec = importlib.util.find_spec("openwakeword")
    except (ImportError, ValueError):
        return None
    if not spec or not spec.submodule_search_locations:
        return None
    return os.path.join(list(spec.submodule_search_locations)[0], "resources", "models")


def available():
    """[(name, path, origin)] with origin "own" or "openWakeWord"; your own models first, names unique."""
    out, seen = [], set()
    for folder, origin in ((custom_dir(), "own"), (bundled_dir(), "openWakeWord")):
        try:
            files = sorted(os.listdir(folder)) if folder else []
        except OSError:
            continue
        for f in files:
            stem, ext = os.path.splitext(f)
            name = _VERSION.sub("", stem)
            if ext.lower() != ".onnx" or name in NOT_WAKE_WORDS or name in seen:
                continue
            seen.add(name)
            out.append((name, os.path.join(folder, f), origin))
    return out


def find(name):
    """Path of the model called `name` (or a path to an .onnx file), None if there is none.
    A part of a name also works ("jarvis" -> hey_jarvis), as with the older pretrained-only lookup."""
    if not name:
        return None
    if os.path.isfile(name):
        return name
    models = available()
    for n, path, _ in models:
        if n == name:
            return path
    return next((path for n, path, _ in models if name in n), None)
