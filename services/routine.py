import os
import json
import time
import datetime
from jarvis import config, state as global_state
from jarvis.utils import session

ROUTINE_LOG_FILE = os.path.join(config.BASE_DIR, "data", "daily_routine.log")
HABITS_FILE = os.path.join(config.BASE_DIR, "data", "habits.json")

# Ensure data directory exists
if not os.path.exists(os.path.dirname(ROUTINE_LOG_FILE)):
    os.makedirs(os.path.dirname(ROUTINE_LOG_FILE))

class RoutineTracker:
    def __init__(self):
        self.last_states = {} # entity_id -> state
        self.ignored_entities = ["sensor.time", "sensor.date"] 
        self.monitored_domains = ["person", "lock", "cover"] # Key domains we care about for habits
        self.monitored_patterns = ["computer", "pc", "tv", "bed", "sleep"] # Keywords in entity_id

    def should_track(self, entity_id):
        domain = entity_id.split(".")[0]
        if domain in self.monitored_domains: return True
        if any(p in entity_id.lower() for p in self.monitored_patterns): return True
        return False

    def log_event(self, entity_id, new_state, friendly_name=None):
        """
        Logs a significant state change.
        """
        # Dedup: If state hasn't changed, don't log (unless it's been a long time? No, keep it simple)
        if self.last_states.get(entity_id) == new_state:
            return

        self.last_states[entity_id] = new_state
        
        # Filter: Only track interesting things
        if not self.should_track(entity_id):
            return

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "timestamp": timestamp,
            "entity_id": entity_id,
            "name": friendly_name or entity_id,
            "state": new_state,
            "weekday": datetime.datetime.now().strftime("%A")
        }
        
        # Log to file
        with open(ROUTINE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
            
        print(f" [Routine] Tracked: {friendly_name} -> {new_state}")

    def analyze_routine(self):
        """
        Called during 'dreaming'. Sends the daily log to LLM to find patterns.
        """
        if not os.path.exists(ROUTINE_LOG_FILE): return
        
        # Read logs
        with open(ROUTINE_LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            
        if len(lines) < 10: return # Not enough data
        
        # Limit to last ~500 events to fit in context
        recent_logs = "".join(lines[-500:])
        
        current_habits = "{}"
        if os.path.exists(HABITS_FILE):
            with open(HABITS_FILE, "r", encoding="utf-8") as f:
                current_habits = f.read()

        prompt = f"""
        Analyze the following Home Assistant event logs and identify the user's DAILY ROUTINE and HABITS.
        
        CURRENT KNOWN HABITS (JSON):
        {current_habits}
        
        NEW EVENT LOGS:
        {recent_logs}
        
        TASK:
        1. Identify consistent patterns (e.g., "Always leaves for work around 08:00 on Weekdays", "Goes to bed around 23:00").
        2. Merge new patterns with known habits. Update times if they shifted.
        3. IGNORE one-off anomalies. Focus on repetition.
        4. Output strictly a JSON object with this structure:
        {{
            "morning_start": "HH:MM",
            "work_start": "HH:MM",
            "work_end": "HH:MM",
            "evening_relax": "HH:MM",
            "bedtime": "HH:MM",
            "detected_patterns": [
                "Leaves house at 07:45 (Mon-Fri)",
                "Turns on PC at 18:00",
                ...
            ]
        }}
        """
        
        try:
            from jarvis.core import llm
            # We use a direct raw call logic here or reuse a helper if available. 
            # Reusing llm.ask_gemini might trigger tools. Let's do a raw call for safety or use a simplified helper.
            # Using the one from memory.py logic:
            url = config.get_gemini_url()
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }
            resp = session.post(url, json=payload, timeout=30)
            if resp.status_code == 200:
                new_habits_json = resp.json()['candidates'][0]['content']['parts'][0]['text']
                
                # Save
                with open(HABITS_FILE, "w", encoding="utf-8") as f:
                    f.write(new_habits_json)
                
                # Clear logs after successful processing
                with open(ROUTINE_LOG_FILE, "w", encoding="utf-8") as f:
                    f.write("")
                
                print(" [Routine] 🧠 Habits updated and logs cleared.")
                
                # Setup next wakeup based on this? 
                # Not here, this is just analysis. The main loop checks this file.
                
        except Exception as e:
            print(f" [Routine] Analysis failed: {e}")

    def get_predicted_wakeup(self):
        """
        Returns the next timestamp where we expect user activity based on habits.
        """
        if not os.path.exists(HABITS_FILE): return None
        
        try:
            with open(HABITS_FILE, "r", encoding="utf-8") as f:
                habits = json.load(f)
            
            now = datetime.datetime.now()
            today_str = now.strftime("%Y-%m-%d")
            
            # Simple logic: Find next milestone in the day
            candidates = []
            for key in ["morning_start", "work_start", "work_end", "evening_relax", "bedtime"]:
                t_str = habits.get(key)
                if t_str:
                    # Parse HH:MM
                    h, m = map(int, t_str.split(":"))
                    dt = datetime.datetime.now().replace(hour=h, minute=m, second=0, microsecond=0)
                    if dt > now:
                        candidates.append((dt, key))
            
            if candidates:
                # Get earliest next event
                candidates.sort(key=lambda x: x[0])
                next_dt, reason = candidates[0]
                return next_dt.timestamp(), f"Routine: {reason}"
                
        except Exception as e:
            print(f" [Routine] Prediction error: {e}")
            
        return None, None

    def get_habits_summary(self):
        """
        Returns a string representation of learned habits.
        """
        if not os.path.exists(HABITS_FILE): return "Noch keine Gewohnheiten gelernt."
        try:
            with open(HABITS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            # Simple Text Format
            lines = []
            if "detected_patterns" in data:
                lines.append("DETECTED PATTERNS:")
                for p in data["detected_patterns"]:
                    lines.append(f"- {p}")
            
            lines.append("SCHEDULE:")
            for k in ["morning_start", "work_start", "work_end", "evening_relax", "bedtime"]:
                if k in data: lines.append(f"- {k}: {data[k]}")
                
            return "\n".join(lines)
        except Exception as e:
            return f"Fehler: {e}"

def check_background_routine():
    """
    Fetches current HA state and logs changes.
    """
    try:
        from jarvis.services import ha
        ctx, _ = ha.fetch_ha_context()
        for device in ctx:
            tracker.log_event(device['entity_id'], device['state'], device['name'])
    except Exception as e:
        print(f" [Routine] Background check failed: {e}")

# Global Instance
tracker = RoutineTracker()
