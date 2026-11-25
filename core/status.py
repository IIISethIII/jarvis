"""Jarvis' current state as a small JSON file, read by the web UI (web/server.py).

Only the Jarvis process writes it: main.py calls init(), and update() is a no-op in any other process
(scripts, tests), so a script that uses the learner or MCP never overwrites the daemon's state.
The file carries the writer's pid; readers treat it as stale when that process is gone.

  state    booting | idle | session
  alarm    true while an alarm rings
  learner  name of the running learner job, or null
  mcp      {"servers": {name: true/false}, "tools": n}
  error    last error message (boot or session), with error_at
"""
import json
import os
import threading
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_lock = threading.Lock()
_state = None  # set by init() in the Jarvis process


def path():
    if os.getenv("JARVIS_STATUS_FILE"):
        return os.getenv("JARVIS_STATUS_FILE")
    return os.path.join(os.getenv("JARVIS_DATA_DIR") or os.path.join(BASE_DIR, "data"), ".status.json")


def init(**fields):
    global _state
    with _lock:
        _state = {"pid": os.getpid(), "started": time.time(), "state": "booting", "since": time.time(),
                  "alarm": False, "learner": None, "mcp": {"servers": {}, "tools": None}, "error": None, "error_at": None}
        _state.update(fields)
        _save()


def update(**fields):
    """Merges fields into the state and writes the file. 'state' also sets 'since'."""
    if _state is None:
        return
    with _lock:
        if "state" in fields and fields["state"] != _state.get("state"):
            _state["since"] = time.time()
        if "error" in fields:
            _state["error_at"] = time.time() if fields["error"] else None
        _state.update(fields)
        _save()


def refresh_config(keys, now):
    """After main.py re-read the per-conversation settings: their hashes in 'config' follow the current .env
    (now = settings_schema.fingerprint of it), the others keep the values Jarvis started with."""
    if _state is None:
        return
    with _lock:
        running = dict(_state.get("config") or {})
        for key in keys:
            if key in now:
                running[key] = now[key]
            else:
                running.pop(key, None)
        _state["config"] = running
        _save()


def set_mcp_server(name, online):
    if _state is None:
        return
    with _lock:
        if _state["mcp"]["servers"].get(name) == online:
            return
        _state["mcp"]["servers"][name] = online
        _save()


def _save():
    _state["updated"] = time.time()
    target = path()
    tmp = f"{target}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state, f)
        os.replace(tmp, target)
    except OSError as e:
        print(f"[Status] Could not write {target}: {e}", flush=True)


def read():
    """The state written by Jarvis, with 'alive' = whether the writing process still runs. {} if there is none."""
    try:
        with open(path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    pid = data.get("pid")
    data["alive"] = bool(pid) and _pid_alive(pid)
    return data


def _pid_alive(pid):
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if os.path.isdir("/proc/self"):
        # after a reboot the pid may belong to another program
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return b"main.py" in f.read()
        except OSError:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True
