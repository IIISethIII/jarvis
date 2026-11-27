"""core/notify.py and the Telegram parts of the web interface, against a fake Telegram Bot API and Home Assistant.

Pure standard library:  python -m unittest discover tests/unit
"""
import http.server
import json
import os
import tempfile
import threading
import unittest

from core import notify
from web import server as web

GOOD = "123456:good-token"
KEYS = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "JARVIS_HA_NOTIFY", "HA_URL", "HA_TOKEN")


class FakeApis:
    """Telegram (POST /bot<token>/<method>) and Home Assistant (/api/services) in one tiny server."""

    def __init__(self):
        self.sent = []  # (chat_id, text)
        self.ha_calls = []  # (service name, payload)
        self.updates = []
        self.get_updates = []  # params of each getUpdates call
        self.conflict = False
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/services" and self.headers.get("Authorization") == "Bearer good":
                    self._send(200, [{"domain": "light", "services": {"turn_on": {}}},
                                     {"domain": "notify", "services": {"send_message": {}, "mobile_app_phone": {},
                                                                       "notify": {}}}])
                else:
                    self._send(401, {})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.path.startswith("/api/services/notify/"):
                    if self.headers.get("Authorization") != "Bearer good":
                        return self._send(401, {})
                    name = self.path.rsplit("/", 1)[1]
                    if name != "mobile_app_phone":
                        return self._send(400, {"message": "Service not found"})
                    outer.ha_calls.append((name, body))
                    return self._send(200, [])
                _, bot, method = self.path.split("/", 2)
                if bot != f"bot{GOOD}":
                    return self._send(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})
                if method == "getMe":
                    return self._send(200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": "test_jarvis_bot"}})
                if method == "sendMessage":
                    if str(body.get("chat_id")) == "404":
                        return self._send(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})
                    outer.sent.append((str(body["chat_id"]), body["text"]))
                    return self._send(200, {"ok": True, "result": {"message_id": len(outer.sent)}})
                if method == "getUpdates":
                    if outer.conflict:
                        return self._send(409, {"ok": False, "error_code": 409,
                                                "description": "Conflict: can't use getUpdates method while webhook is active"})
                    outer.get_updates.append(body)
                    offset = body.get("offset", 0)
                    return self._send(200, {"ok": True, "result": [u for u in outer.updates if u["update_id"] >= offset]})
                self._send(404, {"ok": False, "error_code": 404, "description": "Not Found"})

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def message(self, text, chat_id=42, first_name="Alex"):
        self.updates.append({"update_id": 100 + len(self.updates),
                             "message": {"text": text, "chat": {"id": chat_id, "first_name": first_name, "type": "private"}}})

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeApis()
        self.old_api = notify.TELEGRAM_API
        notify.TELEGRAM_API = self.fake.url
        self.old_env = {k: os.environ.pop(k) for k in KEYS if k in os.environ}

    def tearDown(self):
        notify.TELEGRAM_API = self.old_api
        for k in KEYS:
            os.environ.pop(k, None)
        os.environ.update(self.old_env)
        self.fake.stop()

    def test_channel_follows_the_settings(self):
        self.assertIsNone(notify.channel())
        self.assertEqual(notify.send("hi"), (False, "no channel is set up (Telegram or a Home Assistant notify service)"))
        os.environ.update({"JARVIS_HA_NOTIFY": "notify.mobile_app_phone"})
        self.assertIsNone(notify.channel(), "HA needs its URL and token too")
        os.environ.update({"HA_URL": self.fake.url, "HA_TOKEN": "good"})
        self.assertEqual(notify.channel(), "ha")
        os.environ.update({"TELEGRAM_BOT_TOKEN": GOOD})
        self.assertEqual(notify.channel(), "ha", "a bot without a connected chat is no channel")
        os.environ.update({"TELEGRAM_CHAT_ID": "42"})
        self.assertEqual(notify.channel(), "telegram")

    def test_send_via_telegram(self):
        os.environ.update({"TELEGRAM_BOT_TOKEN": GOOD, "TELEGRAM_CHAT_ID": "42"})
        self.assertEqual(notify.send("Zutaten: Mehl", "Rezept", "https://example.org/r"), (True, "telegram"))
        self.assertEqual(self.fake.sent, [("42", "Rezept\n\nZutaten: Mehl\nhttps://example.org/r")])
        notify.send("Link im Text https://example.org/r", link="https://example.org/r")
        self.assertEqual(self.fake.sent[-1][1], "Link im Text https://example.org/r", "the link is not repeated")
        self.assertEqual(notify.send("  ")[0], False)

    def test_long_messages_are_split(self):
        os.environ.update({"TELEGRAM_BOT_TOKEN": GOOD, "TELEGRAM_CHAT_ID": "42"})
        text = "\n".join(f"Zeile {i}: " + "x" * 90 for i in range(100))  # ~10k chars
        self.assertTrue(notify.send(text)[0])
        parts = [t for _, t in self.fake.sent]
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(len(p) <= notify.TELEGRAM_MAX for p in parts))
        self.assertEqual("\n".join(parts), text, "split at line breaks, nothing lost")
        self.assertEqual(notify.chunks("a" * 9000), ["a" * 4096, "a" * 4096, "a" * 808])

    def test_errors_are_readable(self):
        os.environ.update({"TELEGRAM_BOT_TOKEN": "123:bad", "TELEGRAM_CHAT_ID": "42"})
        self.assertEqual(notify.send("x"), (False, "the bot token was rejected"))
        os.environ.update({"TELEGRAM_BOT_TOKEN": GOOD, "TELEGRAM_CHAT_ID": "404"})
        self.assertEqual(notify.send("x"), (False, "chat not found: connect the chat again"))
        notify.TELEGRAM_API = "http://127.0.0.1:9"  # nothing listens there
        ok, detail = notify.send("x")
        self.assertFalse(ok)
        self.assertIn("not reachable", detail)
        self.assertNotIn(GOOD, detail, "the token is part of the URL and must not leak into messages")

    def test_send_via_home_assistant(self):
        os.environ.update({"JARVIS_HA_NOTIFY": "notify.mobile_app_phone", "HA_URL": self.fake.url, "HA_TOKEN": "good"})
        self.assertEqual(notify.send("Bin gleich da", "Jarvis", "https://example.org"), (True, "ha"))
        self.assertEqual(self.fake.ha_calls, [("mobile_app_phone", {
            "message": "Bin gleich da", "title": "Jarvis",
            "data": {"clickAction": "https://example.org", "url": "https://example.org"}})])
        self.assertIn("no service", notify.send_ha(self.fake.url, "good", "notify.other", "x"))
        self.assertIn("rejected", notify.send_ha(self.fake.url, "bad", "notify.mobile_app_phone", "x"))
        self.assertIn("must look like", notify.send_ha(self.fake.url, "good", "light.kitchen", "x"))

    def test_find_start(self):
        self.fake.message("hello bot", chat_id=7)
        self.fake.message("/start wrong", chat_id=8)
        self.fake.message("/start abc123", chat_id=42, first_name="Alex")
        chat, offset, err = notify.find_start(GOOD, "abc123")
        self.assertIsNone(err)
        self.assertEqual(chat, {"id": 42, "name": "Alex"})
        self.assertEqual(offset, 103)
        self.assertEqual(self.fake.get_updates[-1]["offset"], 103, "read updates are confirmed")
        self.assertEqual(notify.find_start(GOOD, "abc123", offset)[0], None, "not found twice")
        # the bare code and the group form of the command work too
        self.fake.message("abc123", chat_id=43)
        self.assertEqual(notify.find_start(GOOD, "abc123", offset)[0]["id"], 43)
        self.fake.message("/start@test_jarvis_bot zz99", chat_id=-100)
        self.assertEqual(notify.find_start(GOOD, "zz99", offset + 1)[0]["id"], -100)
        self.fake.conflict = True
        self.assertIn("another program", notify.find_start(GOOD, "abc123")[2])


class WebTelegramTest(unittest.TestCase):
    """The connect flow and the test button of the web interface."""

    def setUp(self):
        self.fake = FakeApis()
        self.old_api = notify.TELEGRAM_API
        notify.TELEGRAM_API = self.fake.url
        self.dir = tempfile.mkdtemp()
        self.old_env_path = web.ENV_PATH
        web.ENV_PATH = os.path.join(self.dir, ".env")
        with open(web.ENV_PATH, "w") as f:
            f.write("JARVIS_NAME=Friday\n")
        web.app.telegram_pair = None

    def tearDown(self):
        notify.TELEGRAM_API = self.old_api
        web.ENV_PATH = self.old_env_path
        web.app.telegram_pair = None
        self.fake.stop()

    def test_connect_a_chat(self):
        with self.assertRaises(web.HTTPError) as e:
            web.telegram_pair_start({})
        self.assertIn("token first", e.exception.message)
        with self.assertRaises(web.HTTPError) as e:
            web.telegram_pair_start({"TELEGRAM_BOT_TOKEN": "123:bad"})
        self.assertIn("rejected", e.exception.message)
        r = web.telegram_pair_start({"TELEGRAM_BOT_TOKEN": GOOD})
        self.assertEqual(r["bot"], "test_jarvis_bot")
        self.assertEqual(r["link"], f"https://t.me/test_jarvis_bot?start={r['code']}")
        self.assertNotIn(GOOD, json.dumps(r), "the token stays on the server")
        self.assertEqual(web.telegram_pair_check(), {"chat": None, "error": None})
        self.fake.message(f"/start {r['code']}", chat_id=42, first_name="Alex")
        self.assertEqual(web.telegram_pair_check(), {"chat": {"id": 42, "name": "Alex"}, "error": None})
        self.assertEqual(self.fake.sent, [("42", "Friday is connected: messages from Friday arrive in this chat.")])
        with self.assertRaises(web.HTTPError) as e:
            web.telegram_pair_check()
        self.assertEqual(e.exception.code, 409, "the code is used up")

    def test_send_test_message(self):
        get = web.form_values({"TELEGRAM_BOT_TOKEN": GOOD})
        self.assertIn("connect your chat", web.test_messages(get)[0]["detail"])
        get = web.form_values({"TELEGRAM_BOT_TOKEN": GOOD, "TELEGRAM_CHAT_ID": "42",
                               "JARVIS_HA_NOTIFY": "notify.mobile_app_phone", "HA_URL": self.fake.url, "HA_TOKEN": "good"})
        results = web.test_messages(get)
        self.assertEqual([(r["label"], r["ok"]) for r in results], [("Telegram", True), ("Home Assistant", True)])
        self.assertIn("not used while Telegram", results[1]["detail"])
        self.assertEqual(self.fake.sent, [("42", "Test message from the Friday web interface.")])
        self.assertFalse(web.test_messages(web.form_values({}))[0]["ok"])

    def test_notify_services_dropdown(self):
        load = web.OPTION_SOURCES["ha-notify"][1]
        options = load(web.form_values({"HA_URL": self.fake.url, "HA_TOKEN": "good"}))
        self.assertEqual([o["value"] for o in options], ["notify.mobile_app_phone", "notify.notify"])
        with self.assertRaises(web.OptionsError):
            load(web.form_values({"HA_URL": self.fake.url, "HA_TOKEN": "bad"}))


def _has_genai():
    try:
        import google.genai  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_has_genai(), "needs the runtime dependencies (run with venv/bin/python)")
class ToolOfferedPerSessionTest(unittest.TestCase):
    def test_send_to_phone_only_with_a_channel(self):
        from core import tools
        old = {k: os.environ.pop(k) for k in KEYS if k in os.environ}
        try:
            self.assertNotIn("send_to_phone", [d.name for d in tools.declarations()])
            os.environ.update({"TELEGRAM_BOT_TOKEN": GOOD, "TELEGRAM_CHAT_ID": "42"})
            self.assertIn("send_to_phone", [d.name for d in tools.declarations()])
            self.assertIn("send_to_phone", tools.TOOL_IMPLEMENTATIONS)
        finally:
            for k in KEYS:
                os.environ.pop(k, None)
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
