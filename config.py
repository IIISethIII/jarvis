# jarvis/config.py
import os
from dotenv import load_dotenv
from aiy.leds import Color
import itertools

load_dotenv()

# --- API KEYS & URLS ---
PICOVOICE_KEY = os.getenv("PICOVOICE_KEY")

_keys_env = os.getenv("GEMINI_KEYS") or os.getenv("GEMINI_KEY")
GEMINI_KEYS = [k.strip() for k in _keys_env.split(',') if k.strip()]
_key_cycle = itertools.cycle(GEMINI_KEYS)

GEMINI_STT_KEY = os.getenv("GEMINI_STT_KEY")
GOOGLE_TTS_KEY = os.getenv("GOOGLE_TTS_KEY")
HA_TOKEN = os.getenv("HA_TOKEN")
HA_URL = os.getenv("HA_URL")
MODEL_NAME = os.getenv("GEMINI_MODEL")

def get_gemini_url():
    current_key = next(_key_cycle)
    return f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={current_key}"

def get_next_key():
    return next(_key_cycle)

GEMINI_STT_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={GEMINI_STT_KEY}"

# --- AUDIO SETTINGS ---
WAKE_WORD = "jarvis"
RATE = 16000
CHANNELS = 1
CHUNK_SIZE = 512
VOLUME_STEP = 7 # Volume adjustment step in percentage points

# --- VISUALS ---
DIM_BLUE = Color.blend(Color.BLUE, Color.BLACK, 0.2)
DIM_PURPLE = Color.blend(Color.PURPLE, Color.BLACK, 0.1)     # higher number = dimmer
BRIGHT_PURPLE = Color.blend(Color.PURPLE, Color.BLACK, 0.5)
COLOR_ALARM = (255, 60, 0) # Orange

# --- SAFETY SETTINGS ---
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"}
]

# --- PFADE ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SOUNDS_DIR = os.path.join(BASE_DIR, "assets/sounds") # you may need to adjust this path

SOUND_WAKE = os.path.join(SOUNDS_DIR, "navigation_forward-selection.ogg")
SOUND_THINKING = os.path.join(SOUNDS_DIR, "ui_loading.ogg")
SOUND_SUCCESS = os.path.join(SOUNDS_DIR, "state-change_confirm-up.ogg")
SOUND_ERROR = os.path.join(SOUNDS_DIR, "alert_error-03.ogg")
ALARM_SOUND = os.path.join(SOUNDS_DIR, "alarm_gentle.ogg")
ALARM_TIMEOUT = 120 # Seconds

#  --- LOCATION SETTINGS ---
# Massmann: 48.15354648083181, 11.559096247217958
MY_LAT = 48.15354648083181
MY_LNG = 11.559096247217958

#  --- MEMORY PATHS ---
MEMORY_DIR = os.path.join(BASE_DIR, "memories")
CORE_FILE = os.path.join(MEMORY_DIR, "core.md")         # The "BIOS" (Facts)
EPISODIC_FILE = os.path.join(MEMORY_DIR, "episodic.md") # The "Daily Log"
VECTOR_DB_FILE = os.path.join(MEMORY_DIR, "vectors.json") # Raw Text for search
VECTOR_NPY_FILE = os.path.join(MEMORY_DIR, "vectors.npy") # Embeddings for search
MEM0_DB_DIR = os.path.join(MEMORY_DIR, "mem0_db")       # Mem0 Vector Storage

if not os.path.exists(MEMORY_DIR):
    os.makedirs(MEMORY_DIR)

# Mem0 / LiteLLM looks for GEMINI_API_KEY
if GEMINI_KEYS:
    os.environ["GEMINI_API_KEY"] = GEMINI_KEYS[0]