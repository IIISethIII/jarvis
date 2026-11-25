"""web/server.py over real HTTP: first-run password, login, CSRF and host checks, settings, no leaked secrets.

Pure standard library, runs anywhere:  python -m unittest discover tests/unit
The server runs in a thread against a temporary .env and status file.
"""
import http.client
import http.server
import json
import os
import shutil
import tempfile
import threading
import time
import unittest

from core import envfile, settings_schema, workspace
from web import server as web

SECRET_KEY = "AIzaTestSecretKey1234567890abcdef"
ENV = f"""GEMINI_KEYS={SECRET_KEY}
JARVIS_NAME=Friday
#JARVIS_IDLE_TIMEOUT=15
GOOGLE_TTS_KEY=old-v2-key-1234567890
"""


class WebServerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.env_path = os.path.join(self.dir, ".env")
        with open(self.env_path, "w", encoding="utf-8") as f:
            f.write(ENV)
        self.old_env_path = web.ENV_PATH
        web.ENV_PATH = self.env_path
        os.environ["JARVIS_STATUS_FILE"] = os.path.join(self.dir, "status.json")
        os.environ["JARVIS_WORKSPACE"] = os.path.join(self.dir, "workspace")
        self.old_mcp_path = settings_schema.MCP_CONFIG_PATH
        settings_schema.MCP_CONFIG_PATH = os.path.join(self.dir, "mcp_config.json")
        web.app.failed_logins.clear()
        self.httpd = web.Server(("127.0.0.1", 0), web.Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.cookie = None

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        web.ENV_PATH = self.old_env_path
        settings_schema.MCP_CONFIG_PATH = self.old_mcp_path
        os.environ.pop("JARVIS_STATUS_FILE", None)
        os.environ.pop("JARVIS_WORKSPACE", None)
        shutil.rmtree(self.dir)

    def request(self, method, path, body=None, host="127.0.0.1", csrf=True, cookie=True, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        hdrs = {"Host": host}
        if method != "GET" and csrf:
            hdrs["X-Jarvis"] = "1"
        if cookie and self.cookie:
            hdrs["Cookie"] = self.cookie
        hdrs.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            hdrs["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=hdrs)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        set_cookie = r.getheader("Set-Cookie")
        if set_cookie and set_cookie.startswith(web.COOKIE + "="):
            value = set_cookie.split(";")[0]
            self.cookie = value if value != f"{web.COOKIE}=" else None
        ctype = r.getheader("Content-Type") or ""
        return r.status, (json.loads(raw) if "json" in ctype else raw), r

    def setup_password(self, password="correct horse"):
        status, data, _ = self.request("POST", "/api/setup", {"password": password})
        self.assertEqual(status, 200, data)

    # --- tests

    def test_first_run_only_on_local_names(self):
        self.assertEqual(self.request("GET", "/api/session", host="evil.example.com")[0], 403)
        for host in ("127.0.0.1", "192.168.1.50:80", "jarvis.local", "jarvis", "[::1]:80"):
            status, data, _ = self.request("GET", "/api/session", host=host)
            self.assertEqual(status, 200, host)
            self.assertFalse(data["password_set"])
            self.assertEqual(data["name"], "Friday")
        # once a password is set, any host name works (e.g. a Tailscale name)
        self.setup_password()
        self.assertEqual(self.request("GET", "/api/session", host="jarvis.tail1234.ts.net")[0], 200)

    def test_setup_and_login(self):
        self.assertEqual(self.request("POST", "/api/setup", {"password": "short"})[0], 400)
        self.assertEqual(self.request("POST", "/api/setup", {"password": "long enough"}, csrf=False)[0], 403)
        self.setup_password()
        stored = envfile.read(self.env_path)["JARVIS_WEB_PASSWORD"]
        self.assertTrue(stored.startswith("pbkdf2_sha256:"))
        self.assertEqual(self.request("POST", "/api/setup", {"password": "another one"})[0], 409)
        self.assertEqual(self.request("GET", "/api/status")[0], 200)

        self.request("POST", "/api/logout")
        self.assertIsNone(self.cookie)
        status, data, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 401)
        self.assertTrue(data["password_set"])
        self.assertEqual(self.request("POST", "/api/login", {"password": "wrong password"})[0], 401)
        self.assertEqual(self.request("POST", "/api/login", {"password": "correct horse"})[0], 200)
        self.assertEqual(self.request("GET", "/api/status")[0], 200)

    def test_forged_and_expired_cookies(self):
        self.setup_password()
        good = self.cookie
        expires, sig = good.split("=", 1)[1].split(".")
        for bad in (f"{web.COOKIE}={int(expires) + 99}.{sig}", f"{web.COOKIE}={expires}.{'0' * 64}",
                    f"{web.COOKIE}=garbage", f"{web.COOKIE}=1.{sig}"):
            self.cookie = bad
            self.assertEqual(self.request("GET", "/api/settings")[0], 401, bad)

    def test_password_change_logs_out_other_browsers(self):
        self.setup_password()
        other = self.cookie
        status, data, _ = self.request("POST", "/api/password", {"current": "nope nope", "new": "new password"})
        self.assertEqual(status, 400)
        status, data, _ = self.request("POST", "/api/password", {"current": "correct horse", "new": "new password"})
        self.assertEqual(status, 200, data)
        self.assertNotEqual(self.cookie, other)
        self.assertEqual(self.request("GET", "/api/status")[0], 200)
        self.cookie = other
        self.assertEqual(self.request("GET", "/api/status")[0], 401)

    def test_csrf(self):
        self.setup_password()
        self.assertEqual(self.request("PUT", "/api/settings", {"changes": {}}, csrf=False)[0], 403)
        self.assertEqual(self.request("PUT", "/api/settings", {"changes": {}},
                                      headers={"Origin": "http://evil.example.com"})[0], 403)
        self.assertEqual(self.request("PUT", "/api/settings", {"changes": {}},
                                      headers={"Origin": "http://127.0.0.1"})[0], 200)

    def test_settings_never_contain_secrets(self):
        self.setup_password()
        status, data, _ = self.request("GET", "/api/settings")
        self.assertEqual(status, 200)
        text = json.dumps(data)
        self.assertNotIn(SECRET_KEY, text)
        self.assertNotIn("pbkdf2", text)
        keys = {s["key"]: s for g in data["groups"] for s in g["settings"]}
        self.assertEqual(keys["GEMINI_KEYS"]["hint"], "…cdef")
        self.assertNotIn("value", keys["GEMINI_KEYS"])
        self.assertNotIn("JARVIS_WEB_PASSWORD", keys)
        self.assertEqual(keys["JARVIS_NAME"]["value"], "Friday")
        self.assertIsNone(keys["JARVIS_IDLE_TIMEOUT"]["value"])
        self.assertEqual([o["key"] for o in data["other"]], ["GOOGLE_TTS_KEY"])
        envfile.update({"HA_TOKEN": ""}, self.env_path)
        keys = {s["key"]: s for g in self.request("GET", "/api/settings")[1]["groups"] for s in g["settings"]}
        self.assertFalse(keys["HA_TOKEN"]["set"], "an empty secret counts as not set")

    def test_save_settings(self):
        self.setup_password()
        with open(self.env_path, encoding="utf-8") as f:
            before = f.read()
        status, data, _ = self.request("PUT", "/api/settings", {"changes": {
            "JARVIS_IDLE_TIMEOUT": "abc", "JARVIS_VOICE": "Puck", "JARVIS_WEB_PASSWORD": "x", "NEW_THING": "1"}})
        self.assertEqual(status, 400)
        self.assertEqual(set(data["errors"]), {"JARVIS_IDLE_TIMEOUT", "JARVIS_WEB_PASSWORD", "NEW_THING"})
        with open(self.env_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before, "nothing written on errors")

        status, data, _ = self.request("PUT", "/api/settings", {"changes": {
            "JARVIS_IDLE_TIMEOUT": "20", "JARVIS_VOICE": "Puck", "JARVIS_NAME": None, "GOOGLE_TTS_KEY": None,
            "GEMINI_KEYS": "k1, k2"}})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["changed"], ["GEMINI_KEYS", "GOOGLE_TTS_KEY", "JARVIS_IDLE_TIMEOUT", "JARVIS_NAME",
                                           "JARVIS_VOICE"])
        values = envfile.read(self.env_path)
        self.assertEqual(values["JARVIS_IDLE_TIMEOUT"], "20")
        self.assertEqual(values["JARVIS_VOICE"], "Puck")
        self.assertEqual(values["GEMINI_KEYS"], "k1,k2")
        self.assertNotIn("JARVIS_NAME", values)
        self.assertNotIn("GOOGLE_TTS_KEY", values)
        with open(self.env_path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("JARVIS_IDLE_TIMEOUT=20\n", text)  # took the place of the commented line
        self.assertIn("#JARVIS_NAME=Friday\n", text)

    def test_pending_changes(self):
        old = settings_schema.fingerprint(envfile.read(self.env_path))
        envfile.update({"JARVIS_WAKE_MODEL": "alexa", "JARVIS_WEB_IDLE_MIN": "5"}, self.env_path)
        self.assertEqual(web.pending_changes({"alive": True, "config": old}), ["Wake word model"])
        self.assertIsNone(web.pending_changes({"alive": False, "config": old}))

    def test_status_without_jarvis(self):
        self.setup_password()
        status, data, _ = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertFalse(data["jarvis"]["alive"])
        self.assertIn("system", data)
        self.assertEqual(data["name"], "Friday")

    def test_static_files(self):
        status, body, r = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<title>", body)
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))
        status, body, r = self.request("GET", "/static/app.js")
        self.assertEqual(status, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("text/javascript"))
        for path in ("/static/../server.py", "/static/%2e%2e/server.py", "/static/nope.js", "/server.py", "/.env"):
            self.assertEqual(self.request("GET", path)[0], 404, path)


    # --- phase 2: workspace, MCP servers, connection tests

    def test_workspace_edit_conflict_and_undo(self):
        self.setup_password()
        status, data, _ = self.request("GET", "/api/workspace")
        self.assertEqual(status, 200)
        memory = next(f for f in data["files"] if f["name"] == "MEMORY")
        status, data, _ = self.request("PUT", "/api/workspace/file",
                                       {"name": "MEMORY", "text": "# MEMORY\n- likes tea\n", "hash": "stale"})
        self.assertEqual(status, 409)
        self.assertTrue(data["conflict"])
        status, data, _ = self.request("PUT", "/api/workspace/file",
                                       {"name": "MEMORY", "text": "# MEMORY\n- likes tea\n", "hash": memory["hash"]})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["history"][0]["source"], "web UI")
        self.assertEqual(data["undo"]["source"], "web UI")
        self.assertEqual(workspace.read_file("MEMORY"), "# MEMORY\n- likes tea\n")
        status, data, _ = self.request("POST", "/api/workspace/undo")
        self.assertEqual(status, 200)
        self.assertEqual(workspace.read_file("MEMORY"), memory["text"])

    def test_workspace_busy_while_the_learner_runs(self):
        if workspace.fcntl is None:
            self.skipTest("no fcntl")
        self.setup_password()
        held, release = threading.Event(), threading.Event()

        def learner():
            with workspace.lock():
                held.set()
                release.wait(5)

        t = threading.Thread(target=learner)
        t.start()
        held.wait(5)
        try:
            status, data, _ = self.request("PUT", "/api/workspace/file", {"name": "SOUL", "text": "- calm\n"})
        finally:
            release.set()
            t.join()
        self.assertEqual(status, 409)
        self.assertTrue(data["busy"])

    def test_skills(self):
        self.setup_password()
        skill = {"name": "good-night", "description": "When the user says good night", "body": "1. Lights off"}
        self.assertEqual(self.request("PUT", "/api/workspace/skill", dict(skill, description=""))[0], 400)
        self.assertEqual(self.request("PUT", "/api/workspace/skill", dict(skill, name="Bad Name"))[0], 400)
        status, data, _ = self.request("PUT", "/api/workspace/skill", skill)
        self.assertEqual(status, 200, data)
        saved = data["skills"][0]
        self.assertEqual((saved["name"], saved["body"]), ("good-night", "1. Lights off"))
        # creating the same name again is a conflict, editing with its hash works
        self.assertEqual(self.request("PUT", "/api/workspace/skill", skill)[0], 409)
        status, data, _ = self.request("PUT", "/api/workspace/skill",
                                       dict(skill, body="1. Lights off\n2. Alarm on", hash=saved["hash"], always=True))
        self.assertEqual(status, 200, data)
        self.assertTrue(data["skills"][0]["always"])
        status, data, _ = self.request("POST", "/api/workspace/skill/delete",
                                       {"name": "good-night", "hash": data["skills"][0]["hash"]})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["skills"], [])

    def test_mcp_servers(self):
        self.setup_password()
        status, data, _ = self.request("GET", "/api/mcp")
        self.assertEqual((status, data["exists"], data["servers"]), (200, False, []))
        with open(settings_schema.MCP_CONFIG_PATH, "w") as f:
            json.dump({"servers": {"gw": {"url": "http://gw/sse", "headers": {"X": "1"}}}}, f)
        status, data, _ = self.request("PUT", "/api/mcp", {"servers": [
            {"name": "gw", "url": "http://gw:8000/sse", "type": "sse", "exclude": "a_*, b_*"},
            {"name": "gw", "url": "http://x", "type": "sse"}]})
        self.assertEqual(status, 400)
        self.assertIn("1", data["errors"])
        status, data, _ = self.request("PUT", "/api/mcp", {"servers": [
            {"name": "gw", "url": "http://gw:8000/sse", "type": "sse", "exclude": "a_*, b_*"},
            {"name": "memory", "url": "http://mem/mcp", "type": "stateless", "include": ["bm_search"]}]})
        self.assertEqual(status, 200, data)
        with open(settings_schema.MCP_CONFIG_PATH) as f:
            saved = json.load(f)["servers"]
        self.assertEqual(saved["gw"], {"url": "http://gw:8000/sse", "type": "sse", "exclude": ["a_*", "b_*"],
                                       "headers": {"X": "1"}})
        self.assertEqual(saved["memory"], {"url": "http://mem/mcp", "type": "stateless", "include": ["bm_search"]})
        self.assertEqual([s["name"] for s in data["servers"]], ["gw", "memory"])

    def test_option_lists(self):
        self.setup_password()
        settings = self.request("GET", "/api/settings")[1]
        live = next(s for g in settings["groups"] for s in g["settings"] if s["key"] == "GEMINI_LIVE_MODEL")
        self.assertEqual((live["source"], live["source_deps"]), ("gemini-live", ["GEMINI_KEYS"]))
        fake = FakeUpstream()
        old_api = web.GEMINI_API
        web.GEMINI_API = f"http://127.0.0.1:{fake.port}"
        web.app.cache.clear()

        def options(source, values=None):
            status, data, _ = self.request("POST", "/api/options", {"source": source, "values": values or {}})
            self.assertEqual(status, 200, data)
            return [o["value"] for o in data["options"]], data["error"], data["options"]

        try:
            # the saved key is rejected: no list, the reason instead
            values, error, _ = options("gemini-live")
            self.assertEqual(values, [])
            self.assertIn("API key not valid", error)
            # a key typed into the form wins over the saved one; filtered by what the models support, newest first
            typed = {"GEMINI_KEYS": GOOD_KEY}
            values, error, opts = options("gemini-live", typed)
            self.assertIsNone(error)
            self.assertEqual(values, ["gemini-3.8-live", "gemini-3.8-live-extended-thinking",
                                      "gemini-2.5-flash-native-audio-latest"])
            self.assertEqual([o["thinking"] for o in opts], [False, True, True])
            self.assertEqual(options("gemini-text", typed)[0], ["gemini-3.8-flash"])
            self.assertEqual(options("gemini-embed", typed)[0], ["gemini-embedding-001"])
            self.assertEqual(fake.gemini_calls, 2, "one models.list per key, then cached")
            base = f"http://127.0.0.1:{fake.port}"
            self.assertEqual(options("openclaw-agents", {"OPENCLAW_URL": base, "OPENCLAW_TOKEN": "good"})[0],
                             ["default", "main"])
            self.assertIn("401", options("openclaw-agents", {"OPENCLAW_URL": base, "OPENCLAW_TOKEN": "bad"})[1])
            self.assertIn("first", options("openclaw-agents")[1])
        finally:
            web.GEMINI_API = old_api
            web.app.cache.clear()
            fake.stop()
        os.makedirs(os.path.join(self.dir, "wakewords"))
        open(os.path.join(self.dir, "wakewords", "computer_v0.2.onnx"), "w").close()
        os.environ["JARVIS_DATA_DIR"] = self.dir
        try:
            _, _, opts = options("wakeword")
            self.assertIn({"value": "computer", "label": "computer", "note": "your own"}, opts)
        finally:
            os.environ.pop("JARVIS_DATA_DIR")
        self.assertEqual(self.request("POST", "/api/options", {"source": "nope"})[0], 400)
        self.assertEqual(self.request("POST", "/api/options", {"source": "wakeword"}, csrf=False)[0], 403)

    def test_connection_tests_against_fake_servers(self):
        self.setup_password()
        fake = FakeUpstream()
        try:
            base = f"http://127.0.0.1:{fake.port}"
            for path in ("/sse", "/sse-crlf"):
                start = time.time()
                result = self.request("POST", "/api/mcp/test", {"url": base + path, "type": "sse"})[1]
                self.assertTrue(result["ok"], (path, result))
                self.assertLess(time.time() - start, 2, "must not wait for the stream to end")
            result = self.request("POST", "/api/mcp/test", {"url": base + "/mcp", "type": "stateless"})[1]
            self.assertTrue(result["ok"], result)
            self.assertIn("2 tools", result["detail"])
            result = self.request("POST", "/api/mcp/test", {"url": base + "/nothing", "type": "sse"})[1]
            self.assertFalse(result["ok"])
            ha = self.request("POST", "/api/test", {"what": "home_assistant",
                                                    "values": {"HA_URL": base, "HA_TOKEN": "good"}})[1]["results"][0]
            self.assertTrue(ha["ok"], ha)
            self.assertIn("Home", ha["detail"])
            ha = self.request("POST", "/api/test", {"what": "home_assistant",
                                                    "values": {"HA_URL": base, "HA_TOKEN": "bad"}})[1]["results"][0]
            self.assertFalse(ha["ok"])
            self.assertIn("rejected", ha["detail"])
        finally:
            fake.stop()
        gemini = self.request("POST", "/api/test", {"what": "gemini", "values": {"GEMINI_KEYS": ""}})
        self.assertEqual(gemini[0], 200)

    def test_setup_flow(self):
        self.setup_password()
        session = self.request("GET", "/api/session")[1]
        self.assertFalse(session["needs_setup"], "the test .env has a key")
        envfile.update({"GEMINI_KEYS": None}, self.env_path)
        self.assertTrue(self.request("GET", "/api/session")[1]["needs_setup"])
        data = self.request("GET", "/api/onboarding")[1]
        self.assertEqual([q["key"] for q in data["questions"]], [k for k, _ in workspace.ONBOARDING_QUESTIONS])
        self.assertIsNone(data["pending"])
        self.assertEqual(self.request("POST", "/api/onboarding", {"answers": {"evil": "x"}})[0], 400)
        self.assertEqual(self.request("POST", "/api/onboarding", {"answers": {"home": "  "}})[0], 400)
        status, data, _ = self.request("POST", "/api/onboarding", {"answers": {"user_name": "Alex", "home": "Munich"}})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["pending"]["answers"], {"user_name": "Alex", "home": "Munich"})

    def test_session_settings_need_no_restart(self):
        old = settings_schema.fingerprint(envfile.read(self.env_path))
        envfile.update({"JARVIS_IDLE_TIMEOUT": "30", "JARVIS_ALARM_TIMEOUT": "60"}, self.env_path)
        self.assertEqual(web.pending_changes({"alive": True, "config": old}), ["Alarm stops after (s)"])
        self.assertIsNone(web.pending_changes({"alive": True, "config": old, "state": "unconfigured"}))

    def test_timers_in_status(self):
        self.setup_password()
        os.environ["JARVIS_DATA_DIR"] = self.dir
        try:
            with open(os.path.join(self.dir, "timers.json"), "w") as f:
                json.dump({"timers": [{"id": "1", "kind": "alarm", "label": "Aufstehen", "due": 2e9}]}, f)
            data = self.request("GET", "/api/status")[1]
        finally:
            os.environ.pop("JARVIS_DATA_DIR", None)
        self.assertEqual(data["timers"], [{"label": "Aufstehen", "kind": "alarm", "due": 2e9}])


GOOD_KEY = "AIzaGoodFakeKeyForTheModelList1234"
MODELS = [  # a cut of a real models.list answer
    {"name": "models/gemini-3.8-flash", "supportedGenerationMethods": ["generateContent", "createCachedContent"], "thinking": True},
    {"name": "models/gemini-2.5-flash-image", "supportedGenerationMethods": ["generateContent", "countTokens"]},
    {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
    {"name": "models/gemini-2.5-flash-native-audio-latest", "supportedGenerationMethods": ["bidiGenerateContent"], "thinking": True},
    {"name": "models/gemini-3.5-transcribe-live", "supportedGenerationMethods": ["bidiGenerateContent"]},
    {"name": "models/gemini-3.8-live-extended-thinking", "supportedGenerationMethods": ["bidiGenerateContent"], "thinking": True},
    {"name": "models/gemini-3.8-live", "displayName": "Gemini 3.8 Live", "supportedGenerationMethods": ["bidiGenerateContent"]},
    {"name": "models/genie-4", "supportedGenerationMethods": ["bidiGenerateContent"]},
    {"name": "models/lyria-realtime-exp", "supportedGenerationMethods": ["bidiGenerateMusic"]},
]


class FakeUpstream:
    """Tiny HTTP server playing an MCP server (SSE and stateless), Home Assistant, the Gemini model list and OpenClaw."""

    def __init__(self):
        self.gemini_calls = 0
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path in ("/sse", "/sse-crlf"):
                    # a real SSE stream stays open after the first event
                    nl = "\r\n" if self.path == "/sse-crlf" else "\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    self.wfile.write(f"event: endpoint{nl}data: /messages/?session_id=1{nl}{nl}".encode())
                    self.wfile.flush()
                    time.sleep(3)
                elif self.path.startswith("/v1beta/models"):
                    outer.gemini_calls += 1
                    if self.headers.get("x-goog-api-key") == GOOD_KEY:
                        self._send(200, json.dumps({"models": MODELS}))
                    else:
                        self._send(400, json.dumps({"error": {"code": 400, "message": "API key not valid."}}))
                elif self.path == "/v1/models":
                    if self.headers.get("Authorization") == "Bearer good":
                        self._send(200, json.dumps({"object": "list", "data": [
                            {"id": "openclaw"}, {"id": "openclaw/default"}, {"id": "openclaw/main"}]}))
                    else:
                        self._send(401, "{}")
                elif self.path == "/api/config":
                    if self.headers.get("Authorization") == "Bearer good":
                        self._send(200, json.dumps({"location_name": "Home", "version": "2026.11.1"}))
                    else:
                        self._send(401, "{}")
                else:
                    self._send(200, "hello", "text/plain")

            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self._send(200, json.dumps({"jsonrpc": "2.0", "id": 1,
                                            "result": {"tools": [{"name": "a"}, {"name": "b"}]}}))

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class RestartWhenIdleTest(unittest.TestCase):
    def test_waits_for_the_conversation_to_end(self):
        gate = threading.Event()  # the conversation lasts until the test sets it
        after = [{"alive": True, "state": "idle", "learner": "review"}, {"alive": True, "state": "idle"}]
        calls, done = [], threading.Event()

        def read():
            if not gate.is_set():
                return {"alive": True, "state": "session"}
            return after.pop(0) if len(after) > 1 else after[0]

        old_read, old_restart, old_sleep = web.status.read, web.restart_jarvis, web.time.sleep
        web.status.read = read
        web.restart_jarvis = lambda: (calls.append(dict(after[0])), done.set())
        web.time.sleep = lambda s: old_sleep(0.001)
        try:
            web.restart_when_idle()
            web.restart_when_idle()  # a second request while one waits is ignored
            self.assertIsNotNone(web.app.restart_pending)
            old_sleep(0.05)
            self.assertEqual(calls, [], "no restart during the conversation")
            gate.set()
            self.assertTrue(done.wait(5))
            for _ in range(200):
                if web.app.restart_pending is None:
                    break
                old_sleep(0.01)
        finally:
            web.status.read, web.restart_jarvis, web.time.sleep = old_read, old_restart, old_sleep
        self.assertEqual(calls, [{"alive": True, "state": "idle"}], "one restart, after session and learner")
        self.assertIsNone(web.app.restart_pending)

    def test_busy_reasons(self):
        self.assertEqual(web.jarvis_busy({"state": "session"}), "in a conversation")
        self.assertEqual(web.jarvis_busy({"state": "idle", "alarm": True}), "an alarm is ringing")
        self.assertIsNone(web.jarvis_busy({"state": "idle"}))


class HelpersTest(unittest.TestCase):
    def test_passwords(self):
        stored = web.hash_password("secret pw")
        self.assertTrue(web.check_password("secret pw", stored))
        self.assertFalse(web.check_password("secret pX", stored))
        self.assertTrue(web.check_password("plain text", "plain text"))
        self.assertFalse(web.check_password("x", ""))
        self.assertEqual(envfile.quote(stored), stored, "the hash must not need quoting in .env")

    def test_mask(self):
        secrets_list = web.secret_values({"GEMINI_KEYS": f"{SECRET_KEY},short", "HA_TOKEN": "tok-1234567890"})
        text = f"key {SECRET_KEY} and Rotated to API Key #2 ({SECRET_KEY[:8]}...) tok-1234567890"
        masked = web.mask(text, secrets_list)
        self.assertNotIn(SECRET_KEY[:8], masked)
        self.assertNotIn("tok-1234567890", masked)


if __name__ == "__main__":
    unittest.main()
