# jarvis/services/timer.py
import threading
import time
import os
import datetime
from jarvis import state, config
from jarvis.services import sfx

def play_alarm_sound():
    if state.ALARM_PROCESS: return
    state.ALARM_PROCESS = True
    print(" [Timer] Alarm ausgelöst!")
    
    # Timeout Timer starten
    if state.ALARM_TIMEOUT_TIMER:
        state.ALARM_TIMEOUT_TIMER.cancel()
        
    state.ALARM_TIMEOUT_TIMER = threading.Timer(config.ALARM_TIMEOUT, stop_alarm_sound)
    state.ALARM_TIMEOUT_TIMER.start()

    sfx.play_loop(config.ALARM_SOUND)

def stop_alarm_sound():
    if state.ALARM_TIMEOUT_TIMER:
        state.ALARM_TIMEOUT_TIMER.cancel()
        state.ALARM_TIMEOUT_TIMER = None

    if state.ALARM_PROCESS:
        state.ALARM_PROCESS = None
        sfx.stop_loop()
        print(" [Timer] Alarm gestoppt (Timeout oder Manuell).")
        return "Alarm gestoppt."
    return "Kein Alarm aktiv."

def manage_timer_alarm(action, seconds=0, summary="Timer"):
    if action == "set_timer":
        # Check if alarm already exists
        if state.ACTIVE_TIMERS:
            # Get existing time
            existing_ts = state.ACTIVE_TIMERS[0]['timestamp']
            dt_existing = datetime.datetime.fromtimestamp(existing_ts)
            return f"Es ist bereits ein Wecker für {dt_existing.strftime('%H:%M')} Uhr gestellt."

        target_time = time.time() + int(seconds)
        state.ACTIVE_TIMERS.append({
            'timestamp': target_time, 'active': True, 'summary': summary
        })
        dt = datetime.datetime.fromtimestamp(target_time)
        return f"Alarm auf {dt.strftime('%H:%M')} Uhr gestellt."
        
    elif action == "stop_alarm":
        if state.ALARM_PROCESS: return stop_alarm_sound()
        if state.ACTIVE_TIMERS:
            c = len(state.ACTIVE_TIMERS)
            state.ACTIVE_TIMERS.clear()
            return f"{c} Timer gelöscht."
        return "Kein Timer aktiv."
    return "Funktion nicht verstanden."

def background_timer_check():
    while True:
        try:
            now = time.time()
            # Kopie der Liste iterieren, um sicher zu entfernen
            for t in state.ACTIVE_TIMERS[:]:
                if t['active'] and t['timestamp'] <= now:
                    state.ACTIVE_TIMERS.remove(t)
                    play_alarm_sound()
        except Exception as e: print(f"[Timer Error] {e}")
        time.sleep(1)