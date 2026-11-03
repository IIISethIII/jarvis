import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

# Optional: Jarvis delegates what it can't answer to an OpenClaw agent through the Gateway's
# OpenAI-compatible endpoint (gateway.http.endpoints.chatCompletions.enabled = true).
URL = (os.getenv("OPENCLAW_URL") or "").rstrip("/")
TOKEN = os.getenv("OPENCLAW_TOKEN", "")
NAME = os.getenv("OPENCLAW_NAME", "OpenClaw")
AGENT = os.getenv("OPENCLAW_AGENT", "default")
MODEL = os.getenv("OPENCLAW_MODEL", "")  # provider/model; empty = the agent's own model
TIMEOUT = float(os.getenv("OPENCLAW_TIMEOUT", "90"))
SESSION_IDLE_MIN = float(os.getenv("OPENCLAW_SESSION_IDLE_MIN", "30"))
MAX_CHARS = 1500  # tool results go back into the Live session, keep them short

VOICE_RULES = (
    "[Anfrage von Jarvis, dem Sprachassistenten im Smart Home. Suche gründlich, bevor du sagst, dass du etwas nicht hast: "
    "in deinem Gedächtnis und in den Dateien deines Workspace (Notizen, Tracking, Pläne). Deine Antwort wird vorgelesen: "
    "antworte kurz (höchstens vier Sätze) in der Sprache der Anfrage, als Fließtext ohne Markdown, "
    "Listen, Links oder Emojis. Liefere direkt das Ergebnis, ohne Rückfragen.]"
)

_session = {"key": None, "last": 0.0}


def enabled():
    return bool(URL and TOKEN)


def _session_key():
    """Follow-ups share one OpenClaw session; after SESSION_IDLE_MIN without a question a fresh one starts."""
    now = time.time()
    if not _session["key"] or now - _session["last"] > SESSION_IDLE_MIN * 60:
        _session["key"] = f"jarvis-{time.strftime('%Y%m%d-%H%M%S')}"
    _session["last"] = now
    return _session["key"]


def ask(task):
    """Sends a self-contained task to the OpenClaw agent and returns its answer as plain text."""
    if not enabled():
        return f"Fehler: {NAME} ist nicht konfiguriert."
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if MODEL:
        headers["x-openclaw-model"] = MODEL
    payload = {
        "model": f"openclaw/{AGENT}",
        "user": _session_key(),
        "messages": [{"role": "user", "content": f"{VOICE_RULES}\n\n{task}"}],
    }
    start = time.time()
    try:
        r = requests.post(f"{URL}/v1/chat/completions", json=payload, headers=headers, timeout=(5, TIMEOUT))
        r.raise_for_status()
        answer = (r.json()["choices"][0]["message"].get("content") or "").strip()
    except requests.exceptions.Timeout:
        print(f"[OpenClaw] No answer after {time.time() - start:.0f}s", flush=True)
        return f"Fehler: {NAME} hat nicht innerhalb von {TIMEOUT:.0f} Sekunden geantwortet."
    except Exception as e:
        print(f"[OpenClaw] Request failed: {e}", flush=True)
        return f"Fehler: {NAME} ist gerade nicht erreichbar."
    print(f"[OpenClaw] Answer after {time.time() - start:.1f}s ({len(answer)} chars)", flush=True)
    if not answer:
        return f"Fehler: {NAME} hat keine Antwort geliefert."
    return answer[:MAX_CHARS]


if __name__ == "__main__":
    import sys
    print(ask(" ".join(sys.argv[1:]) or "Wie heißt du?"))
