import os
import json
import time
import datetime
from jarvis import config, state as global_state
from jarvis.utils import session
import math
import re
HABITS_FILE = os.path.join(config.BASE_DIR, "data", "habits.json")

# Ensure data directory exists
if not os.path.exists(os.path.dirname(HABITS_FILE)):
    try:
        os.makedirs(os.path.dirname(HABITS_FILE))
    except OSError: pass



class RoutineTracker:
    # Removed: should_track, log_event, track_location, get_dist, reverse_geocode etc.
    # We now strictly rely on HA History for analysis.
    pass

    def analyze_routine(self):
        """
        Refactored: Uses HA History instead of local logs.
        """
        from jarvis.services import ha, google
        
        # 1. Select Entities (Filter)
        all_ctx, _ = ha.fetch_ha_context()
        pattern = re.compile(r"sleep|oneplus|cph2609|mensa|yamaha_receiver|device_tracker|person|todo|zone", re.IGNORECASE)
        relevant_eids = [e['entity_id'] for e in all_ctx if pattern.search(e['entity_id'])]
        
        if not relevant_eids:
            return "No relevant entities found for analysis."

        # 2. Fetch History (last 3 Days)
        days_back = 7
        now = datetime.datetime.now()
        start_time = now - datetime.timedelta(days=days_back)
        
        print(f" [Routine] Fetching history for {len(relevant_eids)} entities since {start_time}...")
        history_data = ha.get_ha_history(relevant_eids, start_time, now)
        
        if not history_data:
            return "No history data returned."

        # 3. Process Data
        processed_log_str = ""
        poi_cache = {} # address/loc -> name
        
        # history_data is list of lists
        all_events = []
        for entity_history in history_data:
            # Sort just in case? Usually sorted.
            # Limit context spam per entity
            # Taking last 50 events per entity should be enough for routine pattern
            top_events = entity_history[-50:] 
            
            for event in top_events:
                s = event.get('state')
                if s in ["unknown", "unavailable", ""]: continue
                
                ts_str = event.get('last_changed', '')
                try:
                    # Convert to local time string for LLM readability
                    dt = datetime.datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    local_dt = dt.astimezone() # Local system time
                    timestamp = local_dt.strftime("%Y-%m-%d %H:%M:%S")
                    weekday = local_dt.strftime("%A")
                except:
                    timestamp = ts_str
                    weekday = ""

                entity_id = event.get('entity_id')
                attributes = event.get('attributes', {})
                
                # --- Location Intelligent Resolution ---
                custom_state = s
                
                # Priority: Geocoded Location > GPS > State
                # Check for address in attributes (generic or specific)
                addr = attributes.get('geocoded_location') or attributes.get('address')
                lat = attributes.get('latitude')
                lon = attributes.get('longitude')
                
                display_name = attributes.get('friendly_name', entity_id)

                if addr:
                    # POI Resolve
                    if addr not in poi_cache:
                        print(f"   [Routine] Resolving POI for: {addr}")
                        poi_name = google.resolve_location_name(addr)
                        poi_cache[addr] = poi_name or addr # Fallback to raw address
                    
                    place = poi_cache[addr]
                    custom_state = f"At {place}"
                
                elif lat and lon:
                    # Reverse Geo if no address
                    # Round coords to cache better (approx 11m precision is 4 decimals)
                    key = f"{lat:.4f},{lon:.4f}"
                    if key not in poi_cache:
                         print(f"   [Routine] Reverse Geocoding: {key}")
                         # Use existing reverse_geocode logic? We deleted it. 
                         # We can use google's search or re-implement simply if allowed.
                         # But wait, google.resolve_location_name expects address.
                         # We could ask google "What is at lat, lon?"
                         # Let's try to ask Gemini with lat/lon directly if we have no address.
                         poi_name = google.resolve_location_name(f"{lat}, {lon}") # Works often
                         poi_cache[key] = poi_name or f"GPS: {lat:.3f},{lon:.3f}"
                    
                    place = poi_cache[key]
                    custom_state = f"At {place}"
                
                # Filter spammy updates (e.g. slight GPS drift or sun elevation)
                # We already filtered entity_ids.
                # Just append.
                
                entry = {
                    "timestamp": timestamp,
                    "weekday": weekday,
                    "entity": display_name,
                    "state": custom_state
                }
                all_events.append(entry)

        # Sort all events by time to give linear progression
        all_events.sort(key=lambda x: x['timestamp'])
        
        # Convert to string
        processed_log_str = json.dumps(all_events, indent=1, ensure_ascii=False)
        
        # 4. LLM Analysis (Same as before)
        current_habits = "{}"
        if os.path.exists(HABITS_FILE):
             with open(HABITS_FILE, "r", encoding="utf-8") as f:
                 current_habits = f.read()

        prompt = f"""
        Analyze the following Home Assistant history ({days_back} days) and identify the user's DAILY ROUTINE and HABITS.
        
        CURRENT KNOWN HABITS (JSON):
        {current_habits}
        
        NEW HISTORY DATA:
        {processed_log_str}
        
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
            url = config.get_gemini_url()
            payload = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json"}
            }
            resp = session.post(url, json=payload, timeout=45)
            if resp.status_code == 200:
                new_habits_json = resp.json()['candidates'][0]['content']['parts'][0]['text']
                
                with open(HABITS_FILE, "w", encoding="utf-8") as f:
                    f.write(new_habits_json)
                
                return f"Routine Analysis Complete. Habits updated."
            else:
                 return f"LLM Error: {resp.status_code}"
                 
        except Exception as e:
            return f"Analysis failed: {e}"

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
    Background check logic using HA history is not periodic per se, 
    but we might want to keep this hook for other routine things.
    Current requirement says: "instead of manually saving the states".
    So we don't log things here anymore.
    """
    pass

# Global Instance
tracker = RoutineTracker()
