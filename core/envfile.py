"""Reads and edits the .env file. Pure standard library (the web UI uses it without the Jarvis dependencies).

Parsing follows python-dotenv for the forms used here: KEY=value, optional 'export ', single or double quotes,
'#' comments (a ' #' ends an unquoted value). Editing keeps every other line, comments and order as they are:
  - a changed key is replaced in place;
  - a new key replaces its commented-out line ('#KEY=...', as in .env.example) or is appended;
  - a removed key is commented out, so the default applies again and the line stays findable.
Writes are atomic and the file stays private (0600).
"""
import os
import re
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, ".env")

_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_COMMENTED_RE = re.compile(r"^\s*#\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")
_PLAIN_OK = re.compile(r"^[A-Za-z0-9_\-.,:/@+=*?!%^~\[\]{}()<>|;&]*$")


def _unquote(raw):
    raw = raw.strip()
    if raw[:1] == '"':
        out, i = [], 1
        while i < len(raw):
            c = raw[i]
            if c == "\\" and i + 1 < len(raw):
                nxt = raw[i + 1]
                out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
                i += 2
                continue
            if c == '"':
                break
            out.append(c)
            i += 1
        return "".join(out)
    if raw[:1] == "'":
        out, i = [], 1
        while i < len(raw):
            c = raw[i]
            if c == "\\" and raw[i + 1:i + 2] in ("\\", "'"):
                out.append(raw[i + 1])
                i += 2
                continue
            if c == "'":
                break
            out.append(c)
            i += 1
        return "".join(out)
    m = re.search(r"\s#", raw)
    return (raw[:m.start()] if m else raw).strip()


def parse_line(line):
    """Returns (key, value) for an active assignment, else None."""
    if line.lstrip().startswith("#"):
        return None
    m = _KEY_RE.match(line)
    if not m:
        return None
    return m.group(1), _unquote(m.group(2))


def read(path=DEFAULT_PATH):
    """Dict of the active assignments; the last one wins, like python-dotenv."""
    values = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                kv = parse_line(line.rstrip("\n"))
                if kv:
                    values[kv[0]] = kv[1]
    except FileNotFoundError:
        pass
    return values


def reload(keys, base_env, path=DEFAULT_PATH):
    """Re-applies .env to os.environ for the given keys, as a fresh load_dotenv() would: variables of the real
    environment (base_env, captured before .env was loaded) win, keys removed from .env fall back to the
    default. Used for the settings that apply per conversation. Returns the keys whose value changed."""
    values = read(path)
    changed = []
    for key in keys:
        if key in base_env:
            continue
        new = values.get(key)
        if os.environ.get(key) == new:
            continue
        if new is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = new
        changed.append(key)
    return changed


def quote(value):
    """The value as written to .env: plain when safe, otherwise single-quoted (python-dotenv expands ${...}
    in plain and double-quoted values, not in single-quoted ones)."""
    if _PLAIN_OK.match(value):
        return value
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def update(changes, path=DEFAULT_PATH):
    """Applies {key: value or None}. None comments the key out. Returns the keys that actually changed."""
    for key, value in changes.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"invalid key {key!r}")
        if value is not None and ("\n" in value or "\r" in value):
            raise ValueError(f"{key}: value must be one line")
        if value is not None and "${" in value:
            # python-dotenv expands ${VAR} in every value, quoted or not, and has no escape for it
            raise ValueError(f"{key}: value may not contain '${{'")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = []
    current = {}
    for line in lines:
        kv = parse_line(line)
        if kv:
            current[kv[0]] = kv[1]
    changed = {k for k, v in changes.items() if (v is None and k in current) or (v is not None and current.get(k) != v)}
    if not changed:
        return set()

    out, done = [], set()
    for line in lines:
        kv = parse_line(line)
        key = kv[0] if kv else None
        if key in changed:
            if key in done:
                continue  # drop later duplicates of a key we set
            done.add(key)
            value = changes[key]
            out.append(f"{key}={quote(value)}" if value is not None else f"#{line.lstrip()}")
            continue
        out.append(line)
    # new keys: take the place of their commented-out line, else append
    for key in sorted(changed - done, key=list(changes).index):
        value = changes[key]
        if value is None:
            continue
        for i, line in enumerate(out):
            m = _COMMENTED_RE.match(line)
            if m and m.group(1) == key:
                out[i] = f"{key}={quote(value)}"
                break
        else:
            out.append(f"{key}={quote(value)}")
    _write(path, "\n".join(out) + "\n")
    return changed


def _write(path, text):
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
