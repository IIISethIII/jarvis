"""Event sessions: Home Assistant starts a conversation, Jarvis speaks first and reports the result.

Flow: an automation fires 'jarvis_trigger' (core/ha_events.py receives it) -> main.py starts a session with
event=Event -> GeminiLiveProvider builds a restricted session (prompt.build_event, EventSession.declarations)
-> at the end EventSession.finish() sends the message to the phone (core/notify.py) and fires
'jarvis_trigger_result' back to Home Assistant.

Restricted on purpose: whoever is in the room may be a stranger. The session has no smart home, memory or MCP
tools and its prompt has no USER/MEMORY/house rules/HA overview, so nobody can switch devices or ask what
Jarvis knows about the user. The owner can identify with a code word (JARVIS_OWNER_CODEWORD): it never enters
the prompt, Gemini passes what it heard to verify_owner and the code compares. Transcripts are redacted before
they are archived or sent, so the word doesn't end up in later prompts either.

Event data (all optional except prompt):
  prompt          the task, e.g. "Someone is in the room while Paul is away. Ask who they are ..."
  id              echoed in the result event (default: generated)
  kind            groups repeated triggers ("who_is_there"): the last result of the same kind within
                  REPEAT_WINDOW_S goes into the prompt, so Jarvis doesn't introduce itself twice
  owner           name of the person Jarvis reports to ("the owner" if missing)
  title           first line of the message to the phone
  skills          workspace skills with extra rules for this event (their full text goes into the prompt)
  silence_message sent when nobody answered; nothing is sent without it
  idle_timeout    seconds of silence after Jarvis spoke before the session ends
"""
import os
import re
import time
import unicodedata
import uuid

EVENT_TYPE = "jarvis_trigger"
RESULT_TYPE = "jarvis_trigger_result"
NO_ANSWER_NUDGE_S = 30  # nobody said anything this long after Jarvis spoke: it asks once more, then it ends
MAX_ATTEMPTS = 3  # code word guesses per session
MAX_GUESS_WORDS = 6  # a guess longer than this is not compared (no reciting word lists)
REPEAT_WINDOW_S = 30 * 60
QUOTE_CHARS = 800  # the person's own words appended to the message
PROMPT_CHARS = 4000

_last_results = {}  # kind -> (time, message): the previous run, for repeated triggers


class Event:
    def __init__(self, data, received=None):
        if not isinstance(data, dict):
            raise ValueError("event data must be an object")
        self.prompt = str(data.get("prompt") or "").strip()[:PROMPT_CHARS]
        if not self.prompt:
            raise ValueError("event data needs a 'prompt'")
        self.id = str(data.get("id") or uuid.uuid4().hex[:12])
        self.kind = str(data.get("kind") or "event")
        self.owner = str(data.get("owner") or "").strip()
        self.title = str(data.get("title") or "").strip()
        self.silence_message = str(data.get("silence_message") or "").strip()
        skills = data.get("skills") or []
        if isinstance(skills, str):
            skills = [s.strip() for s in skills.split(",")]
        self.skills = [str(s).strip() for s in skills if str(s).strip()]
        try:
            idle = float(data.get("idle_timeout") or 0)
        except (TypeError, ValueError):
            idle = 0
        self.idle_timeout = min(max(idle, 5.0), 120.0) if idle else None
        self.received = received or time.time()

    @property
    def owner_name(self):
        return self.owner or "the owner"


def codeword():
    return os.getenv("JARVIS_OWNER_CODEWORD", "").strip()


def _words(text):
    """Lowercase words without accents and punctuation ('Kastanie!' -> ['kastanie'])."""
    text = unicodedata.normalize("NFKD", (text or "").lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9ß]+", text)


def matches(said, code):
    """True if the code word (one or more words) appears in what was said."""
    code_words, said_words = _words(code), _words(said)
    if not code_words or not said_words or len(said_words) > MAX_GUESS_WORDS:
        return False
    n = len(code_words)
    return any(said_words[i:i + n] == code_words for i in range(len(said_words) - n + 1))


def _distance(a, b):
    """Levenshtein distance (short words only)."""
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def redact(text, code):
    """Replaces the code word in a transcript, also near misses of the speech recognition ("Kastanje" for
    "Kastanie"): they would give the word away just as well. Checking it (matches) stays exact."""
    if not text or not code:
        return text
    text = re.sub(re.escape(code), "[code word]", text, flags=re.IGNORECASE)
    code_words = _words(code)
    if len(code_words) != 1 or len(code_words[0]) < 5:
        return text
    target = code_words[0]
    limit = max(1, len(target) // 4)

    def near(m):
        w = _words(m.group(0))
        if w and abs(len(w[0]) - len(target)) <= limit and _distance(w[0], target) <= limit:
            return "[code word]"
        return m.group(0)
    return re.sub(r"\w+", near, text)


def last_result(kind, now=None):
    """(minutes ago, message) of the previous run of this kind within REPEAT_WINDOW_S, else None."""
    hit = _last_results.get(kind)
    now = now or time.time()
    if not hit or now - hit[0] > REPEAT_WINDOW_S:
        return None
    return (now - hit[0]) / 60, hit[1]


class EventSession:
    """State of one event session: the tools Gemini gets, what it reported, and the result at the end."""

    TOOL_NAMES = ("report_event_result", "verify_owner")

    def __init__(self, event):
        self.event = event
        self.code = codeword()
        self.report = None
        self.owner = False
        self.attempts = 0
        self.turns = []  # (user, jarvis), redacted

    # --- tools -----------------------------------------------------------------------------

    def declarations(self):
        """The tools of this session: report, owner check (only with a code word set), end."""
        from google.genai import types
        from core import prompt, tools
        owner = self.event.owner_name
        language = prompt.language()
        decls = [types.FunctionDeclaration(
            name="report_event_result",
            description=f"Saves your message to {owner}. It is sent when the conversation ends. Call it once the "
                        f"person has answered your questions (not while one is open), and again with the complete "
                        f"message if you learn more.",
            parameters=types.Schema(type=types.Type.OBJECT, properties={
                "message": types.Schema(type=types.Type.STRING, description=(
                    f"Short message to {owner}, written in {language}: who is there, what they are doing, "
                    f"anything they asked you to pass on, and the answers to the questions in your rules "
                    f"(e.g. when something comes back). Plain text, no Markdown.")),
            }, required=["message"]))]
        if self.code:
            decls.append(types.FunctionDeclaration(
                name="verify_owner",
                description=f"Checks the code word of {owner}. Only the result is returned, never the word.",
                parameters=types.Schema(type=types.Type.OBJECT, properties={
                    "code_word": types.Schema(type=types.Type.STRING,
                                              description="Exactly the word(s) the person said as the code word."),
                }, required=["code_word"])))
        decls += [d for d in tools.FUNCTION_DECLARATIONS if d.name == "end_conversation"]
        return decls

    def handles(self, name):
        return name in self.TOOL_NAMES

    def nudge_text(self):
        """Sent as a text turn when nobody answered for NO_ANSWER_NUDGE_S: Gemini Live only speaks on input, so
        without this nudge a prompt rule like "ask once more" never fires."""
        owner = self.event.owner_name
        return (f"[Nobody has answered for {NO_ANSWER_NUDGE_S} seconds. Say once, short: ask if anyone is there and "
                f"that you will tell {owner} about it either way, e.g. \"Hello, is anyone there? I'll let {owner} "
                f"know either way.\" Then stay silent.]")

    def handle(self, name, args):
        owner = self.event.owner_name
        if name == "report_event_result":
            message = str((args or {}).get("message") or "").strip()
            if not message:
                return "Error: the message is empty."
            self.report = redact(message, self.code)
            print(f"[Event] Report: {self.report[:200]}", flush=True)
            return (f"Saved, it goes to {owner} when the conversation ends. Say goodbye briefly and call "
                    f"end_conversation, unless the person is still talking.")
        if name == "verify_owner":
            if not self.code:
                return "No code word is set up."
            if self.owner:
                return f"Already confirmed: this is {owner}."
            if self.attempts >= MAX_ATTEMPTS:
                return "No attempts left. Treat the person as a visitor."
            self.attempts += 1
            if matches((args or {}).get("code_word"), self.code):
                self.owner = True
                print(f"[Event] Code word correct after {self.attempts} attempt(s).", flush=True)
                return (f"Correct, this is {owner}. Greet them briefly, don't call report_event_result, "
                        f"and end the conversation.")
            print(f"[Event] Wrong code word (attempt {self.attempts}/{MAX_ATTEMPTS}).", flush=True)
            left = MAX_ATTEMPTS - self.attempts
            return (f"Wrong. {left} attempt(s) left. Never hint at the right word. Treat the person as a visitor "
                    f"and mention in your report that someone claimed to be {owner} without the right code word.")
        return f"Unknown tool {name}."

    # --- transcript and result ---------------------------------------------------------------

    def redact_text(self, text):
        return redact(text, self.code)

    def add_turn(self, user, jarvis):
        user, jarvis = redact(user, self.code), redact(jarvis, self.code)
        if user or jarvis:
            self.turns.append((user, jarvis))
        return user, jarvis

    def quotes(self):
        said = " / ".join(u for u, _ in self.turns if u)
        return said if len(said) <= QUOTE_CHARS else said[:QUOTE_CHARS - 1] + "…"

    def message(self):
        """The text for the phone, or None when there is nothing to send.

        The person's own words are appended by code, not by Gemini: whoever is in the room can't dictate a
        harmless-sounding report without the owner seeing what was really said."""
        if self.owner:
            return None
        said = self.quotes()
        if self.report:
            return self.report + (f"\n\n🗣 „{said}“" if said else "")
        if said:
            return f"🗣 „{said}“"
        return self.event.silence_message or None

    def result(self, status="done"):
        return {"id": self.event.id, "kind": self.event.kind, "status": status,
                "answered": any(u for u, _ in self.turns), "owner": self.owner,
                "message": self.report or "", "transcript": self.quotes()}

    def finish(self, send=None, fire=None):
        """Sends the message to the phone and the result to Home Assistant. Never raises."""
        from core import notify
        send = send or notify.send
        text = self.message()
        sent = False
        if text:
            sent, detail = send(text, self.event.title or None)
            if not sent:
                print(f"[Event] Message not sent: {detail}", flush=True)
        if not self.owner:
            _last_results[self.event.kind] = (time.time(), self.report or self.quotes() or "nobody answered")
        result = {**self.result(), "sent": bool(sent)}
        _fire(result, fire)
        return result


def reject(event_data, status, fire=None):
    """Answers a trigger that didn't run (busy, expired, invalid) with a result event."""
    data = event_data if isinstance(event_data, dict) else {}
    result = {"id": str(data.get("id") or ""), "kind": str(data.get("kind") or "event"), "status": status,
              "answered": False, "owner": False, "message": "", "transcript": "", "sent": False}
    print(f"[Event] Trigger not run: {status}.", flush=True)
    _fire(result, fire)
    return result


def _fire(result, fire=None):
    try:
        if fire is None:
            from core.tools import ha_client
            fire = ha_client.fire_event
        fire(RESULT_TYPE, result)
    except Exception as e:
        print(f"[Event] Result event failed: {e}", flush=True)
