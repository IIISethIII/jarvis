"""Imports facts, rules and preferences from text files into the workspace (via the learner).

  python -m scripts.import_facts data/core_memory.md notes.txt
  python -m scripts.import_facts --workspace /tmp/ws old_memory.md   # into another workspace

The learner sorts the text into SOUL / AGENTS / USER / MEMORY and skills, drops noise and keeps the budgets.
Every change lands in CHANGES.md and can be undone with: python -m scripts.import_facts --undo
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHUNK_CHARS = 12_000  # one learner request per chunk
PASSES = 3  # a chunk is offered again until a pass adds nothing (one pass rarely sorts in everything)


def chunks(text):
    """Splits at line breaks into pieces of at most CHUNK_CHARS."""
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > CHUNK_CHARS and cur:
            out.append(cur)
            cur = ""
        cur += line
    if cur.strip():
        out.append(cur)
    return out


def import_text(text, source="import"):
    from core import learner
    applied = []
    for i, piece in enumerate(chunks(text), 1):
        for p in range(1, PASSES + 1):
            print(f"[Import] Part {i}, pass {p}: {len(piece)} chars", flush=True)
            new = learner.run("import", piece, source=source)
            applied += new
            if not new:
                break
    return applied


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="text or markdown files to import ('-' = stdin)")
    parser.add_argument("--workspace", help="workspace directory (default: data/workspace)")
    parser.add_argument("--undo", action="store_true", help="revert the last change batch instead")
    args = parser.parse_args()
    if args.workspace:
        os.environ["JARVIS_WORKSPACE"] = os.path.abspath(args.workspace)

    from core import workspace
    workspace.ensure()
    if args.undo:
        print(workspace.undo_last())
        return
    if not args.files:
        parser.error("no files given")
    for path in args.files:
        text = sys.stdin.read() if path == "-" else open(path, encoding="utf-8").read()
        applied = import_text(text, source=f"import {os.path.basename(path)}")
        print(f"[Import] {path}: {len(applied)} change(s), see {workspace._path('CHANGES.md')}")


if __name__ == "__main__":
    main()
