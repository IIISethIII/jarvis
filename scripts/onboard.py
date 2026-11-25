"""First-time setup: settings in .env, then the first workspace entries (personality, user), then an optional import.

  python -m scripts.onboard                      # interactive
  python -m scripts.onboard --answers a.json     # unattended, keys as in QUESTIONS / SETTINGS below
  python -m scripts.onboard --workspace /tmp/ws  # into another workspace, .env stays untouched

Existing values are offered as defaults; press Enter to keep them. Secrets are only written to .env.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from core import envfile, workspace  # noqa: E402

ENV_FILE = envfile.DEFAULT_PATH

# (.env key, question, default)
SETTINGS = [
    ("JARVIS_LANGUAGE", "Language the assistant speaks (e.g. Deutsch, English)", "English"),
    ("JARVIS_NAME", "Name of the assistant", "Jarvis"),
    ("GEMINI_KEYS", "Gemini API key(s), comma-separated", ""),
    ("HA_URL", "Home Assistant URL (empty = no smart home)", ""),
    ("HA_TOKEN", "Home Assistant long-lived token", ""),
    ("OPENCLAW_URL", "OpenClaw Gateway URL for delegation (empty = off)", ""),
    ("OPENCLAW_TOKEN", "OpenClaw Gateway token", ""),
    ("OPENCLAW_NAME", "Name of the OpenClaw agent", "OpenClaw"),
]
SECRET = {"GEMINI_KEYS", "HA_TOKEN", "OPENCLAW_TOKEN"}

# answers that go to the learner for SOUL and USER (shared with the web UI's setup)
QUESTIONS = workspace.ONBOARDING_QUESTIONS


def read_env():
    return envfile.read(ENV_FILE)


def write_env(updates):
    """Updates or adds keys and keeps every other line (comments included) as it is."""
    envfile.update(updates, ENV_FILE)


def ask(question, default=""):
    shown = f" [{default}]" if default else ""
    answer = input(f"{question}{shown}: ").strip()
    return answer or default


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--answers", help="JSON file with answers (setting keys and question keys)")
    parser.add_argument("--workspace", help="workspace directory; with it, .env is not changed")
    parser.add_argument("--import", dest="import_files", nargs="*", default=None, help="files to import afterwards")
    args = parser.parse_args()
    given = json.load(open(args.answers, encoding="utf-8")) if args.answers else None

    env = read_env()
    updates = {}
    for key, question, default in SETTINGS:
        current = env.get(key, default)
        if given is not None:
            value = given.get(key, current)
        else:
            value = ask(question, "***" if key in SECRET and current else current)
            if value == "***":
                value = current
        if value != env.get(key, None) and (value or key in env):
            updates[key] = value
        os.environ[key] = value  # the learner below uses the new language and keys
    if args.workspace:
        os.environ["JARVIS_WORKSPACE"] = os.path.abspath(args.workspace)
    elif updates:
        write_env(updates)
        print(f"[Onboard] .env updated: {', '.join(sorted(updates))}")

    answers = {}
    for key, question in QUESTIONS:
        answers[key] = given.get(key, "") if given is not None else ask(question)

    from core import learner
    workspace.ensure()
    text = workspace.onboarding_text(answers)
    if text:
        learner.run("onboarding", text, source="onboarding")

    files = args.import_files
    if files is None and given is None:
        path = ask("File with facts to import (old memory, notes; empty = skip)")
        files = [path] if path else []
    if files:
        from scripts.import_facts import import_text
        for path in files:
            import_text(open(path, encoding="utf-8").read(), source=f"import {os.path.basename(path)}")
    print(f"[Onboard] Done. Workspace: {workspace.root()}")


if __name__ == "__main__":
    main()
