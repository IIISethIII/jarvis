# jarvis/state.py
import threading
from collections import deque
import time

# Conversation History
CONVERSATION_HISTORY = deque()
HISTORY_LOCK = threading.Lock()

# Device Awareness
AVAILABLE_LIGHTS = {}

# Session Management
SESSION_OPEN_UNTIL = 0

# Timers & Alarms
ACTIVE_TIMERS = []
ALARM_PROCESS = None # Boolean flag or process handle

PREVIOUS_VOLUME = None

def open_session(seconds=8):
    global SESSION_OPEN_UNTIL
    SESSION_OPEN_UNTIL = time.time() + seconds

def session_active():
    return time.time() < SESSION_OPEN_UNTIL