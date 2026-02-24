import os
import json
import time
import datetime
from jarvis import config, state as global_state
from jarvis.utils import session
import math
import re

import math
import re

def haversine_distance(lat1, lon1, lat2, lon2):
    """
    Computes the great-circle distance between two points on a sphere given their longitudes and latitudes.
    Returns distance in meters.
    """
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return 0
    R = 6371000  # Radius of earth in meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    
    a = math.sin(delta_phi / 2.0) ** 2 + \
        math.cos(phi1) * math.cos(phi2) * \
        math.sin(delta_lambda / 2.0) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# Removed HABITS_FILE

class RoutineTracker:
    # We now strictly rely on HA History for analysis and Mem0 for storage.
    pass

    def analyze_routine(self):
        """
        Refactored: Uses HA History to extract chronological events and uses Mem0 LLM to store daily observations.
        """
        from jarvis.services import ha, google, memory
        
        # 1. Select Entities (Filter)
        all_ctx, _ = ha.fetch_ha_context()
        pattern = re.compile(r"sleep|oneplus|cph2609|mensa|yamaha_receiver|device_tracker|person|todo|zone", re.IGNORECASE)
        relevant_eids = [e['entity_id'] for e in all_ctx if pattern.search(e['entity_id'])]
        
        if not relevant_eids:
            return "No relevant entities found for analysis."

        # 2. Fetch History (last 2 Days for daily chronologies)
        days_back = 2
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
                
                display_name = attributes.get('friendly_name') or entity_id or "Unbekannte Entität"

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
                
                entry = {
                    "timestamp": timestamp,
                    "weekday": weekday,
                    "entity": display_name,
                    "state": custom_state,
                    "attributes": attributes
                }
                all_events.append(entry)
                
        # Sort all events by time to give linear progression
        all_events.sort(key=lambda x: x['timestamp'])

        # --- Base Aggregation & State Timelines ---
        entity_timelines = {}
        for ev in all_events:
            ent = ev['entity']
            if ent not in entity_timelines:
                entity_timelines[ent] = []
            
            timeline = entity_timelines[ent]
            try:
                dt = datetime.datetime.strptime(ev['timestamp'], "%Y-%m-%d %H:%M:%S")
            except:
                continue
                
            state_val = ev['state']
            
            if not timeline:
                timeline.append({"state": state_val, "start": dt, "end": dt})
            else:
                last = timeline[-1]
                if last['state'] == state_val:
                    # Same state continues, just extend end time
                    last['end'] = dt
                else:
                    # State changed
                    timeline.append({"state": state_val, "start": dt, "end": dt})
                    
        # Compile final robust timeline
        final_events = []
        
        # We need a separate pass for GPS-based entities (person) vs state-based entities
        # For non-person entities, we use the simple state clustering
        for ent, timeline in entity_timelines.items():
            if not ent or (not ent.lower().startswith('person') and not ent.lower().startswith('paul')):
                 # Normal state aggregation
                 for stay in timeline:
                     final_events.append({
                         "entity": ent,
                         "state": stay['state'],
                         "start": stay['start'],
                         "end": stay['end']
                     })
                     
        # For Person/Location, we do Geospatial Clustering
        location_events = [e for e in all_events if e['entity'] and (e['entity'].lower().startswith('person') or e['entity'].lower().startswith('paul'))]
        location_events.sort(key=lambda x: x['timestamp'])
        
        stays = []
        if location_events:
            current_stay = None
            
            for ev in location_events:
                try:
                    dt = datetime.datetime.strptime(ev['timestamp'], "%Y-%m-%d %H:%M:%S")
                except: continue
                
                lat = ev['attributes'].get('latitude')
                lon = ev['attributes'].get('longitude')
                addr = ev['attributes'].get('geocoded_location') or ev['attributes'].get('address')
                friendly_name = ev['attributes'].get('friendly_name', ev['entity'])
                
                if current_stay is None:
                    current_stay = {
                        "entity": friendly_name,
                        "start": dt,
                        "end": dt,
                        "lat": lat,
                        "lon": lon,
                        "address": addr,
                        "is_home": ev['attributes'].get('state') == 'home'
                    }
                    continue
                
                # Compare distance to current stay center
                if lat is not None and lon is not None and current_stay['lat'] is not None and current_stay['lon'] is not None:
                     dist = haversine_distance(current_stay['lat'], current_stay['lon'], lat, lon)
                     
                     if dist < 150: # 150m threshold for "same place"
                         # Still here
                         current_stay['end'] = dt
                         if not current_stay['address'] and addr: # Upgrade address if found
                             current_stay['address'] = addr
                     else:
                         # Moved! End previous stay
                         if (current_stay['end'] - current_stay['start']).total_seconds() > 300: # Min 5 mins to be a stay
                              stays.append(current_stay)
                         
                         current_stay = {
                             "entity": friendly_name,
                             "start": dt,
                             "end": dt,
                             "lat": lat,
                             "lon": lon,
                             "address": addr,
                             "is_home": ev['attributes'].get('state') == 'home'
                         }
                else:
                    # Fallback to state changes if GPS is missing
                    if ev['attributes'].get('state') == current_stay.get('state_fallback'):
                        current_stay['end'] = dt
                    else:
                        if (current_stay['end'] - current_stay['start']).total_seconds() > 300:
                             stays.append(current_stay)
                        current_stay = {
                             "entity": friendly_name,
                             "start": dt,
                             "end": dt,
                             "state_fallback": ev['attributes'].get('state')
                         }
            
            # Append last stay
            if current_stay and (current_stay['end'] - current_stay['start']).total_seconds() > 300:
                stays.append(current_stay)

        # Resolve addresses for stays
        for stay in stays:
             place = None
             if stay.get('is_home'):
                 place = "Zuhause"
             elif stay.get('address'):
                 if stay['address'] not in poi_cache:
                     poi_name = google.resolve_location_name(stay['address'])
                     poi_cache[stay['address']] = poi_name or stay['address']
                 place = poi_cache[stay['address']]
             elif stay.get('lat') and stay.get('lon'):
                 key = f"{stay['lat']:.4f},{stay['lon']:.4f}"
                 if key not in poi_cache:
                     poi_name = google.resolve_location_name(f"{stay['lat']}, {stay['lon']}")
                     poi_cache[key] = poi_name or f"GPS: {stay['lat']:.3f},{stay['lon']:.3f}"
                 place = poi_cache[key]
             else:
                 place = stay.get('state_fallback', 'Unbekannt')
                 
             final_events.append({
                 "entity": stay['entity'],
                 "state": f"Aufenthalt bei {place}",
                 "start": stay['start'],
                 "end": stay['end']
             })
             
        # Create Bewegung between stays if there is a gap > 10 mins
        transits = []
        stays.sort(key=lambda x: x['start'])
        for i in range(len(stays) - 1):
             gap = (stays[i+1]['start'] - stays[i]['end']).total_seconds()
             if gap > 600: # 10 mins Gap
                 final_events.append({
                     "entity": stays[i]['entity'],
                     "state": "Bewegung",
                     "start": stays[i]['end'],
                     "end": stays[i+1]['start']
                 })

        # Final Sort
        final_events.sort(key=lambda x: x['start'])
        
        compiled_list = []
        for stay in final_events:
            start_str = stay['start'].strftime("%d.%m. %H:%M")
            duration_mins = int((stay['end'] - stay['start']).total_seconds() / 60)
            
            if duration_mins > 5:
                end_str = stay['end'].strftime("%H:%M")
                compiled_list.append(f"[{start_str} bis {end_str} ({duration_mins} min)] {stay['entity']}: {stay['state']}")
            else:
                compiled_list.append(f"[{start_str}] {stay['entity']}: {stay['state']}")

        processed_log_str = "\n".join(compiled_list)
        
        print(f"   [Routine] Processed {len(final_events)} events into {len(compiled_list)} compiled lines.")
        if not processed_log_str.strip():
            print("   [Routine] WARNING: processed_log_str is EMPTY!")
        
        # 4. LLM Analysis
        prompt = f"""
        Du analysierst die chronologisch zusammengefasste Home Assistant History der letzten {days_back} Tage, um die täglichen Erlebnisse, Routinen und Besonderheiten des Users festzuhalten.
        
        HISTORY DATA (Geclustert nach Aufenthalten und Bewegung):
        {processed_log_str}
        
        TASK:
        1. Fasse den Tagesverlauf detailreich in 2-4 präzisen Sätzen zusammen (z.B. "Am 22.02. verließ der User um 08:30 Uhr das Haus, war 2 Stunden unterwegs zur Bibliothek und ab 16:00 Uhr wieder Zuhause.").
        2. Achte auf Schlaf-/Wachzeiten (aus Lampen/Geräten ableitbar), 'Aufenthalt'-Orte und 'Bewegung'-Zeiten ("In Bewegung").
        3. Formuliere die Sätze streng objektiv und präzise, da sie direkt in eine Memory-Datenbank eingespeist werden.
        4. Wenn es einen Tag ohne Bewegung gab und als Ort nur "Zuhause" auftaucht, berichte klar, dass der User den ganzen Tag zu Hause war.
        5. Output strictly a JSON object mit einem Array "observations", in dem jeder Satz ein eigenes Array-Element ist.
        
        Beispiel:
        {{
            "observations": [
                "Am 22.02. verbrachte der User die meiste Zeit Zuhause und verließ erst gegen 16:30 Uhr das Haus, um für 45 Minuten zu Rewe zu gehen.",
                "Abends war der User ab 19:00 Uhr wieder Zuhause, wobei Aktivitäten bis ca. 23:45 Uhr festgestellt wurden. Er hat vielleicht gekocht"
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
                result_json_str = resp.json()['candidates'][0]['content']['parts'][0]['text']
                result_data = json.loads(result_json_str)
                observations = result_data.get("observations", [])
                
                # Save to Mem0
                if hasattr(memory, 'memory_client') and memory.memory_client:
                    for obs in observations:
                        print(f"   [Routine] Speichere Memory: {obs}")
                        memory.memory_client.add(obs, user_id="paul")
                
                return f"Routine Analysis Complete. {len(observations)} observations saved to Mem0."
            else:
                 return f"LLM Error: {resp.status_code}"
                 
        except Exception as e:
            return f"Analysis failed: {e}"

    def get_habits_summary(self):
        """
        Returns a string representation of learned habits by searching Mem0.
        To avoid high latency during the prompt generation, we do a quick 
        broad search on 'Tagesablauf Routine Gewohnheiten' or keep it lightweight.
        """
        from jarvis.services import memory
        if not hasattr(memory, 'memory_client') or not memory.memory_client:
            return "Noch keine Gewohnheiten gelernt."
            
        try:
            hits = []
            results = memory.memory_client.search(query="Tagesablauf Routine Gewohnheiten", user_id="paul", limit=5)
            if isinstance(results, dict) and 'results' in results:
                results_list = results['results']
            elif isinstance(results, dict) and 'memories' in results:
                results_list = results['memories']
            else:
                results_list = results

            for res in results_list:
                if isinstance(res, dict):
                    memory_text = res.get('memory', '')
                else:
                    memory_text = getattr(res, 'memory', '')
                if memory_text:
                    hits.append(f"- {memory_text}")
            
            if hits:
                return "\n".join(hits)
            return "Wenig Routinedaten."
        except Exception as e:
            return f"Fehler beim Abrufen von Habits: {e}"

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
