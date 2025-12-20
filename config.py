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
VOLUME_STEP = 3

# --- VISUALS ---
DIM_BLUE = Color.blend(Color.BLUE, Color.BLACK, 0.3)
DIM_PURPLE = Color.blend(Color.PURPLE, Color.BLACK, 0.4)

# Lila: Zuhören
DIM_PURPLE = Color.blend(Color.PURPLE, Color.BLACK, 0.4)     # Warten auf Input
BRIGHT_PURPLE = Color.blend(Color.PURPLE, Color.BLACK, 0.8)

# --- SAFETY SETTINGS ---
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_CIVIC_INTEGRITY", "threshold": "BLOCK_NONE"}
]