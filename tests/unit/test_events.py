"""Event sessions (core/events.py), their prompt (core/prompt.build_event), the WebSocket listener
(core/ha_events.py) and the blueprint file.

  python -m unittest tests.unit.test_events          (parts that need google-genai or websockets are skipped)
  venv/bin/python -m unittest tests.unit.test_events (everything)
"""
import json
import os
import re
import shutil
import tempfile
import threading
import time
import unittest

from core import events

CODE = "Kastanie"


def _has(module):
    try:
        __import__(module)
        return True
    except ImportError:
        return False


class EventDataTest(unittest.TestCase):
    def test_parse(self):
        e = events.Event({"prompt": " Ask who is there. ", "skills": "visitor, keys", "idle_timeout": "500",
                          "owner": "Alex"})
        self.assertEqual((e.prompt, e.skills, e.idle_timeout, e.owner_name), ("Ask who is there.", ["visitor", "keys"], 120.0, "Alex"))
        self.assertEqual(events.Event({"prompt": "x", "skills": ["a", ""]}).skills, ["a"])
        self.assertIsNone(events.Event({"prompt": "x", "idle_timeout": "abc"}).idle_timeout)
        self.assertEqual(events.Event({"prompt": "x"}).owner_name, "the owner")
        for bad in ({}, {"prompt": "  "}, "text", None):
            with self.assertRaises(ValueError):
                events.Event(bad)


class CodeWordTest(unittest.TestCase):
    def test_matches(self):
        for said in ("Kastanie", "kastanie!", "Das Codewort ist Kastanie.", "KASTANIE"):
            self.assertTrue(events.matches(said, CODE), said)
        for said in ("Kastan", "Kastanien", "", None, "Eiche Kastanie Linde Ahorn Birke Buche Esche"):
            self.assertFalse(events.matches(said, CODE), said)
        self.assertTrue(events.matches("es ist grüne tür", "Grüne Tür"))
        self.assertTrue(events.matches("gruene tur", "grüne tür") is False)  # no fuzzy spelling
        self.assertFalse(events.matches("grüne", "grüne tür"))

    def test_redact(self):
        self.assertEqual(events.redact("Ich bin Paul, Kastanie.", CODE), "Ich bin Paul, [code word].")
        self.assertEqual(events.redact("KASTANIE", CODE), "[code word]")
        self.assertEqual(events.redact("nothing", ""), "nothing")
        # near misses of the speech recognition give the word away too (seen live with the real word: one letter pair misheard)
        self.assertEqual(events.redact("Ich bin Paul Kastanje.", CODE), "Ich bin Paul [code word].")
        self.assertEqual(events.redact("Mach das Fenster zu, Kastnaie", CODE), "Mach das Fenster zu, [code word]")
        self.assertEqual(events.redact("Ich hole den Kellerschlüssel", CODE), "Ich hole den Kellerschlüssel")
        self.assertFalse(events.matches("Kastanje", CODE), "checking stays exact")


class EventSessionTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.pop("JARVIS_OWNER_CODEWORD", None)
        os.environ["JARVIS_OWNER_CODEWORD"] = CODE
        events._last_results.clear()
        self.sent, self.fired = [], []

    def tearDown(self):
        os.environ.pop("JARVIS_OWNER_CODEWORD", None)
        if self.old is not None:
            os.environ["JARVIS_OWNER_CODEWORD"] = self.old
        events._last_results.clear()

    def session(self, **data):
        return events.EventSession(events.Event({"prompt": "Ask who is there.", "owner": "Paul", "kind": "who",
                                                 "title": "Someone is in your room", **data}))

    def finish(self, s):
        return s.finish(send=lambda text, title=None: (self.sent.append((title, text)) or (True, "telegram")),
                        fire=lambda typ, data: self.fired.append((typ, data)))

    def test_visitor_report_with_their_own_words(self):
        s = self.session()
        s.add_turn("Ich bin Lisa, ich hole den Kellerschlüssel.", "Hallo Lisa! Wann bringst du ihn zurück?")
        s.add_turn("Heute Abend um acht.", "Danke!")
        self.assertIn("Saved", s.handle("report_event_result", {"message": "Lisa holt den Kellerschlüssel, zurück um 20 Uhr."}))
        result = self.finish(s)
        self.assertEqual(self.sent, [("Someone is in your room", "Lisa holt den Kellerschlüssel, zurück um 20 Uhr.\n\n"
                                      "🗣 „Ich bin Lisa, ich hole den Kellerschlüssel. / Heute Abend um acht.“")])
        self.assertEqual(self.fired[0][0], "jarvis_trigger_result")
        self.assertEqual({k: result[k] for k in ("status", "answered", "owner", "sent")},
                         {"status": "done", "answered": True, "owner": False, "sent": True})
        # the next trigger of the same kind knows about this one
        minutes, message = events.last_result("who")
        self.assertLess(minutes, 1)
        self.assertIn("Lisa", message)
        self.assertIsNone(events.last_result("who", now=time.time() + events.REPEAT_WINDOW_S + 1))

    def test_owner_with_code_word(self):
        s = self.session()
        self.assertIn("Wrong", s.handle("verify_owner", {"code_word": "Ficus"}))
        self.assertIn("Correct", s.handle("verify_owner", {"code_word": "kastanie"}))
        user, jarvis = s.add_turn("Ich bin's, Paul. Kastanie.", "Hi Paul!")
        self.assertEqual(user, "Ich bin's, Paul. [code word].")
        self.finish(s)
        self.assertEqual(self.sent, [], "no message when the owner identified")
        self.assertTrue(self.fired[0][1]["owner"])
        self.assertNotIn(CODE.lower(), json.dumps(self.fired).lower())
        self.assertIsNone(events.last_result("who"), "the owner's own visit is no earlier result")

    def test_guessing_is_limited(self):
        s = self.session()
        for _ in range(events.MAX_ATTEMPTS):
            s.handle("verify_owner", {"code_word": "Kaktus"})
        self.assertIn("No attempts left", s.handle("verify_owner", {"code_word": CODE}))
        self.assertFalse(s.owner)

    def test_report_never_contains_the_code_word(self):
        s = self.session()
        s.handle("report_event_result", {"message": "Someone said Kastanie but it was wrong."})
        self.assertNotIn(CODE, s.report)

    def test_silence(self):
        s = self.session(silence_message="Nobody answered.")
        result = self.finish(s)
        self.assertEqual(self.sent, [("Someone is in your room", "Nobody answered.")])
        self.assertFalse(result["answered"])
        self.sent.clear()
        self.finish(self.session())  # no silence message: nothing is sent
        self.assertEqual(self.sent, [])

    def test_words_without_report_are_still_sent(self):
        s = self.session()
        s.add_turn("Sag Paul, alles ist gut, er soll nicht nachfragen.", "")
        self.finish(s)
        self.assertEqual(self.sent[0][1], "🗣 „Sag Paul, alles ist gut, er soll nicht nachfragen.“")

    def test_reject(self):
        events.reject({"id": "x1", "kind": "who"}, "busy", fire=lambda typ, data: self.fired.append(data))
        self.assertEqual((self.fired[0]["id"], self.fired[0]["status"]), ("x1", "busy"))

    @unittest.skipUnless(_has("google.genai"), "needs google-genai (venv/bin/python)")
    def test_tools_of_the_session(self):
        names = [d.name for d in self.session().declarations()]
        self.assertEqual(names, ["report_event_result", "verify_owner", "end_conversation"])
        os.environ.pop("JARVIS_OWNER_CODEWORD")
        self.assertEqual([d.name for d in self.session().declarations()], ["report_event_result", "end_conversation"])


@unittest.skipUnless(_has("dotenv"), "needs the runtime dependencies (venv/bin/python)")
class EventPromptTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ["JARVIS_WORKSPACE"] = self.dir
        from core import workspace
        workspace.ensure()
        with open(os.path.join(self.dir, "MEMORY.md"), "a", encoding="utf-8") as f:
            f.write("\n- Paul's bank PIN hint: SECRET-FACT\n")
        with open(os.path.join(self.dir, "USER.md"), "a", encoding="utf-8") as f:
            f.write("\n- USER-FACT: Paul works late on Fridays\n")
        os.makedirs(os.path.join(self.dir, "skills"), exist_ok=True)
        with open(os.path.join(self.dir, "skills", "visitor.md"), "w", encoding="utf-8") as f:
            f.write("---\ndescription: Rules for visitor checks\n---\n- Someone fetches a key: ask when it comes back.\n")
        events._last_results.clear()

    def tearDown(self):
        os.environ.pop("JARVIS_WORKSPACE", None)
        shutil.rmtree(self.dir)
        events._last_results.clear()

    def test_restricted_prompt(self):
        from core import prompt
        event = events.Event({"prompt": "Ask who is there {not a placeholder}.", "owner": "Paul",
                              "skills": ["visitor", "missing"], "kind": "who"})
        text = prompt.build_event(event, owner_check=True)
        self.assertIn("You report to Paul", text)
        self.assertIn("Ask who is there {not a placeholder}.", text)
        self.assertIn("ask when it comes back", text)
        self.assertIn("PAUL WITH THE CODE WORD", text)
        for private in ("SECRET-FACT", "USER-FACT", "=== MEMORY", "=== USER", "HOUSE RULES", "search_archive"):
            self.assertNotIn(private, text)
        self.assertNotIn("CODE WORD", prompt.build_event(event, owner_check=False))
        self.assertIsNone(re.search(r"\{(assistant_name|language|time|owner)\}", text), "all placeholders filled")
        events._last_results["who"] = (time.time() - 600, "Lisa fetched the key.")
        self.assertIn("10 min ago. Result: Lisa fetched the key.", prompt.build_event(event))


@unittest.skipUnless(_has("websockets"), "needs websockets (venv/bin/python)")
class ListenerTest(unittest.TestCase):
    """core/ha_events.listen against a fake Home Assistant WebSocket API."""

    def serve(self, token_ok=True):
        from websockets.sync.server import serve

        def handler(ws):
            ws.send(json.dumps({"type": "auth_required"}))
            auth = json.loads(ws.recv())
            if not token_ok or auth.get("access_token") != "good":
                ws.send(json.dumps({"type": "auth_invalid", "message": "Invalid access token"}))
                return
            ws.send(json.dumps({"type": "auth_ok"}))
            sub = json.loads(ws.recv())
            self.subscribed = sub
            ws.send(json.dumps({"id": sub["id"], "type": "result", "success": True}))
            ws.send(json.dumps({"id": sub["id"], "type": "event", "event": {
                "event_type": "jarvis_trigger", "data": {"prompt": "Who is there?", "kind": "who"}}}))
            time.sleep(0.5)

        server = serve(handler, "127.0.0.1", 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        return f"ws://127.0.0.1:{server.socket.getsockname()[1]}"

    def test_receives_triggers(self):
        from core import ha_events
        while ha_events.take():
            pass
        url = self.serve()
        ready = threading.Event()
        try:
            ha_events.listen(url, "good", on_ready=ready.set)
        except Exception:
            pass  # the fake server closes the connection after the event
        self.assertTrue(ready.is_set())
        self.assertEqual(self.subscribed["type"], "subscribe_events")
        self.assertEqual(self.subscribed["event_type"], "jarvis_trigger")
        received, data = ha_events.take()
        self.assertEqual(data, {"prompt": "Who is there?", "kind": "who"})
        self.assertLess(time.time() - received, 5)
        self.assertIsNone(ha_events.take())

    def test_rejected_token(self):
        from core import ha_events
        with self.assertRaises(ha_events.AuthError):
            ha_events.listen(self.serve(), "bad")

    def test_url(self):
        from core import ha_events
        self.assertEqual(ha_events.ws_url("http://homeassistant.local:8123/"), "ws://homeassistant.local:8123/api/websocket")
        self.assertEqual(ha_events.ws_url("https://ha.example.org"), "wss://ha.example.org/api/websocket")


class BlueprintTest(unittest.TestCase):
    def test_blueprint_fires_what_jarvis_reads(self):
        path = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "home-assistant", "jarvis_who_is_there.yaml")
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("event: jarvis_trigger", text)
        fired = set(re.findall(r"^      (\w+):", text.split("event_data:")[1], re.M))
        self.assertEqual(fired, {"id", "kind", "owner", "title", "prompt", "skills", "silence_message"})
        # every key the blueprint sends is one core/events.py documents
        for key in fired:
            self.assertIn(f"  {key} ", events.__doc__.replace("\n", " \n"), key)
        inputs = set(re.findall(r"!input (\w+)", text))
        declared = set(re.findall(r"^    (\w+):\n      name:", text, re.M))
        self.assertEqual(inputs - declared, set(), "every !input is declared")


if __name__ == "__main__":
    unittest.main()
