"""Jarvis web interface: status, logs, restart and the .env settings. Pure standard library (~15 MB RAM).

Runs by systemd socket activation (deploy/jarvis-web.socket + jarvis-web.service): systemd listens on port 80
and starts this process on the first request; it ends itself after JARVIS_WEB_IDLE_MIN minutes without
requests, so it costs no RAM while nobody has the page open. Because systemd opens the port, port 80 works
without root.

Development, anywhere (no Jarvis dependencies needed):
  python3 -m web.server --port 8080
  python3 -m web.server --set-password      # set or reset the password on the command line
  python3 -m web.server --port 8080 --env-file /tmp/test.env   # try it without touching the real .env

Security: LAN only, plain HTTP. The first visitor sets the password (like Home Assistant's onboarding);
until then only requests to local host names are accepted (against DNS rebinding). Secrets never leave the
server: the page only learns whether a secret is set and its last characters. Sessions are signed cookies
(HttpOnly, SameSite=Strict) keyed by the password hash, so changing the password logs out every browser.
Every state-changing request needs the header 'X-Jarvis: 1', which other sites cannot send.
"""
import argparse
import base64
import getpass
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from core import envfile, settings_schema as schema, status, workspace  # noqa: E402  (stdlib-only modules)

ENV_PATH = envfile.DEFAULT_PATH
STATIC_DIR = os.path.join(ROOT, "web", "static")
SERVICE = "jarvis"
COOKIE = "jarvis_session"
SESSION_DAYS = 30
MAX_BODY = 256 * 1024
MIN_PASSWORD = 8
PBKDF2_ITERATIONS = 100_000
RESTART_WAIT_S = 600  # 'restart when idle' gives up after this long
LOG_NOISE = re.compile(r"ALSA lib|[Jj]ack|JackShm|server socket|server request channel|CUDAExecutionProvider|"
                       r"warnings\.warn")
LOCAL_SUFFIXES = (".local", ".lan", ".home", ".home.arpa", ".internal", ".localdomain")
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                 ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                 ".ico": "image/x-icon", ".json": "application/json"}


# --- State of this process -----------------------------------------------------------

class App:
    def __init__(self):
        self.lock = threading.Lock()
        self.last_request = time.time()
        self.active = 0
        self.restart_pending = None  # {"since": ts} while 'restart when idle' waits
        self.failed_logins = {}  # ip -> [timestamps]
        self.cache = {}  # name -> (time, value)

    def busy(self):
        with self.lock:
            return self.active > 0 or self.restart_pending is not None

    def cached(self, name, max_age, fn):
        now = time.time()
        hit = self.cache.get(name)
        if hit and now - hit[0] < max_age:
            return hit[1]
        value = fn()
        self.cache[name] = (now, value)
        return value


app = App()


def env():
    return envfile.read(ENV_PATH)


# --- Passwords and sessions ------------------------------------------------------------

def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256:{PBKDF2_ITERATIONS}:{_b64(salt)}:{_b64(digest)}"


def check_password(password, stored):
    if not stored:
        return False
    if stored.startswith("pbkdf2_sha256:"):
        try:
            _, iterations, salt, digest = stored.split(":")
            calc = hashlib.pbkdf2_hmac("sha256", password.encode(), _unb64(salt), int(iterations))
            return hmac.compare_digest(calc, _unb64(digest))
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(password.encode(), stored.encode())  # plain text written by hand


def _session_key(stored):
    return hashlib.sha256(b"jarvis-web-session\0" + stored.encode()).digest()


def make_session(stored):
    expires = int(time.time()) + SESSION_DAYS * 86400
    sig = hmac.new(_session_key(stored), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def session_valid(value, stored):
    if not value or not stored or "." not in value:
        return False
    expires, sig = value.split(".", 1)
    if not expires.isdigit() or int(expires) < time.time():
        return False
    good = hmac.new(_session_key(stored), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good)


def set_password(password):
    envfile.update({"JARVIS_WEB_PASSWORD": hash_password(password)}, ENV_PATH)


# --- Jarvis service, system info and logs ---------------------------------------------

def run(cmd, timeout=10):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as e:
        return -1, "", str(e)


def service_info():
    code, out, err = run(["systemctl", "show", SERVICE, "-p", "LoadState", "-p", "ActiveState", "-p", "SubState",
                          "-p", "NRestarts", "-p", "ActiveEnterTimestamp"])
    if code != 0:
        return {"installed": False, "active": "unknown", "error": (err or out).strip()[:200]}
    props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    return {
        "installed": props.get("LoadState") == "loaded",
        "active": props.get("ActiveState", "unknown"),
        "sub": props.get("SubState", ""),
        "restarts": int(props.get("NRestarts") or 0),
        "active_since": props.get("ActiveEnterTimestamp", ""),
    }


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def system_info(jarvis_pid=None):
    mem = {}
    for line in _read("/proc/meminfo").splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree"):
            mem[key] = int(rest.split()[0]) // 1024  # MB
    info = {
        "hostname": socket.gethostname(),
        "mem_total": mem.get("MemTotal"), "mem_available": mem.get("MemAvailable"),
        "swap_total": mem.get("SwapTotal"),
        "swap_used": (mem["SwapTotal"] - mem["SwapFree"]) if "SwapTotal" in mem else None,
        "uptime": float((_read("/proc/uptime").split() or [0])[0]) or None,
        "load": (_read("/proc/loadavg").split() or [None])[0],
        "web_rss": _status_kb("/proc/self/status", "VmRSS"),
    }
    temp = _read("/sys/class/thermal/thermal_zone0/temp").strip()
    if temp.isdigit():
        info["temp"] = int(temp) / 1000
    if jarvis_pid:
        # PSS counts shared pages proportionally: the honest number for Jarvis + its mic process
        info["jarvis_pss"] = sum(filter(None, (_pss(p) for p in [jarvis_pid] + _children(jarvis_pid)))) or None
    return info


def _status_kb(path, field):
    m = re.search(rf"^{field}:\s+(\d+) kB", _read(path), re.M)
    return int(m.group(1)) // 1024 if m else None


def _pss(pid):
    m = re.search(r"^Pss:\s+(\d+) kB", _read(f"/proc/{pid}/smaps_rollup"), re.M)
    return int(m.group(1)) // 1024 if m else None


def _children(pid):
    return [int(c) for c in _read(f"/proc/{pid}/task/{pid}/children").split() if c.isdigit()]


def secret_values(values):
    found = []
    for s in schema.SETTINGS:
        if s.kind == "secret":
            for key in (s.key,) + s.aliases:
                found += [v.strip() for v in values.get(key, "").split(",") if len(v.strip()) >= 8]
    return found


def mask(text, secrets_list):
    for value in sorted(secrets_list, key=len, reverse=True):
        text = text.replace(value, "•••")
        if len(value) >= 16:
            text = text.replace(value[:8], "•••")  # Jarvis logs the first 8 characters of the active key
    return text


def read_logs(lines):
    code, out, err = run(["journalctl", "-u", SERVICE, "-n", str(lines), "--no-pager", "-o", "short-iso"], timeout=15)
    if code != 0 and not out:
        hint = " Add the user to the group 'adm' or 'systemd-journal' to read the log." if "permission" in err.lower() else ""
        return {"lines": [], "error": (err.strip() or "journalctl failed") + hint}
    keep = [l for l in out.splitlines() if not LOG_NOISE.search(l)]
    return {"lines": mask("\n".join(keep), secret_values(env())).splitlines()}


def restart_jarvis():
    code, out, err = run(["sudo", "-n", "systemctl", "restart", "--no-block", SERVICE])
    if code != 0:
        msg = (err or out).strip()
        if "password" in msg.lower() or "sudo" in msg.lower():
            msg += " (see deploy/jarvis-web.sudoers: the web user may restart the jarvis service without a password)"
        raise RuntimeError(msg or "systemctl failed")
    print("[Web] Jarvis restarted.", flush=True)


def jarvis_busy(st):
    """Why a restart now would interrupt something, or None."""
    if st.get("state") == "session":
        return "in a conversation"
    if st.get("alarm"):
        return "an alarm is ringing"
    if st.get("learner"):
        return "the learner is updating the workspace"
    return None


def restart_when_idle():
    def wait():
        deadline = time.time() + RESTART_WAIT_S
        try:
            while time.time() < deadline:
                st = status.read()
                if not st.get("alive") or not jarvis_busy(st):
                    restart_jarvis()
                    return
                time.sleep(2)
            print("[Web] Restart when idle: gave up after waiting.", flush=True)
        except RuntimeError as e:
            print(f"[Web] Restart failed: {e}", flush=True)
        finally:
            with app.lock:
                app.restart_pending = None
    with app.lock:
        if app.restart_pending:
            return
        app.restart_pending = {"since": time.time()}
    threading.Thread(target=wait, daemon=True, name="restart").start()


# --- Settings ------------------------------------------------------------------------

def secret_hint(value):
    parts = [p.strip() for p in value.split(",") if p.strip()]
    if not parts:
        return ""
    tails = ["…" + p[-4:] if len(p) >= 12 else "set" for p in parts]
    return ", ".join(tails)


def settings_payload(values=None):
    values = env() if values is None else values
    groups = []
    for gid, title, description in schema.GROUPS:
        items = []
        for s in schema.SETTINGS:
            if s.group != gid or not s.ui:
                continue
            raw, via = values.get(s.key), None
            if raw is None:
                via = next((a for a in s.aliases if a in values), None)
                raw = values.get(via) if via else None
            item = {
                "key": s.key, "label": s.label, "help": s.help, "kind": s.kind, "default": s.default,
                "min": s.min, "max": s.max, "choices": list(s.choices), "suggest": list(s.suggest),
                "applies": s.applies, "alias": via,
                # an empty secret (GEMINI_KEYS= from .env.example) is no secret
                "set": bool(raw) if s.kind == "secret" else raw is not None,
            }
            if s.kind == "secret":
                item["hint"] = secret_hint(raw or "")
            else:
                item["value"] = raw
            items.append(item)
        if items:
            groups.append({"id": gid, "title": title, "description": description, "settings": items})
    known = set(schema.BY_KEY)
    other = []
    for key in sorted(values):
        if key in known:
            continue
        if key in schema.ALIASES:
            main = schema.ALIASES[key]
            note = f"older name of {main}" + (", not used because it is set" if main in values else ", still used")
        else:
            note = "not used by Jarvis"
        other.append({"key": key, "note": note})
    return {"groups": groups, "other": other}


def apply_settings(changes):
    """Validates {key: value or None} and writes .env. Returns (changed keys, errors)."""
    if not isinstance(changes, dict):
        return [], {"_": "expected an object"}
    values = env()
    updates, errors = {}, {}
    for key, value in changes.items():
        s = schema.BY_KEY.get(key)
        if value is not None and not isinstance(value, str):
            errors[key] = "must be text"
            continue
        if s is None:
            if value is None and key in values:
                updates[key] = None  # remove an unknown or old key
            else:
                errors[key] = "unknown setting"
            continue
        if not s.ui:
            errors[key] = "can't be changed here"
            continue
        if value is None:
            updates[key] = None
            continue
        normalized, err = schema.validate(s, value)
        if err:
            errors[key] = err
        else:
            updates[key] = normalized or None
    if errors:
        return [], errors
    changed = envfile.update(updates, ENV_PATH) if updates else set()
    if changed:
        print(f"[Web] Settings changed: {', '.join(sorted(changed))}", flush=True)
    return sorted(changed), {}


def pending_changes(st):
    """Settings that differ from what the running Jarvis loaded and need a restart (labels), or None if unknown.
    Settings with applies="session" are left out: Jarvis re-reads them before every conversation."""
    running = st.get("config")
    if not st.get("alive") or running is None or st.get("state") == "unconfigured":
        return None
    now = schema.fingerprint(env())
    session = set(schema.session_keys())
    keys = sorted(k for k in set(running) | set(now) if running.get(k) != now.get(k) and k not in session)
    return [schema.BY_KEY[k].label if k in schema.BY_KEY else k for k in keys]


def needs_setup(values=None):
    values = env() if values is None else values
    return not any(values.get(k) for k in ("GEMINI_KEYS",) + schema.BY_KEY["GEMINI_KEYS"].aliases)


def onboarding_payload():
    st = status.read()
    values = env()
    return {
        "questions": [{"key": k, "question": q} for k, q in workspace.ONBOARDING_QUESTIONS],
        "pending": workspace.pending_onboarding(),
        "learner": st.get("learner") if st.get("alive") else None,
        "learner_on": values.get("JARVIS_LEARNER", "1") != "0",
        "needs_setup": needs_setup(values),
    }


def status_payload():
    st = status.read()
    svc = app.cached("service", 2, service_info)
    alive = bool(st.get("alive"))
    with app.lock:
        pending = dict(app.restart_pending) if app.restart_pending else None
    return {
        "jarvis": {
            "alive": alive,
            "state": st.get("state") if alive else None,
            "since": st.get("since"), "started": st.get("started"),
            "alarm": bool(st.get("alarm")) and alive, "learner": st.get("learner") if alive else None,
            "mcp": st.get("mcp") if alive else None,
            "error": st.get("error"), "error_at": st.get("error_at"),
            "test_mode": st.get("test_mode"),
            "busy": jarvis_busy(st) if alive else None,
        },
        "service": svc,
        "restart_pending": pending,
        "pending_changes": pending_changes(st),
        "timers": timers_payload(),
        "system": app.cached("system", 4, lambda: system_info(st.get("pid") if alive else None)),
        "name": env().get("JARVIS_NAME") or schema.BY_KEY["JARVIS_NAME"].default,
        "needs_setup": needs_setup(),
        "now": time.time(),
    }


# --- Workspace (personality, rules, memory, skills) -----------------------------------------

FILE_INFO = {
    "SOUL": ("Personality", "How Jarvis talks and behaves: tone, humour, style."),
    "AGENTS": ("House rules", "How Jarvis should act in your home: what to do or avoid, devices, routines."),
    "USER": ("About you", "How Jarvis addresses you and what it should keep in mind about you."),
    "MEMORY": ("Memory", "Facts Jarvis should know. One '- ' line per entry; '## ' headings group them."),
}
MAX_FILE_CHARS = 50_000


def workspace_payload():
    workspace.ensure()
    files = []
    for name, (fname, budget) in workspace.FILES.items():
        text = workspace.read_file(name)
        title, description = FILE_INFO.get(name, (name, ""))
        files.append({"name": name, "file": fname, "title": title, "description": description, "budget": budget,
                      "text": text, "hash": workspace.text_hash(text)})
    usage = workspace.skill_usage()
    skills = [{"name": s["name"], "description": s["description"], "always": s["always"], "body": s["body"],
               "hash": workspace.text_hash(s["text"]), "last_used": usage.get(s["name"])}
              for s in workspace.list_skills()]
    st = status.read()
    return {
        "files": files, "skills": skills, "skill_budget": workspace.SKILL_BUDGET, "max_skills": workspace.MAX_SKILLS,
        "notes": workspace.read_notes(), "history": workspace.history(25), "undo": workspace.undo_available(),
        "learner": st.get("learner") if st.get("alive") else None,
    }


def workspace_change(fn):
    """Runs fn() under the workspace lock and maps its errors to HTTP errors."""
    try:
        with workspace.lock(wait=False):
            return fn()
    except workspace.Busy as e:
        raise HTTPError(409, f"Not saved: {e}. Try again in a minute.", busy=True)
    except workspace.Conflict as e:
        raise HTTPError(409, f"Not saved: {e} (probably by the learner). Reload to see the current version.",
                        conflict=True)
    except ValueError as e:
        raise HTTPError(400, str(e))


# --- MCP servers ---------------------------------------------------------------------------

MCP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$")


def mcp_read():
    """(whole config dict, servers dict, error) of core/mcp_config.json."""
    try:
        with open(schema.MCP_CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, {}, None
    except (OSError, ValueError) as e:
        return None, {}, f"core/mcp_config.json is not valid JSON: {e}"
    if not isinstance(data, dict):
        return None, {}, "core/mcp_config.json must contain an object"
    servers = data.get("servers") or data.get("mcpServers") or {}
    return data, servers if isinstance(servers, dict) else {}, None


def mcp_payload():
    data, servers, error = mcp_read()
    st = status.read()
    live = (st.get("mcp") or {}).get("servers", {}) if st.get("alive") else {}
    items = []
    for name, info in servers.items():
        info = info if isinstance(info, dict) else {}
        items.append({"name": name, "url": info.get("url", ""), "type": info.get("type", "sse"),
                      "include": info.get("include"), "exclude": info.get("exclude") or [],
                      "online": live.get(name)})
    return {"exists": data is not None, "error": error, "servers": items,
            "tools": (st.get("mcp") or {}).get("tools") if st.get("alive") else None}


def _patterns(value, field):
    if value is None:
        return None
    if isinstance(value, str):
        value = [p.strip() for p in value.split(",")]
    if not isinstance(value, list) or not all(isinstance(p, str) for p in value):
        raise ValueError(f"{field} must be a list of tool name patterns")
    return [p.strip() for p in value if p.strip()]


def mcp_save(items):
    if not isinstance(items, list):
        raise ValueError("expected a list of servers")
    data, old, error = mcp_read()
    if error:
        raise ValueError(error + " Fix or delete the file first.")
    servers, errors = {}, {}
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError("invalid server entry")
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        kind = item.get("type") or "sse"
        if not MCP_NAME_RE.match(name):
            errors[str(i)] = "name: letters, digits, '-', '_' or '.', at most 40 characters"
            continue
        if name in servers:
            errors[str(i)] = f"the name {name} is used twice"
            continue
        if not url.startswith(("http://", "https://")) or any(c.isspace() for c in url):
            errors[str(i)] = "the URL must start with http:// or https://"
            continue
        if kind not in ("sse", "stateless"):
            errors[str(i)] = "type must be sse or stateless"
            continue
        try:
            include = _patterns(item.get("include"), "include")
            exclude = _patterns(item.get("exclude"), "exclude") or []
        except ValueError as e:
            errors[str(i)] = str(e)
            continue
        entry = dict(old.get(name) or {})  # keeps keys this page doesn't know
        entry.update({"url": url, "type": kind})
        for key, value in (("include", include), ("exclude", exclude)):
            if value:
                entry[key] = value
            else:
                entry.pop(key, None)
        if include == []:
            entry["include"] = []  # explicit: no tools from this server
        servers[name] = entry
    if errors:
        raise HTTPError(400, "Some servers are invalid.", errors=errors)
    data = dict(data or {})
    data.pop("mcpServers", None)
    data["servers"] = servers
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    folder = os.path.dirname(schema.MCP_CONFIG_PATH)
    tmp = os.path.join(folder, ".mcp_config.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o600)  # URLs may carry tokens
    os.replace(tmp, schema.MCP_CONFIG_PATH)
    print(f"[Web] MCP servers saved: {', '.join(servers) or 'none'}", flush=True)


# --- Connection tests ------------------------------------------------------------------------

def http_probe(method, url, headers=None, body=None, timeout=8, limit=65536):
    """(status or None, text, error) of one HTTP request, without raising."""
    req = urllib.request.Request(url, method=method, headers=headers or {},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(limit).decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        try:
            text = e.read(limit).decode("utf-8", "replace")
        except OSError:
            text = ""
        return e.code, text, None
    except (urllib.error.URLError, OSError, ValueError) as e:
        reason = getattr(e, "reason", e)
        return None, "", f"not reachable: {reason}"


def _json_error(text):
    try:
        data = json.loads(text)
    except ValueError:
        return text.strip()[:160]
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return str(err.get("message") or err.get("status") or err)[:200]
    return str(err or data)[:200]


def test_gemini(keys, model):
    keys = [k.strip() for k in (keys or "").split(",") if k.strip()]
    if not keys:
        return [{"label": "Gemini", "ok": False, "detail": "no API key set"}]
    results = []
    for key in keys[:6]:
        label = f"Key …{key[-4:]}" if len(key) >= 8 else "Key"
        code, text, err = http_probe("GET", f"https://generativelanguage.googleapis.com/v1beta/models/{model}",
                                     {"x-goog-api-key": key})
        if err:
            results.append({"label": label, "ok": False, "detail": err})
        elif code == 200:
            results.append({"label": label, "ok": True, "detail": f"works, model {model} available"})
        elif code == 404:
            results.append({"label": label, "ok": False, "detail": f"key works, but model {model} was not found"})
        elif code == 429:
            results.append({"label": label, "ok": True, "detail": "valid, but its quota is used up right now"})
        else:
            results.append({"label": label, "ok": False, "detail": f"rejected (HTTP {code}): {_json_error(text)}"})
    return results


def test_home_assistant(url, token):
    if not url or not token:
        return [{"label": "Home Assistant", "ok": False, "detail": "URL and token are needed"}]
    headers = {"Authorization": f"Bearer {token}"}
    code, text, err = http_probe("GET", f"{url.rstrip('/')}/api/config", headers)
    if err:
        return [{"label": "Home Assistant", "ok": False, "detail": err}]
    if code == 200:
        try:
            cfg = json.loads(text)
            detail = f"connected: {cfg.get('location_name', 'Home Assistant')}, version {cfg.get('version', '?')}"
        except ValueError:
            detail = "connected"
        return [{"label": "Home Assistant", "ok": True, "detail": detail}]
    if code in (401, 403):
        return [{"label": "Home Assistant", "ok": False, "detail": "the token was rejected"}]
    return [{"label": "Home Assistant", "ok": False, "detail": f"HTTP {code}: {_json_error(text)}"}]


def test_openclaw(url, token):
    if not url or not token:
        return [{"label": "OpenClaw", "ok": False, "detail": "URL and token are needed"}]
    code, text, err = http_probe("GET", f"{url.rstrip('/')}/v1/models", {"Authorization": f"Bearer {token}"})
    if err:
        return [{"label": "OpenClaw", "ok": False, "detail": err}]
    if code in (401, 403):
        return [{"label": "OpenClaw", "ok": False, "detail": "the token was rejected"}]
    if code == 200:
        return [{"label": "OpenClaw", "ok": True, "detail": "connected"}]
    return [{"label": "OpenClaw", "ok": True, "detail": f"reachable (HTTP {code} for /v1/models)"}]


def test_mcp(url, kind):
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "detail": "the URL must start with http:// or https://"}
    start = time.time()
    if kind == "stateless":
        code, text, err = http_probe("POST", url, {"Content-Type": "application/json",
                                                   "Accept": "application/json, text/event-stream"},
                                     {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        if err:
            return {"ok": False, "detail": err}
        try:
            tools = json.loads(text).get("result", {}).get("tools")
        except (ValueError, AttributeError):
            tools = None
        if code == 200 and isinstance(tools, list):
            return {"ok": True, "detail": f"{len(tools)} tools ({(time.time() - start) * 1000:.0f} ms)"}
        return {"ok": False, "detail": f"HTTP {code}, no tool list: {text.strip()[:120]}"}
    # SSE: the server's first event ("event: endpoint" + "data: <url>") names the endpoint for requests.
    # The stream stays open, so stop at that data line (lines may end with \r\n).
    req = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
    seen = b""
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            while len(seen) < 4096:
                line = r.readline()
                if not line:
                    break
                seen += line
                if line.startswith(b"data:") and b"endpoint" in seen:
                    break
    except urllib.error.HTTPError as e:
        return {"ok": False, "detail": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError, ValueError) as e:
        if not seen:
            return {"ok": False, "detail": f"not reachable: {getattr(e, 'reason', e)}"}
    if b"endpoint" in seen:
        return {"ok": True, "detail": f"MCP server answers ({(time.time() - start) * 1000:.0f} ms)"}
    return {"ok": False, "detail": "reachable, but it doesn't speak MCP over SSE (try type 'stateless')"}


def connection_test(what, typed):
    """Tests with the values typed into the form, else the saved ones."""
    values = env()
    typed = {k: v for k, v in (typed or {}).items() if isinstance(v, str) and v.strip()}

    def get(key):
        s = schema.BY_KEY[key]
        if key in typed:
            return typed[key].strip()
        for k in (key,) + s.aliases:
            if values.get(k):
                return values[k]
        return s.default

    if what == "gemini":
        return test_gemini(get("GEMINI_KEYS"), get("GEMINI_LIVE_MODEL"))
    if what == "home_assistant":
        return test_home_assistant(get("HA_URL"), get("HA_TOKEN"))
    if what == "openclaw":
        return test_openclaw(get("OPENCLAW_URL"), get("OPENCLAW_TOKEN"))
    raise HTTPError(400, f"nothing to test for {what!r}")


# --- Timers ------------------------------------------------------------------------------------

def timers_payload():
    data_dir = os.getenv("JARVIS_DATA_DIR") or os.path.join(ROOT, "data")
    try:
        with open(os.path.join(data_dir, "timers.json"), encoding="utf-8") as f:
            timers = json.load(f).get("timers") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [{"label": t.get("label", ""), "kind": t.get("kind", "timer"), "due": t.get("due")}
            for t in timers if isinstance(t, dict)]


# --- HTTP ------------------------------------------------------------------------------

class HTTPError(Exception):
    def __init__(self, code, message, **extra):
        super().__init__(message)
        self.code, self.message, self.extra = code, message, extra


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "JarvisWeb"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass  # no access log in the journal

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def _handle(self, method):
        with app.lock:
            app.active += 1
            app.last_request = time.time()
        try:
            url = urllib.parse.urlsplit(self.path)
            self.query = urllib.parse.parse_qs(url.query)
            values = env()
            self.stored_password = values.get("JARVIS_WEB_PASSWORD", "")
            if not self.stored_password and not self._local_host():
                raise HTTPError(403, "Use the device's IP address or its .local name until a password is set.")
            if url.path.startswith("/api/"):
                self._api(method, url.path)
            elif method == "GET":
                self._static(url.path)
            else:
                raise HTTPError(405, "method not allowed")
        except HTTPError as e:
            self._json({"error": e.message, **e.extra}, e.code)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            try:
                self._json({"error": "internal error, see journalctl -u jarvis-web"}, 500)
            except OSError:
                pass
        finally:
            with app.lock:
                app.active -= 1
                app.last_request = time.time()

    # helpers

    def _local_host(self):
        host = (self.headers.get("Host") or "").strip().lower()
        if host.startswith("["):
            host = host[1:host.find("]")] if "]" in host else host
        else:
            host = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        if not host:
            return False
        try:
            ipaddress.ip_address(host)
            return True
        except ValueError:
            pass
        return host in ("localhost", socket.gethostname().lower()) or host.endswith(LOCAL_SUFFIXES) \
            or "." not in host

    def _headers(self, code, ctype, length, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; style-src 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def _json(self, data, code=200, cookie=None):
        body = json.dumps(data).encode()
        extra = {"Cache-Control": "no-store"}
        if cookie is not None:
            extra["Set-Cookie"] = cookie
        self._headers(code, "application/json", len(body), extra)
        if self.command != "HEAD":
            self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise HTTPError(413, "request too large")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPError(400, "invalid JSON")
        if not isinstance(data, dict):
            raise HTTPError(400, "expected a JSON object")
        return data

    def _cookie(self, value, max_age):
        return f"{COOKIE}={value}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Strict"

    def _authenticated(self):
        for part in (self.headers.get("Cookie") or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and session_valid(value, self.stored_password):
                return True
        return False

    def _client_ip(self):
        return self.client_address[0] if self.client_address else "?"

    # static files

    def _static(self, path):
        name = "index.html" if path in ("/", "/index.html") else path[len("/static/"):] if path.startswith("/static/") else None
        if not name or not re.fullmatch(r"[a-z0-9_-]+\.[a-z0-9]+", name):
            raise HTTPError(404, "not found")
        file = os.path.join(STATIC_DIR, name)
        try:
            with open(file, "rb") as f:
                body = f.read()
        except OSError:
            raise HTTPError(404, "not found")
        ctype = CONTENT_TYPES.get(os.path.splitext(name)[1], "application/octet-stream")
        self._headers(200, ctype, len(body), {"Cache-Control": "no-cache"})
        self.wfile.write(body)

    # API

    def _api(self, method, path):
        if method != "GET":
            # other sites can't set this header without a CORS preflight, which this server never allows
            if self.headers.get("X-Jarvis") != "1":
                raise HTTPError(403, "missing X-Jarvis header")
            origin = self.headers.get("Origin")
            if origin and urllib.parse.urlsplit(origin).netloc.lower() != (self.headers.get("Host") or "").lower():
                raise HTTPError(403, "cross-origin request")
        public = {("GET", "/api/session"): self.api_session, ("POST", "/api/setup"): self.api_setup,
                  ("POST", "/api/login"): self.api_login, ("POST", "/api/logout"): self.api_logout}
        private = {("GET", "/api/status"): self.api_status, ("GET", "/api/settings"): self.api_settings,
                   ("PUT", "/api/settings"): self.api_save_settings, ("GET", "/api/logs"): self.api_logs,
                   ("POST", "/api/restart"): self.api_restart, ("POST", "/api/password"): self.api_password,
                   ("POST", "/api/test"): self.api_test,
                   ("GET", "/api/workspace"): self.api_workspace,
                   ("PUT", "/api/workspace/file"): self.api_workspace_file,
                   ("PUT", "/api/workspace/skill"): self.api_workspace_skill,
                   ("POST", "/api/workspace/skill/delete"): self.api_workspace_skill_delete,
                   ("POST", "/api/workspace/undo"): self.api_workspace_undo,
                   ("GET", "/api/mcp"): self.api_mcp, ("PUT", "/api/mcp"): self.api_mcp_save,
                   ("POST", "/api/mcp/test"): self.api_mcp_test,
                   ("GET", "/api/onboarding"): self.api_onboarding,
                   ("POST", "/api/onboarding"): self.api_onboarding_queue}
        route = (method, path)
        if route in public:
            return public[route]()
        if route in private:
            if not self._authenticated():
                raise HTTPError(401, "login required", password_set=bool(self.stored_password))
            return private[route]()
        raise HTTPError(404, "not found")

    def api_session(self):
        authenticated = self._authenticated()
        self._json({"password_set": bool(self.stored_password), "authenticated": authenticated,
                    "name": env().get("JARVIS_NAME") or schema.BY_KEY["JARVIS_NAME"].default,
                    "needs_setup": needs_setup() if authenticated else None})

    def api_setup(self):
        if self.stored_password:
            raise HTTPError(409, "a password is already set")
        password = str(self._body().get("password") or "")
        if len(password) < MIN_PASSWORD:
            raise HTTPError(400, f"the password needs at least {MIN_PASSWORD} characters")
        set_password(password)
        stored = env().get("JARVIS_WEB_PASSWORD", "")
        print(f"[Web] Password set from {self._client_ip()}.", flush=True)
        self._json({"ok": True}, cookie=self._cookie(make_session(stored), SESSION_DAYS * 86400))

    def api_login(self):
        ip = self._client_ip()
        now = time.time()
        with app.lock:
            recent = [t for t in app.failed_logins.get(ip, []) if now - t < 600]
            app.failed_logins[ip] = recent
        if len(recent) >= 10:
            raise HTTPError(429, "too many wrong passwords, wait 10 minutes")
        password = str(self._body().get("password") or "")
        if not check_password(password, self.stored_password):
            with app.lock:
                app.failed_logins.setdefault(ip, []).append(now)
            time.sleep(1)
            raise HTTPError(401, "wrong password", password_set=bool(self.stored_password))
        with app.lock:
            app.failed_logins.pop(ip, None)
        self._json({"ok": True}, cookie=self._cookie(make_session(self.stored_password), SESSION_DAYS * 86400))

    def api_logout(self):
        self._json({"ok": True}, cookie=self._cookie("", 0))

    def api_password(self):
        body = self._body()
        if not check_password(str(body.get("current") or ""), self.stored_password):
            time.sleep(1)
            raise HTTPError(400, "the current password is wrong")
        new = str(body.get("new") or "")
        if len(new) < MIN_PASSWORD:
            raise HTTPError(400, f"the password needs at least {MIN_PASSWORD} characters")
        set_password(new)
        print(f"[Web] Password changed from {self._client_ip()}.", flush=True)
        # the old sessions are invalid now: hand this browser a new one
        self._json({"ok": True}, cookie=self._cookie(make_session(env()["JARVIS_WEB_PASSWORD"]), SESSION_DAYS * 86400))

    def api_status(self):
        self._json(status_payload())

    def api_settings(self):
        self._json(settings_payload())

    def api_save_settings(self):
        changed, errors = apply_settings(self._body().get("changes"))
        if errors:
            raise HTTPError(400, "some values are invalid", errors=errors)
        self._json({"changed": changed, **settings_payload(), "pending_changes": pending_changes(status.read())})

    def api_logs(self):
        try:
            lines = max(10, min(2000, int((self.query.get("lines") or ["200"])[0])))
        except ValueError:
            lines = 200
        self._json(read_logs(lines))

    def api_test(self):
        body = self._body()
        self._json({"results": connection_test(body.get("what"), body.get("values"))})

    def api_workspace(self):
        self._json(workspace_payload())

    def api_workspace_file(self):
        body = self._body()
        name, text, expected = body.get("name"), body.get("text"), body.get("hash")
        if name not in workspace.FILES or not isinstance(text, str):
            raise HTTPError(400, "expected name and text")
        if len(text) > MAX_FILE_CHARS:
            raise HTTPError(400, f"at most {MAX_FILE_CHARS} characters")
        changed = workspace_change(lambda: workspace.edit_by_hand(
            "web UI", files={name: text}, expected={name: expected} if expected else None))
        if changed:
            print(f"[Web] Workspace: {name} edited.", flush=True)
        self._json({"changed": changed, **workspace_payload()})

    def api_workspace_skill(self):
        body = self._body()
        name = str(body.get("name") or "").strip().lower()
        description = str(body.get("description") or "").strip()
        steps = str(body.get("body") or "").strip()
        if not description or "\n" in description:
            raise HTTPError(400, "the skill needs a one-line description: when to use it")
        if not steps:
            raise HTTPError(400, "the skill needs steps")
        text = workspace.skill_text(name, description, steps, bool(body.get("always")))
        if len(text) > workspace.SKILL_BUDGET * 3:
            raise HTTPError(400, f"too long (budget {workspace.SKILL_BUDGET} characters)")
        expected = {f"skill:{name}": body.get("hash") or None}
        changed = workspace_change(lambda: workspace.edit_by_hand("web UI", skills={name: text}, expected=expected))
        if changed:
            print(f"[Web] Workspace: skill {name} saved.", flush=True)
        self._json({"changed": changed, **workspace_payload()})

    def api_workspace_skill_delete(self):
        body = self._body()
        name = str(body.get("name") or "")
        expected = {f"skill:{name}": body.get("hash")} if body.get("hash") else None
        changed = workspace_change(lambda: workspace.edit_by_hand("web UI", skills={name: None}, expected=expected))
        print(f"[Web] Workspace: skill {name} deleted.", flush=True)
        self._json({"changed": changed, **workspace_payload()})

    def api_workspace_undo(self):
        result = workspace_change(workspace.undo_last)
        print(f"[Web] Workspace {result}", flush=True)
        self._json({"result": result, **workspace_payload()})

    def api_mcp(self):
        self._json(mcp_payload())

    def api_mcp_save(self):
        try:
            mcp_save(self._body().get("servers"))
        except ValueError as e:
            raise HTTPError(400, str(e))
        self._json({**mcp_payload(), "pending_changes": pending_changes(status.read())})

    def api_mcp_test(self):
        body = self._body()
        self._json(test_mcp(str(body.get("url") or "").strip(), body.get("type") or "sse"))

    def api_onboarding(self):
        self._json(onboarding_payload())

    def api_onboarding_queue(self):
        answers = self._body().get("answers")
        keys = {k for k, _ in workspace.ONBOARDING_QUESTIONS}
        if not isinstance(answers, dict) or not set(answers) <= keys:
            raise HTTPError(400, "unexpected answers")
        answers = {k: str(v or "").strip()[:2000] for k, v in answers.items()}
        if not workspace.onboarding_text(answers):
            raise HTTPError(400, "no answers")
        workspace.ensure()
        workspace.queue_onboarding(answers)
        print("[Web] Setup answers queued for the learner.", flush=True)
        self._json(onboarding_payload())

    def api_restart(self):
        when = self._body().get("when", "now")
        svc = service_info()
        if not svc.get("installed"):
            raise HTTPError(409, f"{SERVICE}.service is not installed on this machine")
        st = status.read()
        if st.get("alive") and st.get("state") == "unconfigured" and not needs_setup():
            return self._json({"ok": True, "waiting": None})  # it starts by itself within 5 s
        if when == "idle" and st.get("alive") and jarvis_busy(st):
            restart_when_idle()
            return self._json({"ok": True, "waiting": jarvis_busy(st)})
        try:
            restart_jarvis()
        except RuntimeError as e:
            raise HTTPError(500, str(e))
        app.cache.pop("service", None)
        self._json({"ok": True})


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16


# --- Startup ---------------------------------------------------------------------------

def _systemd_socket():
    """The listening socket systemd passed (socket activation), or None."""
    if os.getenv("LISTEN_PID") != str(os.getpid()) or int(os.getenv("LISTEN_FDS") or 0) < 1:
        return None
    for key in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(key, None)
    return socket.socket(fileno=3)


def _idle_watch(server, idle_s):
    while True:
        time.sleep(min(15, idle_s))
        with app.lock:
            idle = time.time() - app.last_request
            busy = app.active > 0 or app.restart_pending is not None
        if not busy and idle >= idle_s:
            print(f"[Web] No requests for {idle_s / 60:g} min, exiting (systemd starts it again on the next visit).",
                  flush=True)
            server.shutdown()
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--idle-min", type=float, default=None,
                        help="exit after this many idle minutes (default: JARVIS_WEB_IDLE_MIN with socket activation, "
                             "else never)")
    parser.add_argument("--set-password", action="store_true", help="set the web password and exit")
    parser.add_argument("--env-file", help="another .env file (development and tests)")
    args = parser.parse_args()

    global ENV_PATH
    if args.env_file:
        ENV_PATH = os.path.abspath(args.env_file)

    values = env()
    # the status file and data paths may be moved in .env
    for key in ("JARVIS_DATA_DIR", "JARVIS_WORKSPACE", "JARVIS_STATUS_FILE", "JARVIS_MCP_CONFIG"):
        if values.get(key) and not os.getenv(key):
            os.environ[key] = values[key]
    if os.getenv("JARVIS_MCP_CONFIG"):
        schema.MCP_CONFIG_PATH = schema.CONFIG_FILES["MCP servers"] = os.getenv("JARVIS_MCP_CONFIG")

    if args.set_password:
        password = getpass.getpass("New web password: ")
        if len(password) < MIN_PASSWORD:
            sys.exit(f"The password needs at least {MIN_PASSWORD} characters.")
        if getpass.getpass("Repeat: ") != password:
            sys.exit("The passwords differ.")
        set_password(password)
        print("Password set. Every browser has to log in again.")
        return

    sock = _systemd_socket()
    if sock is not None:
        server = Server(None, Handler, bind_and_activate=False)
        server.socket.close()
        server.socket = sock
        server.server_address = sock.getsockname()
        default_idle = values.get("JARVIS_WEB_IDLE_MIN") or schema.BY_KEY["JARVIS_WEB_IDLE_MIN"].default
        idle_min = args.idle_min if args.idle_min is not None else float(default_idle)
        where = f"systemd socket {server.server_address[:2]}"
    else:
        server = Server((args.host, args.port), Handler)
        idle_min = args.idle_min or 0
        where = f"http://{args.host}:{args.port}/"
    if idle_min > 0:
        threading.Thread(target=_idle_watch, args=(server, idle_min * 60), daemon=True, name="idle").start()
    print(f"[Web] Serving on {where}" + (f", exits after {idle_min:g} idle min" if idle_min > 0 else ""), flush=True)
    if not values.get("JARVIS_WEB_PASSWORD"):
        print("[Web] No password set yet: the first visitor sets it.", flush=True)
    try:
        server.serve_forever(poll_interval=1)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
