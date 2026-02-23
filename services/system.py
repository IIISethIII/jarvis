# jarvis/services/system.py
import os
import subprocess
import time
import io
import sys
import math
import datetime
import requests
import random
from jarvis import state

def schedule_wakeup(minutes, reason="Routine Check"):
    """
    Plant einen autonomen Wakeup.
    """
    try:
        minutes = int(minutes)
        if minutes < 1: return "Minuten müssen >= 1 sein."
        
        target_time = time.time() + (minutes * 60)
        state.NEXT_WAKEUP = target_time
        state.WAKEUP_REASON = reason
        
        dt = datetime.datetime.fromtimestamp(target_time)
        return f"Ich werde um {dt.strftime('%H:%M')} Uhr wieder aufwachen. Grund: {reason}"
    except Exception as e:
        return f"Fehler beim Planen: {e}"

def get_bonnet_card_index():
    """
    Dynamically finds the card index for the AIY Voice Bonnet.
    Returns the index (int) or None if not found.
    """
    try:
        with open("/proc/asound/cards", "r") as f:
            lines = f.readlines()
            for line in lines:
                if "aiyvoicebonnet" in line.lower():
                    # The index is the first character of the line (e.g., " 1 [aiyvoicebonnet...]")
                    return int(line.strip().split()[0])
    except Exception as e:
        print(f" [System] Error detecting sound card: {e}")
    return None

def init_audio_settings():
    """Sets ALSA settings dynamically by detecting the Bonnet index."""
    card_idx = get_bonnet_card_index()
    if card_idx is None:
        print(" [Audio Error] AIY Voice Bonnet not found in /proc/asound/cards")
        return False

    print(f" [Audio] Setting Optimized Settings for Card {card_idx}...")
    try:
        # Use the detected card_idx instead of hardcoded numbers
        os.system(f"amixer -c {card_idx} sset 'Mono ADC' 100% > /dev/null 2>&1")
        os.system(f"amixer -c {card_idx} sset 'ADC' 100% > /dev/null 2>&1")
        os.system(f"amixer -c {card_idx} sset 'Mono ADC Boost' 3 > /dev/null 2>&1")
        os.system(f"amixer -c {card_idx} sset 'ADC Boost' 1 > /dev/null 2>&1")
        os.system(f"amixer -c {card_idx} sset 'Speaker' 35% > /dev/null 2>&1")
        os.system(f"amixer -c {card_idx} sset 'Speaker Channel' unmute > /dev/null 2>&1")
        return True
    except Exception as e:
        print(f" [Audio Error] {e}")
        return False

def set_system_volume(volume_level):
    """Sets local system volume using dynamic index detection."""
    card_idx = get_bonnet_card_index()
    if card_idx is None:
        return "Fehler: Soundkarte nicht gefunden."
        
    try:
        vol = max(0, min(100, int(volume_level)))
        os.system(f"amixer -c {card_idx} sset 'Speaker' {vol}%")
        return f"Systemlautstärke auf {vol} Prozent gesetzt (Karte {card_idx})."
    except Exception as e:
        return f"Fehler beim Einstellen der Systemlautstärke: {e}"

def restart_service():
    """Restarts the systemd service via subprocess."""
    print(" [System] Service Restart initiated...")
    try:
        subprocess.Popen(
            "sleep 5; sudo systemctl restart jarvis.service",
            shell=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        return "Der Service startet sich in 5 Sekunden neu."
    except Exception as e:
        print(f" [RESTART ERROR] {e}")
        return f"Fehler: {e}"
    
def run_local_python(code):
    """Runs Python code in a safe local environment and captures the output."""
    print(f"  [Python Tool] Executing:\n{code}")
    
    buffer = io.StringIO()
    sys.stdout = buffer
    
    safe_globals = {
        "math": math,
        "datetime": datetime,
        "requests": requests, 
        "random": random,
        "__builtins__": __builtins__
    }
    
    try:
        exec(code, safe_globals)
        output = buffer.getvalue()
        
        if not output.strip():
            return "Code ausgeführt, aber keine Ausgabe. Hast du print() vergessen?"
            
        return output.strip()
        
    except Exception as e:
        return f"Python Fehler: {str(e)}"
        
    finally:
        sys.stdout = sys.__stdout__