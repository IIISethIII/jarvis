import os
import json
import time
import datetime
from jarvis import config, state as global_state
from jarvis.utils import session
import math

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
        
        # Location Stuff
        self.last_location = {} # entity_id -> (lat, lon, timestamp)
        self.current_stop = {}  # entity_id -> (lat, lon, start_time)
        self.MIN_STOP_DURATION = 15 * 60 # 15 Minutes to count as "Visit"
        self.MOVE_THRESHOLD = 0.100      # 100 meters (approx)

    def should_track_location(self, entity_id, attributes):
        if not entity_id.startswith("person."): return False
        if "latitude" not in attributes or "longitude" not in attributes: return False
        return True

    def get_dist(self, lat1, lon1, lat2, lon2):
        # Haversine approx (km)
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
        return R * c

    def track_location(self, entity_id, attributes, friendly_name):
        try:
            lat = float(attributes['latitude'])
            lon = float(attributes['longitude'])
            now_ts = time.time()
            
            # 1. Update Movement Check
            if entity_id not in self.last_location:
                 self.last_location[entity_id] = (lat, lon, now_ts)
                 return

            last_lat, last_lon, last_ts = self.last_location[entity_id]
            dist = self.get_dist(last_lat, last_lon, lat, lon) # in km
            
            # A) MOVEMENT DETECTED (> 100m)
            if dist > self.MOVE_THRESHOLD:
                # If we were in a stop, close it
                if entity_id in self.current_stop:
                    start_lat, start_lon, start_time = self.current_stop[entity_id]
                    duration = (now_ts - start_time) / 60 # mins
                    
                    if duration >= 15: # Only log real stops
                        self.log_stop(entity_id, friendly_name, start_lat, start_lon, start_time, now_ts)
                    
                    del self.current_stop[entity_id]
                
                # Update last known pos
                self.last_location[entity_id] = (lat, lon, now_ts)

            # B) NO MOVEMENT (Stationary)
            else:
                # If not in a stop, start one
                if entity_id not in self.current_stop:
                    # We start counting from when we *first* saw this loc (last_ts)
                    self.current_stop[entity_id] = (last_lat, last_lon, last_ts)
                
                # We don't update last_location timestamp here, so dist stays low relative to anchor
                # Wait.. actually we should keep the anchor as the 'stop center' logic. 
                # Current 'last_location' acts as anchor. Correct.
                pass
                
        except Exception as e:
            print(f"LocTrack Error: {e}")

    def log_stop(self, entity_id, name, lat, lon, start_ts, end_ts):
        duration_min = int((end_ts - start_ts) / 60)
        t_str = datetime.datetime.fromtimestamp(start_ts).strftime("%H:%M")
        
        # Log entry
        entry = {
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entity_id": entity_id,
            "name": name,
            "state": f"Stopped at {lat:.4f}, {lon:.4f} for {duration_min} min",
            "lat": lat, 
            "lon": lon,
            "weekday": datetime.datetime.now().strftime("%A")
        }
        with open(ROUTINE_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        print(f" [Routine] 📍 Stop Detected: {name} @ {lat:.4f},{lon:.4f} ({duration_min} min)")

    def reverse_geocode(self, lat, lon):
        try:
            # Free OSM Nominatim (Please respect Usage Policy: User-Agent required)
            url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=18&addressdetails=1"
            headers = {"User-Agent": "JarvisAI_Personal_Assistant"} 
            r = session.get(url, headers=headers, timeout=2)
            if r.status_code == 200:
                data = r.json()
                # Try to get a specific name first, fallback to the full address string, then default
                return data.get('name') or data.get('display_name') or "Unbekannter Ort"
        except: pass
        return None

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
        
        # --- PRE-PROCESS LOCATIONS ---
        # Find raw coordinates in logs and resolve them
        import re
        # Pattern: Stopped at 48.1234, 11.5678
        coords = re.findall(r"Stopped at (\d+\.\d+), (\d+\.\d+)", recent_logs)
        
        # De-duplicate to save API calls
        unique_coords = set(coords)
        
        resolved_map = {}
        for lat_str, lon_str in unique_coords:
            # Only resolve if it appears often enough? For now just resolve all unique stops.
            place_name = self.reverse_geocode(float(lat_str), float(lon_str))
            if place_name:
                resolved_map[f"{lat_str}, {lon_str}"] = place_name
                time.sleep(1.0) # Respect OSM API limit
        
        # Replace in logs
        for coord_str, name in resolved_map.items():
            recent_logs = recent_logs.replace(coord_str, f"'{name}' ({coord_str})")
        
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
        if not os.path.exists(HABITS_FILE): return None, None
        
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
            
            # Check Location
            if tracker.should_track_location(device['entity_id'], device.get('attributes', {})):
                tracker.track_location(device['entity_id'], device['attributes'], device['name'])
                
    except Exception as e:
        print(f" [Routine] Background check failed: {e}")

# Global Instance
tracker = RoutineTracker()
