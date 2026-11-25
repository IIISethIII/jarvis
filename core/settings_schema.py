"""All .env settings in one place: type, default, help text and group.

Used by the web UI (web/server.py) to render and validate the settings form and by scripts/onboard.py.
The runtime code still reads its values with os.getenv(KEY, default); tests/unit/test_settings_schema.py
checks that those defaults and .env.example match this list. Pure standard library: the web UI imports it
without the Jarvis dependencies.

Fields of a setting:
  kind      text | secret | list | int | float | bool | url
            secret: never sent to the browser; list: comma-separated; bool: "1" / "0"
  default   the value the code uses when the key is missing, as it is written in .env ("" = none)
  min, max  range for int / float
  choices   allowed values (anything else is rejected)
  suggest   offered values, others are allowed (e.g. voices, models)
  applies   "restart": takes effect after a Jarvis restart; "session": at the next wake word (main.py re-reads
            these from .env before every conversation); "web": web UI only
  ui        False hides it from the form (paths used by tests)
  aliases   older key names the code still accepts
"""
import hashlib
import os
from dataclasses import dataclass, field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# config files besides .env that Jarvis reads only at start (label -> path), for fingerprint()
MCP_CONFIG_PATH = os.getenv("JARVIS_MCP_CONFIG") or os.path.join(BASE_DIR, "core", "mcp_config.json")
CONFIG_FILES = {"MCP servers": MCP_CONFIG_PATH}

GROUPS = [
    ("assistant", "Assistant", "Name, language and voice."),
    ("gemini", "Gemini", "API keys and models. Free keys come from aistudio.google.com."),
    ("home_assistant", "Home Assistant", "Optional. Without a URL and token the smart home tools are off."),
    ("openclaw", "OpenClaw", "Optional. Jarvis hands questions it can't answer to an OpenClaw agent."),
    ("memory", "Memory", "Short-term context, archive search and the optional Basic Memory knowledge base."),
    ("learner", "Learner", "Background model that maintains the personality and memory files."),
    ("audio", "Microphone and sound", "Wake word, microphone and the loudness of the UI sounds."),
    ("session", "Conversation timing", "When a conversation ends and how long Jarvis waits for you."),
    ("hardware", "Hardware", "Button and alarms."),
    ("web", "Web interface", "Settings of this page."),
    ("paths", "Paths", "Other locations for the data directory (used by tests)."),
]

GEMINI_VOICES = [
    "Zubenelgenubi", "Puck", "Charon", "Kore", "Fenrir", "Leda", "Orus", "Aoede", "Callirrhoe", "Autonoe",
    "Enceladus", "Iapetus", "Umbriel", "Algieba", "Despina", "Erinome", "Algenib", "Rasalgethi", "Laomedeia",
    "Achernar", "Alnilam", "Schedar", "Gacrux", "Pulcherrima", "Achird", "Vindemiatrix", "Sadachbia",
    "Sadaltager", "Sulafat", "Zephyr",
]


@dataclass(frozen=True)
class Setting:
    key: str
    group: str
    label: str
    help: str = ""
    kind: str = "text"
    default: str = ""
    min: float = None
    max: float = None
    choices: tuple = ()
    suggest: tuple = ()
    applies: str = "restart"
    ui: bool = True
    aliases: tuple = field(default=())


S = Setting
SETTINGS = [
    # Assistant
    S("JARVIS_NAME", "assistant", "Name", "Name of the assistant. The wake word stays 'Hey Jarvis' unless you change the wake word model.",
      default="Jarvis", applies="session"),
    S("JARVIS_LANGUAGE", "assistant", "Language", "Language it answers in by default.", default="English",
      suggest=("Deutsch", "English", "Français", "Español", "Italiano", "Nederlands"), applies="session"),
    S("JARVIS_VOICE", "assistant", "Voice", "Gemini Live voice. Listen to them in Google AI Studio.",
      default="Zubenelgenubi", suggest=tuple(GEMINI_VOICES), applies="session"),

    # Gemini
    S("GEMINI_KEYS", "gemini", "API keys", "One or more Gemini API keys, comma-separated. Jarvis rotates through them "
      "after each conversation and on quota errors.", kind="secret", aliases=("GEMINI_API_KEY", "GEMINI_KEY")),
    S("GEMINI_LIVE_MODEL", "gemini", "Live model", "Model for the voice conversation.",
      default="gemini-3.1-flash-live-preview", suggest=("gemini-3.1-flash-live-preview",), applies="session"),

    # Home Assistant
    S("HA_URL", "home_assistant", "URL", "e.g. http://homeassistant.local:8123", kind="url"),
    S("HA_TOKEN", "home_assistant", "Access token", "Long-lived access token (HA profile -> Security).", kind="secret"),
    S("JARVIS_HA_CONTEXT_CHARS", "home_assistant", "Overview size", "Max characters of the device overview in the "
      "prompt. Above it attributes, then read-only entities are left out.", kind="int", default="30000", min=2000, max=200000, applies="session"),
    S("JARVIS_HA_EXCLUDE", "home_assistant", "Hidden entities", "Entities hidden from the overview and the search, "
      "fnmatch patterns, e.g. weather.*, sensor.dwd_*", kind="list", applies="session"),

    # OpenClaw
    S("OPENCLAW_URL", "openclaw", "Gateway URL", "e.g. http://openclaw-host:18789. Needs "
      "gateway.http.endpoints.chatCompletions.enabled = true.", kind="url"),
    S("OPENCLAW_TOKEN", "openclaw", "Gateway token", "Grants full operator access: only use it on your LAN.", kind="secret"),
    S("OPENCLAW_NAME", "openclaw", "Agent name", "Name Jarvis uses for the agent.", default="OpenClaw"),
    S("OPENCLAW_AGENT", "openclaw", "Agent id", default="default"),
    S("OPENCLAW_MODEL", "openclaw", "Model override", "provider/model; empty = the agent's own model."),
    S("OPENCLAW_TIMEOUT", "openclaw", "Timeout (s)", "Seconds to wait for an answer.", kind="float", default="90", min=5, max=600),
    S("OPENCLAW_SESSION_IDLE_MIN", "openclaw", "New session after (min)", "After this many idle minutes the next "
      "question starts a fresh OpenClaw session.", kind="float", default="30", min=1, max=1440),

    # Memory
    S("JARVIS_CONTEXT_SESSIONS", "memory", "Recent conversations", "How many recent conversations go into the prompt.",
      kind="int", default="3", min=0, max=20, applies="session"),
    S("JARVIS_CONTEXT_MAX_AGE_MIN", "memory", "Max age (min)", "How old the last turn of a recent conversation may be.",
      kind="int", default="30", min=0, max=1440, applies="session"),
    S("JARVIS_EMBEDDINGS", "memory", "Semantic archive search", "Gemini embeddings for the archive search. "
      "Off = keyword search only.", kind="bool", default="1", applies="session"),
    S("JARVIS_EMBED_MODEL", "memory", "Embedding model", default="gemini-embedding-001", applies="session"),
    S("JARVIS_BASIC_MEMORY_PROJECT", "memory", "Basic Memory project", "Project Jarvis reads with bm_search / bm_read "
      "(only if a Basic Memory MCP server is configured).", default="main", applies="session"),

    # Learner
    S("JARVIS_LEARNER", "learner", "Learner on", "Off: Jarvis only reads the workspace files.", kind="bool", default="1"),
    S("JARVIS_LEARNER_MODELS", "learner", "Models", "Text models, tried in this order (503 -> next model, 429 -> next key).",
      kind="list", default="gemini-3.8-flash,gemini-3.6-flash"),
    S("JARVIS_LEARNER_DELAY_MIN", "learner", "Review delay (min)", "Minutes after the last conversation before "
      "ordinary conversations are reviewed. Notes from 'remember' go right away.", kind="float", default="10", min=0, max=1440),
    S("JARVIS_REFLECT_HOUR", "learner", "Nightly reflection hour", "Hour of the nightly reflection, -1 = off.",
      kind="int", default="4", min=-1, max=23),

    # Microphone and sound
    S("JARVIS_WAKE_MODEL", "audio", "Wake word model", "A pretrained openWakeWord model.", default="hey_jarvis",
      suggest=("hey_jarvis", "alexa", "hey_mycroft", "hey_rhasspy")),
    S("JARVIS_WAKE_THRESHOLD", "audio", "Wake word threshold", "Confidence 0-1. Lower = reacts more easily, also to "
      "false alarms. Scores from 60 % of it upwards are logged as near misses.", kind="float", default="0.5", min=0.05, max=0.99),
    S("JARVIS_MIC_HIGHPASS", "audio", "Microphone high-pass (Hz)", "Cutoff against mains hum and rumble, 0 = off.",
      kind="float", default="150", min=0, max=1000),
    S("JARVIS_MIC_GAIN", "audio", "Microphone gain", "Digital gain on what Gemini hears (not the wake word). "
      "Higher helps from far away, too high distorts.", kind="float", default="4", min=0.5, max=16, applies="session"),
    S("JARVIS_SFX_PEAK", "audio", "UI sound loudness", "Peak of the wake, thinking and success sounds as a fraction of "
      "full scale. Speech volume is unaffected.", kind="float", default="0.7", min=0, max=1),

    # Conversation timing
    S("JARVIS_IDLE_TIMEOUT", "session", "Idle timeout (s)", "Seconds of silence after Jarvis spoke before the "
      "conversation ends.", kind="float", default="15", min=3, max=120, applies="session"),
    S("JARVIS_SPEECH_GRACE", "session", "Speech grace (s)", "Extra time for speech Gemini doesn't answer (music, TV), "
      "less the longer Jarvis has been listening.", kind="float", default="6", min=0, max=60, applies="session"),
    S("JARVIS_MAX_SPEECH", "session", "Max speech (s)", "While you speak the timer pauses, for at most this long.",
      kind="float", default="45", min=5, max=300, applies="session"),
    S("JARVIS_ANSWER_WAIT", "session", "Answer wait (s)", "After you stop speaking, wait this long for Gemini to react "
      "before the silence counts.", kind="float", default="8", min=0, max=60, applies="session"),
    S("JARVIS_END_GRACE", "session", "End grace (s)", "After 'goodbye' the conversation closes once it has been quiet "
      "this long; speaking before that cancels the end.", kind="float", default="2", min=0, max=30, applies="session"),
    S("JARVIS_THINKING_DELAY", "session", "Thinking sound delay (s)", "The thinking sound starts only when there is no "
      "answer this long after you stopped speaking.", kind="float", default="0.8", min=0, max=10, applies="session"),
    S("JARVIS_LOCAL_VAD", "session", "Local voice detection", "Silero VAD on the Pi, only logged for now to compare "
      "with Gemini's.", kind="bool", default="1"),
    S("JARVIS_VAD_THRESHOLD", "session", "Local VAD threshold", "Speech probability 0-1 from which a 32 ms frame counts "
      "as speech.", kind="float", default="0.5", min=0.05, max=0.99),

    # Hardware
    S("JARVIS_BUTTON_PIN", "hardware", "Button GPIO", "GPIO of the button that cancels a conversation or stops an alarm. "
      "23 = AIY Voice Bonnet V2, 'none' = no button.", default="23"),
    S("JARVIS_ALARM_TIMEOUT", "hardware", "Alarm stops after (s)", "A ringing alarm stops by itself after this long "
      "(or on the wake word or button).", kind="float", default="120", min=10, max=3600),

    # Web interface
    S("JARVIS_WEB_PASSWORD", "web", "Password", "Set and changed on the page itself; stored as a salted hash. "
      "Plain text also works if you write it into .env by hand.", kind="secret", applies="web", ui=False),
    S("JARVIS_WEB_IDLE_MIN", "web", "Stop after idle (min)", "With socket activation the web server ends after this "
      "many minutes without requests and systemd starts it again on the next visit. 0 = keep running.",
      kind="int", default="10", min=0, max=1440, applies="web"),

    # Paths (tests and special setups)
    S("JARVIS_DATA_DIR", "paths", "Data directory", "Default: data/ in the repo.", ui=False),
    S("JARVIS_WORKSPACE", "paths", "Workspace directory", "Default: data/workspace/.", ui=False),
    S("JARVIS_STATUS_FILE", "paths", "Status file", "Default: data/.status.json.", ui=False),
    S("JARVIS_MCP_CONFIG", "paths", "MCP server config", "Default: core/mcp_config.json.", ui=False),
]

BY_KEY = {s.key: s for s in SETTINGS}
ALIASES = {a: s.key for s in SETTINGS for a in s.aliases}


def session_keys():
    """Keys main.py re-reads from .env before every conversation (with their older names)."""
    return [k for s in SETTINGS if s.applies == "session" for k in (s.key,) + s.aliases]


def fingerprint(values, files=None):
    """{key: short hash} of the .env values Jarvis loads at start (web-only and unknown keys left out), plus
    {label: hash} of the CONFIG_FILES. Jarvis stores it in its status file; the web UI compares it with the
    current state to tell which settings need a restart. Hashes, so the status file holds no secrets."""
    out = {}
    for key, value in values.items():
        s = BY_KEY.get(key)
        if (s is None and key not in ALIASES) or (s is not None and s.applies == "web"):
            continue
        out[key] = hashlib.sha256(f"{key}={value}".encode()).hexdigest()[:12]
    for label, path in (CONFIG_FILES if files is None else files).items():
        try:
            with open(path, "rb") as f:
                content = f.read()
        except OSError:
            content = b""
        out[label] = hashlib.sha256(content).hexdigest()[:12]
    return out


def validate(setting, value):
    """Returns (normalized value, error or None). An empty value means 'not set' (the default applies)."""
    value = (value or "").strip()
    if "\n" in value or "\r" in value:
        return value, "must be one line"
    if "${" in value:
        return value, "may not contain '${' (.env would expand it)"
    if not value:
        return "", None
    kind = setting.kind
    if kind in ("int", "float"):
        try:
            number = int(value) if kind == "int" else float(value)
        except ValueError:
            return value, "must be a whole number" if kind == "int" else "must be a number"
        if setting.min is not None and number < setting.min:
            return value, f"must be at least {setting.min:g}"
        if setting.max is not None and number > setting.max:
            return value, f"must be at most {setting.max:g}"
        return (str(number) if kind == "int" else f"{number:g}"), None
    if kind == "bool":
        if value.lower() in ("1", "true", "on", "yes"):
            return "1", None
        if value.lower() in ("0", "false", "off", "no"):
            return "0", None
        return value, "must be on or off"
    if kind == "url":
        if not value.startswith(("http://", "https://")):
            return value, "must start with http:// or https://"
        return value.rstrip("/"), None
    if kind in ("list", "secret") and "," in value:
        return ",".join(p.strip() for p in value.split(",") if p.strip()), None
    if setting.choices and value not in setting.choices:
        return value, f"must be one of {', '.join(setting.choices)}"
    return value, None
