"""The learner: a text model that maintains the workspace files (core/workspace.py) in the background.

Jarvis itself never edits files. It passes explicit requests on with the tool 'remember', and the learner works them
in after the session. Runs:
  - notes:      right after a session in which Jarvis called 'remember'
  - session:    JARVIS_LEARNER_DELAY_MIN after the last session, over all sessions not reviewed yet
  - reflection: once a night (JARVIS_REFLECT_HOUR): where was Jarvis unhelpful, what should change?
  - import / onboarding: scripts/import_facts.py and scripts/onboard.py

One run is one structured request: the model gets the current files and returns single changes (add / replace /
remove an entry), which workspace.Run validates and applies. Failed changes get one correction round.
It never rewrites whole files, so entries don't drift over time.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta

from dotenv import load_dotenv

from core import memory, prompt, workspace

load_dotenv()

MODELS = [m.strip() for m in os.getenv("JARVIS_LEARNER_MODELS", "gemini-3.8-flash,gemini-3.6-flash").split(",") if m.strip()]
DELAY_MIN = float(os.getenv("JARVIS_LEARNER_DELAY_MIN", "10"))
NOTES_DELAY_S = 5
REFLECT_HOUR = int(os.getenv("JARVIS_REFLECT_HOUR", "4"))  # -1 disables the nightly reflection
RETRY_MIN = 30  # background runs: next try after all retries failed
RETRY_PAUSES_S = [20, 60, 180]  # pauses between rounds over all models and keys
MAX_CHANGES = {"notes": 8, "session": 5, "reflection": 8, "import": 80, "onboarding": 20}
TRANSCRIPT_CHARS = {"session": 30_000, "notes": 30_000, "reflection": 60_000}
REFLECT_DAYS = 3
TRIVIAL_USER_CHARS = 40  # sessions with less user text and no tool error are skipped (e.g. "Licht an")
STATE_FILE = ".learner.json"

TASKS = {
    "notes": (
        "Below are notes the assistant passed on with 'remember' and the conversations they come from. "
        "The notes are explicit requests of the user: carry them out. The user may change anything, including SOUL and AGENTS. "
        "Also look at the conversations for corrections, new lasting facts or preferences and procedures worth a skill, "
        "but most conversations contain nothing to learn."
    ),
    "session": (
        "Below are recent conversations with their tool calls. Look for corrections by the user, new lasting facts or "
        "preferences, procedures that only worked after several attempts (worth a skill) and skills that misled the "
        "assistant. Most conversations contain nothing to learn: then return no changes."
    ),
    "reflection": (
        "This is your nightly reflection over the conversations of the last days. Find where the assistant was not "
        "helpful: it misunderstood, needed several attempts, gave up, said it doesn't know, was corrected, or the user "
        "had to repeat themselves. If a rule in AGENTS, SOUL or USER or a skill would have prevented it, add or adjust "
        "it. Also remove entries that are clearly outdated or contradicted, merge true duplicates, shorten files above "
        "90 % of their budget, and delete skills not used for 30 days. Be conservative: a few precise changes beat "
        "many vague ones, and no change is a good result."
    ),
    "import": (
        "Below is text to import, e.g. an old memory file or notes about the user. Sort EVERYTHING useful into the "
        "right files and skills; nothing useful may get lost. Routines and multi-step procedures become skills. "
        "Personality descriptions and explicit rules keep their original wording, including their examples: copy "
        "them, don't paraphrase or shorten them. Plain facts may be compressed: merge long lists of similar facts "
        "(e.g. equipment, skills, heart rate zones) into one compact entry each, and group MEMORY with '## ' "
        "sections per topic. Drop noise: error messages, vague guesses, outdated or contradicted items, duplicates, "
        "places the user regularly goes to or stays at, habits guessed from phone or location data, and statements "
        "like 'X is no longer Y' that give no current value. If the input describes a personality, it replaces the "
        "English template entries in SOUL (replace or remove them). "
        "If the files already contain imported entries, only add what is still missing."
    ),
    "onboarding": (
        "Below are the user's answers from the onboarding. Create the first entries of SOUL and USER from them "
        "(replace template entries in SOUL that contradict the answers)."
    ),
}


def _schema():
    from google.genai import types
    S, T = types.Schema, types.Type
    change = S(type=T.OBJECT, properties={
        "target": S(type=T.STRING, enum=["SOUL", "AGENTS", "USER", "MEMORY", "skill", "none"]),
        "action": S(type=T.STRING, enum=["add", "replace", "remove", "create", "delete", "undo"]),
        "skill": S(type=T.STRING, description="skill name (target skill)"),
        "old_text": S(type=T.STRING, description="exact text from the current file (replace, remove)"),
        "text": S(type=T.STRING, description="the new entry or replacement; for a skill 'create': ALL steps of the skill"),
        "section": S(type=T.STRING, description="optional existing '## ' heading for add"),
        "description": S(type=T.STRING, description="when to use the skill (create)"),
        "reason": S(type=T.STRING),
    }, required=["target", "action", "reason"])
    return S(type=T.OBJECT, properties={
        "changes": S(type=T.ARRAY, items=change),
        "summary": S(type=T.STRING, description="one sentence: what was learned, or why nothing"),
    }, required=["changes", "summary"])


def _api_keys():
    keys_env = os.getenv("GEMINI_KEYS") or os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_KEY") or ""
    return [k.strip() for k in keys_env.split(",") if k.strip()]


def _generate(system, user_text, models=None):
    """One JSON request with fallback: 503/overload -> next model, 429 -> next key. Returns (dict, model)."""
    from google import genai
    from google.genai import types
    config = types.GenerateContentConfig(
        system_instruction=system, temperature=0.2,
        response_mime_type="application/json", response_schema=_schema(),
        http_options=types.HttpOptions(timeout=180_000),
    )
    last = None
    # Overload (503/504) is usually gone after a while: retry the whole chain with growing pauses
    # instead of falling back to a weaker model that paraphrases and loses entries.
    for attempt, pause in enumerate([0] + RETRY_PAUSES_S):
        if pause:
            print(f"[Learner] All models busy, retry {attempt}/{len(RETRY_PAUSES_S)} in {pause}s", flush=True)
            time.sleep(pause)
        overloaded = False
        for model in models or MODELS:
            for key in _api_keys():
                try:
                    client = genai.Client(api_key=key)
                    resp = client.models.generate_content(model=model, contents=user_text, config=config)
                    return json.loads(resp.text), model
                except Exception as e:
                    last = e
                    msg = str(e).lower()
                    print(f"[Learner] {model} failed: {str(e)[:120]}", flush=True)
                    if "429" in msg or "resource_exhausted" in msg or "quota" in msg:
                        continue  # quota is per project: another key may belong to another project
                    overloaded = True
                    break  # overloaded or broken model: try the next model
        if not overloaded:
            break  # only quota errors (free tier: 20 requests per day): waiting minutes won't help
    raise RuntimeError(f"all learner models failed: {last}")


def _files_block():
    parts = []
    for name, (fname, budget) in workspace.FILES.items():
        text = workspace.read_file(name)
        pct = round(100 * len(text) / budget)
        parts.append(f"=== FILE {name} ({len(text)}/{budget} characters, {pct} %) ===\n{text.strip()}")
    usage = workspace.skill_usage()
    skills = workspace.list_skills()
    if skills:
        parts.append("=== SKILLS ===")
        for s in skills:
            parts.append(f"--- skill {s['name']} (last used: {usage.get(s['name'], 'never')}) ---\n{s['text'].strip()}")
    else:
        parts.append("=== SKILLS ===\n(none yet)")
    changes = workspace.recent_changes(max_items=15, max_age_days=14)
    if changes:
        parts.append("=== YOUR RECENT CHANGES ===\n" + "\n".join(changes))
    return "\n\n".join(parts)


def _format_sessions(sessions, limit):
    """Transcripts (oldest first) with tool calls, cut from the oldest side to `limit` characters."""
    blocks = []
    for sid, turns in sessions:
        rows = [f"[conversation {turns[0].get('timestamp', '')}]"]
        for t in turns:
            if t.get("user"):
                rows.append(f"User: {t['user']}")
            for call in t.get("tools", []):
                rows.append(f"  tool {call.get('name')}({json.dumps(call.get('args', {}), ensure_ascii=False)}) -> {call.get('result', '')}")
            if t.get("assistant"):
                rows.append(f"Assistant: {t['assistant']}")
        blocks.append("\n".join(rows))
    text = "\n\n".join(blocks)
    return text[-limit:] if len(text) > limit else text


def _system(mode):
    budgets = {f"budget_{n.lower()}": b for n, (_, b) in workspace.FILES.items()}
    text = prompt.render(prompt._read(os.path.join(prompt.PROMPTS_DIR, "learner.md")),
                         max_changes=MAX_CHANGES[mode], budget_skill=workspace.SKILL_BUDGET, **budgets)
    return text + f"\n=== YOUR TASK NOW ({mode}) ===\n{TASKS[mode]}\n"


def run(mode, input_text, source=None, models=None):
    """One learner run. input_text is the mode-specific material (notes, transcripts, import text).
    Returns the list of applied change descriptions."""
    system = _system(mode)
    request = f"{_files_block()}\n\n=== INPUT ===\n{input_text}\n\nToday is {datetime.now().strftime('%Y-%m-%d %A')}."
    result, model = _generate(system, request, models)
    changes = (result.get("changes") or [])[:MAX_CHANGES[mode]]
    workspace._write(workspace._path(".learner-last.json"), json.dumps({"model": model, "result": result}, ensure_ascii=False, indent=1))
    ws_run = workspace.Run(f"{source or mode}, {model}")
    if mode == "session":
        # personality and house rules change only on request (notes), in the reflection or by import
        ws_run.locked = {"SOUL", "AGENTS"}
    errors = _apply_all(ws_run, changes)
    errors += [f"skill {n} was created without steps -> ERROR: send action 'replace' with old_text '' and ALL steps as text"
               for n in ws_run.empty_new_skills()]
    if errors:
        # one correction round for the failed changes, against the state including the successful ones
        retry = (f"{request}\n\n=== SOME OF YOUR CHANGES FAILED ===\n" + "\n".join(errors) +
                 "\n\nThe files above are the state BEFORE your changes; the successful ones are already applied. "
                 "Return corrected versions of ONLY the failed changes (or none). Current state of the affected files:\n\n" +
                 "\n\n".join(f"=== FILE {n} ===\n{ws_run.files[n]}" for n in workspace.FILES) +
                 "\n\n" + "\n\n".join(f"=== SKILL {n} ===\n{t}" for n, t in ws_run.skills.items() if t is not None))
        try:
            fixed, model = _generate(system, retry, models)
            errors = _apply_all(ws_run, (fixed.get("changes") or [])[:len(errors) * 3])
            errors += [f"skill {n} still has no steps, dropped" for n in ws_run.empty_new_skills()]
        except RuntimeError as e:
            print(f"[Learner] Correction round failed: {e}", flush=True)
        for err in errors:
            print(f"[Learner] Change rejected: {err}", flush=True)
    applied = ws_run.commit()
    print(f"[Learner] {mode} run with {model}: {len(applied)} change(s). {result.get('summary', '')}", flush=True)
    for line in applied:
        print(f"[Learner]   {line}", flush=True)
    return applied


def _apply_all(ws_run, changes):
    errors = []
    for c in changes:
        if str(c.get("target", "")).lower() == "none" and c.get("action") != "undo":
            continue
        err = ws_run.apply(c)
        if err:
            errors.append(f"{json.dumps(c, ensure_ascii=False)[:400]} -> ERROR: {err}")
    return errors


# --- Scheduling in the Jarvis process ------------------------------------------------

def _load_state():
    try:
        return json.loads(workspace._read(workspace._path(STATE_FILE)) or "{}")
    except ValueError:
        return {}


def _save_state(state):
    workspace._write(workspace._path(STATE_FILE), json.dumps(state, indent=1))


def _sessions_from_archive(since=None):
    """[(session id, turns)] from the archive tail, oldest first."""
    entries = memory._read_entries(memory.SEARCH_WINDOW_BYTES)
    sessions = {}
    for e in entries:
        sid = e.get("session")
        if not sid:
            continue
        if since and e.get("timestamp", "") < since:
            continue
        sessions.setdefault(sid, []).append(e)
    return list(sessions.items())


def _trivial(turns):
    user_chars = sum(len(t.get("user", "")) for t in turns)
    failed = any(("error" in c.get("result", "").lower() or "fehler" in c.get("result", "").lower())
                 for t in turns for c in t.get("tools", []))
    return user_chars < TRIVIAL_USER_CHARS and not failed


def review_pending():
    """Reviews new sessions and notes (modes notes / session). Returns False if the models failed."""
    state = _load_state()
    done = set(state.get("reviewed", []))
    notes = workspace.read_notes()
    sessions = [(sid, t) for sid, t in _sessions_from_archive() if sid not in done]
    if "reviewed" not in state:
        # first start: don't review the whole old archive, only what comes from now on
        note_sessions = {n.get("session") for n in notes}
        state["reviewed"] = [sid for sid, _ in sessions if sid not in note_sessions][-500:]
        sessions = [(sid, t) for sid, t in sessions if sid in note_sessions]
        _save_state(state)
        if not notes:
            return True
    try:
        learn_from(sessions, notes)
    except Exception as e:
        print(f"[Learner] Run failed, retrying later: {e}", flush=True)
        return False
    state["reviewed"] = (state.get("reviewed", []) + [sid for sid, _ in sessions])[-500:]
    _save_state(state)
    return True


def learn_from(sessions, notes):
    """Runs the learner on new sessions [(id, turns)] and notes, skipping trivial sessions.
    Processed notes are removed from the inbox. Returns the applied changes ([] when nothing was worth a run)."""
    worth = [(sid, t) for sid, t in sessions if not _trivial(t)]
    if notes:
        note_sessions = {n.get("session") for n in notes}
        worth = worth or [(sid, t) for sid, t in sessions if sid in note_sessions]
    if not notes and not worth:
        return []
    mode = "notes" if notes else "session"
    parts = []
    if notes:
        parts.append("=== NOTES FROM THE ASSISTANT ===\n" + "\n".join(f"[{n['time']}] {n['note']}" for n in notes))
    if worth:
        parts.append("=== CONVERSATIONS ===\n" + _format_sessions(worth, TRANSCRIPT_CHARS[mode]))
    applied = run(mode, "\n\n".join(parts))
    if notes:
        workspace.drop_notes(len(notes))
    return applied


def reflect():
    """Nightly reflection over the last REFLECT_DAYS days."""
    since = (datetime.now() - timedelta(days=REFLECT_DAYS)).strftime("%Y-%m-%d")
    sessions = _sessions_from_archive(since)
    if not sessions:
        return True
    try:
        run("reflection", "=== CONVERSATIONS OF THE LAST DAYS ===\n" + _format_sessions(sessions, TRANSCRIPT_CHARS["reflection"]))
    except Exception as e:
        print(f"[Learner] Reflection failed: {e}", flush=True)
        return False
    return True


class Scheduler:
    """Background thread in the Jarvis process. Runs only while no session is active (is_busy)."""

    def __init__(self):
        self._due = None
        self._last_reflect_try = 0.0
        self._wake = threading.Event()
        self._is_busy = lambda: False

    def start(self, is_busy):
        if os.getenv("JARVIS_LEARNER", "1") == "0" or not _api_keys():
            print("[Learner] Off.", flush=True)
            return
        self._is_busy = is_busy
        workspace.ensure()
        if workspace.read_notes():
            self._due = time.time() + NOTES_DELAY_S
        else:
            self._due = time.time() + DELAY_MIN * 60
        threading.Thread(target=self._loop, name="learner", daemon=True).start()
        print(f"[Learner] On ({', '.join(MODELS)}).", flush=True)

    def session_ended(self):
        """Called after every Live session."""
        delay = NOTES_DELAY_S if workspace.read_notes() else DELAY_MIN * 60
        self._due = time.time() + delay
        self._wake.set()

    def _loop(self):
        while True:
            self._wake.wait(30)
            self._wake.clear()
            if self._is_busy():
                continue
            now = time.time()
            if self._due and now >= self._due:
                self._due = None
                if not review_pending():
                    self._due = time.time() + RETRY_MIN * 60
            if REFLECT_HOUR >= 0 and datetime.now().hour == REFLECT_HOUR and now - self._last_reflect_try > 20 * 60:
                self._last_reflect_try = now
                state = _load_state()
                today = datetime.now().strftime("%Y-%m-%d")
                if state.get("last_reflection") != today:
                    if reflect():
                        state = _load_state()
                        state["last_reflection"] = today
                        _save_state(state)


scheduler = Scheduler()
