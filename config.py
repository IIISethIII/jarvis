# jarvis/config.py
import os
from dotenv import load_dotenv
from aiy.leds import Color

load_dotenv()

# --- API KEYS & URLS ---
PICOVOICE_KEY = os.getenv("PICOVOICE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_KEY")
GOOGLE_TTS_KEY = os.getenv("GOOGLE_TTS_KEY")
HA_TOKEN = os.getenv("HA_TOKEN")
HA_URL = os.getenv("HA_URL")
MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent?key={GEMINI_API_KEY}"

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

#  --- LOCATION SETTINGS ---
# Biederstein: 48.1662948905428, 11.596001304713784
MY_LAT = 48.1662948905428
MY_LNG = 11.596001304713784

#  --- MEMORY PATHS ---
MEMORY_FILE = os.path.join(BASE_DIR, "memory_data.json")
VECTOR_FILE = os.path.join(BASE_DIR, "memory_vectors.npy")