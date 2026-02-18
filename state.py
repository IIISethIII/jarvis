# jarvis/state.py
import threading
from collections import deque
import time

# Conversation History
CONVERSATION_HISTORY = deque()
HISTORY_LOCK = threading.Lock()

# LED State
LED_LOCKED = False

# Device Awareness
AVAILABLE_LIGHTS = {}

HA_CONTEXT = []

# Session Management
SESSION_OPEN_UNTIL = 0

# Timers & Alarms
ACTIVE_TIMERS = []
ALARM_PROCESS = None # Boolean flag or process handle
ALARM_TIMEOUT_TIMER = None

PREVIOUS_VOLUME = None

def open_session(seconds=8):
    global SESSION_OPEN_UNTIL
    SESSION_OPEN_UNTIL = time.time() + seconds

def session_active():
    return time.time() < SESSION_OPEN_UNTIL