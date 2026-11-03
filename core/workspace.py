"""The workspace: personal prompt files that Jarvis reads every session and the learner (core/learner.py) maintains.

Layout (data/workspace/, gitignored; JARVIS_WORKSPACE or JARVIS_DATA_DIR move it):
  SOUL.md, AGENTS.md, USER.md, MEMORY.md   prompt files, one entry per "- " line
  skills/<name>.md                         learned procedures with a small frontmatter
  CHANGES.md                               every change with its reason
  .history/<run>/                          copies of the files a run changed, taken before the change
  .inbox.jsonl                             notes Jarvis passed on with 'remember', not yet processed
  .usage.json                              when each skill was last loaded

Changes only ever touch single entries (add / replace / remove). There is deliberately no way to rewrite a
whole file: repeated rewrites by a model slowly blur and shorten what was there. Untouched text stays byte-identical.
"""
import json
import os
import re
import shutil
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BASE_DIR, "prompts", "templates")

# name -> (file, character budget). The budget keeps the prompt small; the learner gets an error when a change exceeds it.
FILES = {
    "SOUL": ("SOUL.md", 2000),
    "AGENTS": ("AGENTS.md", 3000),
    "USER": ("USER.md", 2000),
    "MEMORY": ("MEMORY.md", 5000),
}
SKILL_BUDGET = 2000
MAX_SKILLS = 30
HISTORY_RUNS = 50  # runs kept in .history/
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def root():
    data_dir = os.getenv("JARVIS_DATA_DIR") or os.path.join(BASE_DIR, "data")
    return os.getenv("JARVIS_WORKSPACE") or os.path.join(data_dir, "workspace")


def _path(*parts):
    return os.path.join(root(), *parts)


def ensure():
    """Creates missing workspace files from prompts/templates/. Existing files are never touched."""
    os.makedirs(_path("skills"), exist_ok=True)
    for _, (fname, _) in FILES.items():
        target = _path(fname)
        if not os.path.exists(target):
            src = os.path.join(TEMPLATES_DIR, fname)
            if os.path.exists(src):
                shutil.copyfile(src, target)
            else:
                _write(target, f"# {fname[:-3]}\n")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _write(path, text):
    """Atomic write, so a session starting meanwhile never reads half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def read_file(name):
    return _read(_path(FILES[name][0]))


def prompt_text(name):
    """File content for the system prompt, cut at the budget (only manual edits can exceed it)."""
    text = read_file(name).strip()
    budget = FILES[name][1]
    if len(text) > budget:
        print(f"[Workspace] {FILES[name][0]} has {len(text)} chars, budget {budget}: cut in the prompt.", flush=True)
        text = text[:budget] + "\n[... cut: file exceeds its budget]"
    return text


# --- Skills ------------------------------------------------------------------

def _parse_skill(text):
    """Returns (meta dict, body) of a skill file with a '---' frontmatter of 'key: value' lines."""
    meta, body = {}, text
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            for line in text[4:end].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[end + 4:].lstrip("\n")
    return meta, body


def _skill_text(name, description, body, always=False):
    head = f"---\nname: {name}\ndescription: {description.strip()}\n"
    if always:
        head += "always: true\n"
    return head + "---\n" + (body.strip() + "\n" if body.strip() else "")


def list_skills():
    """[{name, description, always, body, text}] of all active skills, sorted by name."""
    skills = []
    folder = _path("skills")
    if not os.path.isdir(folder):
        return skills
    for fname in sorted(os.listdir(folder)):
        if not fname.endswith(".md"):
            continue
        text = _read(os.path.join(folder, fname))
        meta, body = _parse_skill(text)
        skills.append({
            "name": fname[:-3],
            "description": meta.get("description", ""),
            "always": meta.get("always", "").lower() == "true",
            "body": body.strip(),
            "text": text,
        })
    return skills


def load_skills(names):
    """Returns the bodies of the requested skills for Jarvis and records their use."""
    if isinstance(names, str):
        names = [n.strip() for n in names.split(",")]
    by_name = {s["name"]: s for s in list_skills()}
    parts, missing = [], []
    for n in names or []:
        s = by_name.get(str(n).strip())
        if s:
            parts.append(f"=== Skill {s['name']} ===\n{s['body']}")
        else:
            missing.append(str(n))
    if parts:
        _record_usage([n for n in names if n in by_name])
    if missing:
        parts.append(f"Unknown skills: {', '.join(missing)}. Available: {', '.join(by_name) or 'none'}.")
    return "\n\n".join(parts) or "No skill requested."


def _record_usage(names):
    path = _path(".usage.json")
    try:
        usage = json.loads(_read(path) or "{}")
    except ValueError:
        usage = {}
    today = datetime.now().strftime("%Y-%m-%d")
    for n in names:
        usage[n] = today
    try:
        _write(path, json.dumps(usage, indent=1, sort_keys=True))
    except OSError as e:
        print(f"[Workspace] Could not record skill usage: {e}", flush=True)


def skill_usage():
    try:
        return json.loads(_read(_path(".usage.json")) or "{}")
    except ValueError:
        return {}


# --- Inbox (notes from 'remember') -------------------------------------------

def add_note(note, session_id=""):
    entry = {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "session": session_id, "note": note.strip()}
    os.makedirs(root(), exist_ok=True)
    with open(_path(".inbox.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_notes():
    notes = []
    for line in _read(_path(".inbox.jsonl")).splitlines():
        try:
            notes.append(json.loads(line))
        except ValueError:
            continue
    return notes


def drop_notes(count):
    """Removes the first `count` notes (the ones a learner run processed); notes added meanwhile stay."""
    lines = _read(_path(".inbox.jsonl")).splitlines(keepends=True)
    _write(_path(".inbox.jsonl"), "".join(lines[count:]))


# --- Changes -------------------------------------------------------------------

def _norm(text):
    return " ".join(text.casefold().split())


def _line_end(text, i):
    j = text.find("\n", i)
    return len(text) if j == -1 else j + 1


def _entry_bounds(text, pos):
    """Start and end of the entry (its line plus indented continuation lines) that contains position pos."""
    start = text.rfind("\n", 0, pos) + 1
    end = _line_end(text, start)
    while end < len(text) and text[end] in " \t":
        nxt = _line_end(text, end)
        if not text[end:nxt].strip():
            break
        end = nxt
    return start, end


def _find_unique(text, old):
    if not old or not old.strip():
        return None, "old_text is empty"
    count = text.count(old)
    if count == 0:
        return None, f"old_text not found: {old[:80]!r}"
    if count > 1:
        return None, f"old_text occurs {count} times, make it unique: {old[:80]!r}"
    return text.index(old), None


def _entry_line(text):
    text = " ".join(text.strip().split("\n"))
    return text[2:].strip() if text.startswith("- ") else text


def _split_entries(text):
    """Returns (section from a leading '## ' line or None, entries). Several "- " lines are several entries."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    section = None
    if len(lines) > 1 and lines[0].startswith("#"):
        section = lines.pop(0).lstrip("#").strip()
    if len(lines) > 1 and all(l.startswith("- ") for l in lines):
        return section, [l[2:].strip() for l in lines]
    return section, [_entry_line("\n".join(lines))]


def _add_entry(content, text, section=None):
    section = (section or "").strip().lstrip("#").strip()
    line = f"- {_entry_line(text)}\n"
    if not content.endswith("\n") and content:
        content += "\n"
    if section:
        heading = re.search(rf"^##+\s*{re.escape(section.strip())}\s*$", content, re.M | re.I)
        if heading:
            nxt = re.search(r"^##?\s", content[heading.end():], re.M)
            pos = heading.end() + nxt.start() if nxt else len(content)
            before = content[:pos]
            # keep the blank line in front of the next heading below the new entry
            stripped = before.rstrip("\n")
            gap = before[len(stripped):]
            return stripped + "\n" + line + (gap[1:] if gap.startswith("\n") else gap) + content[pos:]
        return content + f"\n## {section.strip()}\n" + line
    return content + line


class Run:
    """One batch of changes. Collects them in memory, then writes changed files once, with a snapshot in .history/."""

    def __init__(self, source):
        ensure()
        self.source = source  # shown in CHANGES.md, e.g. "session (gemini-3.6-flash)"
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.files = {name: read_file(name) for name in FILES}
        self.original = dict(self.files)
        self.skills = {s["name"]: s["text"] for s in list_skills()}
        self.original_skills = dict(self.skills)
        self.log = []  # (description, reason)
        self.undo_requested = False
        self.locked = set()  # targets this run may not change

    def apply(self, change):
        """Applies one change dict; returns an error string or None."""
        target = str(change.get("target", "")).upper()
        action = str(change.get("action", "")).lower()
        reason = (change.get("reason") or "").strip()
        if action == "undo":
            self.undo_requested = True
            return None
        if target == "SKILL":
            return self._apply_skill(change, action, reason)
        if target not in FILES:
            return f"unknown target {target!r}"
        if target in self.locked:
            return f"{target} can't be changed in this run (only on an explicit request of the user)"
        content = self.files[target]
        old, text = change.get("old_text") or "", change.get("text") or ""
        if action == "add":
            if not text.strip():
                return "add needs text"
            section, entries = _split_entries(text)
            dups = [e for e in entries if _norm(e) in _norm(content)]
            if len(dups) == len(entries):
                return f"duplicate, already in {target}: {text[:80]!r}"
            new = content
            for e in entries:
                if e not in dups:
                    new = _add_entry(new, e, change.get("section") or section)
            desc = f"{target} add: " + " | ".join(e for e in entries if e not in dups)
        elif action == "replace":
            pos, err = _find_unique(content, old)
            if err:
                return f"{target}: {err}"
            if not text.strip():
                return "replace needs text (use remove to delete)"
            repl = text.strip()
            if repl.startswith("- ") and content[max(0, pos - 2):pos] == "- ":
                repl = repl[2:]  # old_text started after the bullet, the new text repeats it
            new = content[:pos] + repl + content[pos + len(old):]
            desc = f"{target} replace: {old.strip()[:120]} -> {repl[:120]}"
        elif action == "remove":
            pos, err = _find_unique(content, old)
            if err:
                return f"{target}: {err}"
            start, end = _entry_bounds(content, pos)
            desc = f"{target} remove: {content[start:end].strip()[:160]}"
            new = content[:start] + content[end:]
        else:
            return f"unknown action {action!r} for {target}"
        budget = FILES[target][1]
        if len(new) > budget and len(new) > len(content):
            return (f"{target} would have {len(new)} chars, budget {budget}. Remove or merge entries first "
                    f"(remove/replace in the same answer), keeping their meaning.")
        self.files[target] = new
        self.log.append((desc, reason))
        return None

    def _apply_skill(self, change, action, reason):
        name = str(change.get("skill") or "").strip().lower()
        if not SKILL_NAME_RE.match(name):
            return f"invalid skill name {name!r} (lowercase letters, digits, dashes)"
        text = self.skills.get(name)
        if action == "create":
            if text is not None:
                return f"skill {name} exists, change it with replace/add"
            if len(self.skills) >= MAX_SKILLS:
                return f"already {MAX_SKILLS} skills, delete or merge one first"
            body, desc = change.get("text") or "", change.get("description") or ""
            if not desc.strip():
                return "create needs a description"
            # the steps may follow as 'add' changes in the same batch; a skill still empty at commit is dropped
            new = _skill_text(name, desc, body, str(change.get("always", "")).lower() == "true")
            log = f"skill {name} created: {desc.strip()}"
        elif text is None:
            return f"skill {name} does not exist"
        elif action == "delete":
            self.skills[name] = None
            self.log.append((f"skill {name} deleted", reason))
            return None
        elif action == "replace" and not (change.get("old_text") or "").strip() and not _parse_skill(text)[1].strip():
            # models often create a skill without steps and fill it right after
            if not (change.get("text") or "").strip():
                return "replace needs text"
            meta, _ = _parse_skill(text)
            new = _skill_text(name, meta.get("description", ""), change["text"], meta.get("always", "") == "true")
            log = f"skill {name} steps: {change['text'].strip()[:120]}"
        elif action == "replace":
            pos, err = _find_unique(text, change.get("old_text") or "")
            if err:
                return f"skill {name}: {err}"
            if not (change.get("text") or "").strip():
                return "replace needs text"
            new = text[:pos] + change["text"].strip() + text[pos + len(change["old_text"]):]
            log = f"skill {name} replace: {change['old_text'].strip()[:100]} -> {change['text'].strip()[:100]}"
        elif action == "add":
            if not (change.get("text") or "").strip():
                return "add needs text"
            new = text.rstrip("\n") + "\n" + change["text"].strip() + "\n"
            log = f"skill {name} add: {change['text'].strip()[:120]}"
        elif action == "remove":
            pos, err = _find_unique(text, change.get("old_text") or "")
            if err:
                return f"skill {name}: {err}"
            start, end = _entry_bounds(text, pos)
            new = text[:start] + text[end:]
            log = f"skill {name} remove: {text[start:end].strip()[:120]}"
        else:
            return f"unknown action {action!r} for skill"
        if len(new) > SKILL_BUDGET and len(new) > len(text or ""):
            return f"skill {name} would have {len(new)} chars, budget {SKILL_BUDGET}"
        self.skills[name] = new
        self.log.append((log, reason))
        return None

    def empty_new_skills(self):
        """Skills created in this run that still have no steps."""
        return [n for n, text in self.skills.items()
                if text is not None and self.original_skills.get(n) is None and not _parse_skill(text)[1].strip()]

    def _drop_empty_skills(self):
        for n, text in list(self.skills.items()):
            if text is not None and not _parse_skill(text)[1].strip():
                if self.original_skills.get(n) is None:
                    del self.skills[n]
                    self.log = [(d, r) for d, r in self.log if not d.startswith(f"skill {n} ")]

    def changed(self):
        return [n for n in FILES if self.files[n] != self.original[n]], \
               [n for n in set(self.skills) | set(self.original_skills) if self.skills.get(n) != self.original_skills.get(n)]

    def commit(self):
        """Writes changed files (snapshot first) and logs to CHANGES.md. Returns the change descriptions."""
        self._drop_empty_skills()
        if self.undo_requested:
            restored = undo_last()
            self.log.append((restored, "requested by the user"))
        files, skills = self.changed()
        if files or skills:
            snap = _path(".history", self.id)
            os.makedirs(os.path.join(snap, "skills"), exist_ok=True)
            manifest = {"source": self.source, "files": files, "skills": skills}
            for n in files:
                _write(os.path.join(snap, FILES[n][0]), self.original[n])
            for n in skills:
                if self.original_skills.get(n) is not None:
                    _write(os.path.join(snap, "skills", f"{n}.md"), self.original_skills[n])
            _write(os.path.join(snap, "manifest.json"), json.dumps(manifest))
            for n in files:
                _write(_path(FILES[n][0]), self.files[n])
            for n in skills:
                path = _path("skills", f"{n}.md")
                if self.skills.get(n) is None:
                    archive = _path("skills", ".archive")
                    os.makedirs(archive, exist_ok=True)
                    if os.path.exists(path):
                        os.replace(path, os.path.join(archive, f"{n}-{self.id}.md"))
                else:
                    _write(path, self.skills[n])
            _prune_history()
        if self.log:
            _log_changes(self.source, self.log)
        return [d for d, _ in self.log]


def _log_changes(source, log):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"\n## {stamp} ({source})\n"]
    for desc, reason in log:
        lines.append(f"- {desc}" + (f" | reason: {reason}" if reason else "") + "\n")
    path = _path("CHANGES.md")
    text = _read(path) or "# Changes\n"
    text += "".join(lines)
    if len(text) > 200_000:  # keep the newest part
        text = "# Changes (older entries dropped)\n\n## " + text[-150_000:].split("\n## ", 1)[-1]
    _write(path, text)


def recent_changes(max_items=6, max_age_days=3):
    """The newest change lines for the prompt ("What did you learn recently?")."""
    items = []
    now = datetime.now()
    for block in _read(_path("CHANGES.md")).split("\n## ")[1:]:
        head, _, body = block.partition("\n")
        try:
            when = datetime.strptime(head[:16], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if (now - when).days > max_age_days:
            continue
        for line in body.splitlines():
            if line.startswith("- "):
                items.append(f"[{head[:16]}] {line[2:].split(' | reason:')[0]}")
    return items[-max_items:]


def _prune_history():
    folder = _path(".history")
    runs = sorted(d for d in os.listdir(folder) if not d.startswith("."))
    for old in runs[:-HISTORY_RUNS]:
        shutil.rmtree(os.path.join(folder, old), ignore_errors=True)


def undo_last():
    """Restores the files of the newest run from its snapshot. Returns a description."""
    folder = _path(".history")
    runs = sorted(d for d in os.listdir(folder) if not d.startswith(".")) if os.path.isdir(folder) else []
    if not runs:
        return "undo: nothing to undo"
    snap = os.path.join(folder, runs[-1])
    try:
        manifest = json.loads(_read(os.path.join(snap, "manifest.json")))
    except ValueError:
        return "undo: snapshot unreadable"
    for n in manifest.get("files", []):
        _write(_path(FILES[n][0]), _read(os.path.join(snap, FILES[n][0])))
    for n in manifest.get("skills", []):
        saved = os.path.join(snap, "skills", f"{n}.md")
        target = _path("skills", f"{n}.md")
        if os.path.exists(saved):
            _write(target, _read(saved))
        elif os.path.exists(target):
            os.remove(target)  # the run had created it
    # mark as undone so the next undo goes one step further back
    os.replace(snap, os.path.join(folder, f".undone-{runs[-1]}"))
    return f"undo: restored the state before the change of {runs[-1]} ({manifest.get('source')})"
