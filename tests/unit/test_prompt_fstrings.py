"""Every name used in an f-string must exist where the f-string is built.

Regression: a prompt rule mentioned the placeholder "{pause}" inside the system prompt
f-string. That only fails at runtime (NameError), so every session aborted right after
the wake word while the service still booted cleanly.

Pure standard library, runs anywhere:  python -m unittest discover tests/unit
"""
import ast
import builtins
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FILES = [os.path.join("core", f) for f in sorted(os.listdir(os.path.join(ROOT, "core"))) if f.endswith(".py")]
FILES.append("main.py")


def bound_names(node):
    """Names bound anywhere inside a function or module body (params, assignments, loops, imports, ...)."""
    names = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            names.add(n.id)
        elif isinstance(n, ast.arg):
            names.add(n.arg)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            names.update((a.asname or a.name).split(".")[0] for a in n.names)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            names.add(n.name)
    return names


def unknown_fstring_names(source):
    tree = ast.parse(source)
    module_names = bound_names(tree) | set(dir(builtins))
    problems = []
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        known = module_names | bound_names(func)
        for node in ast.walk(func):
            if isinstance(node, ast.FormattedValue):
                for n in ast.walk(node.value):
                    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in known:
                        problems.append(f"line {n.lineno}: {{{n.id}}} in {func.name}()")
    return problems


class FStringNames(unittest.TestCase):
    def test_detects_the_pause_bug(self):
        src = 'def connect():\n    time_str = "now"\n    return f"""Zeit {time_str}. Sag nie {pause}."""\n'
        self.assertEqual(unknown_fstring_names(src), ["line 3: {pause} in connect()"])

    def test_runtime_files_have_no_unknown_names(self):
        for rel in FILES:
            with self.subTest(file=rel):
                with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                    self.assertEqual(unknown_fstring_names(f.read()), [])


if __name__ == "__main__":
    unittest.main()
