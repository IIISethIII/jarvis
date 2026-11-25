"""core/settings_schema.py matches the code and .env.example.

Every os.getenv() key in main.py, core/ and web/ must be in the schema with the same default, and .env.example
must list exactly the schema keys. Pure standard library:  python -m unittest discover tests/unit
"""
import ast
import glob
import os
import re
import unittest

from core import settings_schema as schema

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# set by systemd (socket activation), not settings
EXTERNAL = {"LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES", "INVOCATION_ID"}


def _constants(tree):
    """Module-level NAME = "literal" / 123 assignments."""
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    consts[t.id] = node.value.value
    return consts


def _literal(node, consts):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in consts:
        return consts[node.id]
    if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "str" and len(node.args) == 1:
        value = _literal(node.args[0], consts)
        return None if value is None else str(value)
    raise ValueError(ast.dump(node))


def getenv_calls():
    """[(file, line, key, default or None)] for os.getenv / os.environ.get with a literal key."""
    calls = []
    files = [os.path.join(ROOT, "main.py")] + sorted(glob.glob(os.path.join(ROOT, "core", "*.py"))) \
        + sorted(glob.glob(os.path.join(ROOT, "web", "*.py")))
    for path in files:
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        consts = _constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            f = node.func
            is_getenv = f.attr == "getenv" and getattr(f.value, "id", None) == "os"
            is_environ_get = f.attr == "get" and isinstance(f.value, ast.Attribute) and f.value.attr == "environ"
            if not (is_getenv or is_environ_get) or not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            if node.args[0].value in EXTERNAL:
                continue
            default = _literal(node.args[1], consts) if len(node.args) > 1 else None
            calls.append((os.path.relpath(path, ROOT), node.lineno, node.args[0].value, default))
    return calls


def example_lines():
    """{key: (value, commented)} from .env.example."""
    found = {}
    with open(os.path.join(ROOT, ".env.example"), encoding="utf-8") as f:
        for line in f:
            m = re.match(r"^(#?)\s*([A-Z][A-Z0-9_]*)=(.*)$", line.rstrip("\n"))
            if m:
                found[m.group(2)] = (m.group(3).strip(), bool(m.group(1)))
    return found


def same(setting, a, b):
    if setting.kind in ("int", "float") and a and b:
        return float(a) == float(b)
    return (a or "") == (b or "")


class SchemaMatchesCode(unittest.TestCase):
    def test_every_getenv_key_is_in_the_schema(self):
        calls = getenv_calls()
        self.assertGreater(len(calls), 30)
        for path, line, key, default in calls:
            with self.subTest(f"{path}:{line} {key}"):
                self.assertTrue(key in schema.BY_KEY or key in schema.ALIASES,
                                f"{key} is read in {path}:{line} but missing in core/settings_schema.py")

    def test_defaults_match(self):
        for path, line, key, default in getenv_calls():
            setting = schema.BY_KEY.get(key)
            if setting is None or default is None:
                continue
            with self.subTest(f"{path}:{line} {key}"):
                self.assertTrue(same(setting, str(default), setting.default),
                                f"{path}:{line} uses default {default!r}, the schema says {setting.default!r}")


class SessionSettingsAreReadPerConversation(unittest.TestCase):
    """applies="session" promises that a change works without a restart: the code must read such a key inside a
    function that runs per conversation, never at import time or in a constructor."""

    def test_no_session_key_read_at_import_or_in_init(self):
        session = set(schema.session_keys())
        self.assertIn("JARVIS_IDLE_TIMEOUT", session)
        files = [os.path.join(ROOT, "main.py")] + sorted(glob.glob(os.path.join(ROOT, "core", "*.py")))
        for path in files:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node, where in _walk_with_scope(tree, "module"):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in ("getenv", "get") or not node.args:
                    continue
                key = getattr(node.args[0], "value", None)
                if key in session and where in ("module", "class", "__init__"):
                    self.fail(f"{os.path.relpath(path, ROOT)}:{node.lineno} reads {key} at {where} level, "
                              f"but the schema says it applies per session")


def _walk_with_scope(node, scope):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            inner = "__init__" if child.name == "__init__" else "function"
        elif isinstance(child, ast.ClassDef):
            inner = "class"
        else:
            inner = scope
        yield child, scope if inner == scope else inner
        yield from _walk_with_scope(child, inner)


class SchemaMatchesExample(unittest.TestCase):
    def test_same_keys(self):
        example = set(example_lines())
        keys = set(schema.BY_KEY)
        self.assertEqual(sorted(keys - example), [], "schema keys missing in .env.example")
        self.assertEqual(sorted(example - keys), [], ".env.example keys missing in the schema")

    def test_commented_defaults_match(self):
        for key, (value, commented) in example_lines().items():
            setting = schema.BY_KEY.get(key)
            # keys without a default show an example value instead
            if not commented or not value or setting is None or not setting.default:
                continue
            with self.subTest(key):
                self.assertTrue(same(setting, value, setting.default),
                                f".env.example has {key}={value}, the schema default is {setting.default!r}")


class SchemaIsConsistent(unittest.TestCase):
    def test_fields(self):
        groups = {g[0] for g in schema.GROUPS}
        for s in schema.SETTINGS:
            with self.subTest(s.key):
                self.assertIn(s.group, groups)
                self.assertIn(s.kind, ("text", "secret", "list", "int", "float", "bool", "url"))
                self.assertIn(s.applies, ("restart", "session", "web"))
                value, err = schema.validate(s, s.default)
                self.assertIsNone(err, f"default {s.default!r} is invalid: {err}")
        self.assertEqual(len(schema.SETTINGS), len(schema.BY_KEY), "duplicate keys")

    def test_validate(self):
        by = schema.BY_KEY
        self.assertEqual(schema.validate(by["JARVIS_IDLE_TIMEOUT"], " 20 "), ("20", None))
        self.assertIsNotNone(schema.validate(by["JARVIS_IDLE_TIMEOUT"], "abc")[1])
        self.assertIsNotNone(schema.validate(by["JARVIS_IDLE_TIMEOUT"], "1")[1])  # below min
        self.assertEqual(schema.validate(by["JARVIS_REFLECT_HOUR"], "-1"), ("-1", None))
        self.assertIsNotNone(schema.validate(by["JARVIS_REFLECT_HOUR"], "4.5")[1])
        self.assertEqual(schema.validate(by["JARVIS_LEARNER"], "off"), ("0", None))
        self.assertEqual(schema.validate(by["HA_URL"], "http://ha:8123/"), ("http://ha:8123", None))
        self.assertIsNotNone(schema.validate(by["HA_URL"], "ha:8123")[1])
        self.assertEqual(schema.validate(by["GEMINI_KEYS"], "a, b ,,c"), ("a,b,c", None))
        self.assertEqual(schema.validate(by["JARVIS_NAME"], ""), ("", None))
        self.assertIsNotNone(schema.validate(by["JARVIS_NAME"], "a\nb")[1])


if __name__ == "__main__":
    unittest.main()
