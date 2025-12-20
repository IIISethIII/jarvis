# jarvis/services/timer.py
import threading
import subprocess
import time
import os
import datetime
# WICHTIG: Wir importieren state als Modul, um immer auf die aktuellen Listen zuzugreifen
from jarvis import state 

def alarm_loop():
    """Plays alarm sound repeatedly."""
    while state.ALARM_PROCESS:
        p = subprocess.Popen(["mpg123", "-f", "4096", "/home/pi/alarm.mp3"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        p.wait()
        for _ in range(20):
            if not state.ALARM_PROCESS: return
            time.sleep(0.1)

def play_alarm_sound():
    if state.ALARM_PROCESS: return
    state.ALARM_PROCESS = True
    t = threading.Thread(target=alarm_loop, daemon=True)
    t.start()

def stop_alarm_sound():
    if state.ALARM_PROCESS:
        state.ALARM_PROCESS = None
        os.system("killall mpg123 2>/dev/null")
        return "Alarm gestoppt."
    return "Kein Alarm aktiv."

def manage_timer_alarm(action, seconds=0, summary="Timer"):
    if action == "set_timer":
        target_time = time.time() + int(seconds)
        state.ACTIVE_TIMERS.append({
            'timestamp': target_time,
            'active': True,
            'summary': summary
        })
        dt_target = datetime.datetime.fromtimestamp(target_time)
        return f"Alarm auf {dt_target.strftime('%H:%M')} Uhr gestellt."
        
    elif action == "stop_alarm":
        # 1. Priorität: Klingelnden Wecker stoppen
        if state.ALARM_PROCESS:
            return stop_alarm_sound()
        
        # 2. Priorität: Laufende Timer löschen
        if state.ACTIVE_TIMERS:
            count = len(state.ACTIVE_TIMERS)
            state.ACTIVE_TIMERS.clear()
            return f"{count} laufende(r) Timer gelöscht."
            
        return "Kein Timer oder Alarm aktiv."
        
    return "Funktion nicht verstanden."

def background_timer_check():
    while True:
        try:
            now = time.time()
            for t in state.ACTIVE_TIMERS[:]:
                if t['active'] and t['timestamp'] <= now:
                    if t in state.ACTIVE_TIMERS: # Check before remove
                        state.ACTIVE_TIMERS.remove(t)
                        play_alarm_sound()
        except Exception as e:
            print(f"[Timer Thread Error] {e}")
        time.sleep(1)