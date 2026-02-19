# jarvis/services/ha.py
import datetime
import urllib.parse
import json
from jarvis.config import HA_URL, HA_TOKEN, VOLUME_STEP
from jarvis.utils import session
from jarvis import state as global_state 

def fetch_ha_context():
    """
    Holt den Status aller Geräte.
    """
    if not HA_URL or not HA_TOKEN: return [], {}
    
    url = HA_URL + "/api/states"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    
    ignored_domains = [
        "update", "automation", "zone", "sun", "conversation", "tts", "image", "stt",
        "persistent_notification", "event", "camera", "binary_sensor"
    ]
    ignored_attributes = [
        "supported_features", "icon", "entity_picture", "device_class", "state_class",
        "friendly_name", "context", "last_changed", "last_updated", "last_reported", 
        "last_triggered", "editable", "auto_update", "release_url", "release_summary", 
        "installed_version", "latest_version", "in_progress", "display_precision", 
        "attribution", "options", "effect_list", "sound_mode_list", "source_list"
    ]

    llm_context = []
    device_lookup = {}

    try:
        response = session.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            for entity in response.json():
                eid = entity['entity_id']
                domain = eid.split('.')[0]
                state_val = entity['state']
                attrs = entity.get('attributes', {})
                name = attrs.get('friendly_name', eid)

                if state_val in ["unavailable", "unknown"]: continue
                if eid.endswith("_led"): continue 
                if domain in ignored_domains: continue
                if domain == "sensor":
                    if any(x in eid.lower() for x in ["uptime", "signal", "strength", "processor", "memory", "kb/s", "kib/s"]):
                        continue

                clean_entity = {
                    "entity_id": eid,
                    "state": state_val,
                    "name": name,
                    "attributes": {}
                }
                
                for k, v in attrs.items():
                    if k not in ignored_attributes:
                        clean_entity["attributes"][k] = v
                if not clean_entity["attributes"]: del clean_entity["attributes"]
                
                llm_context.append(clean_entity)
                # Lookup jetzt: Name -> ID
                device_lookup[name] = eid
                
            return llm_context, device_lookup
            
    except Exception as e:
        print(f"[HA Error] {e}")
    
    return [], {}

def fetch_ha_entities():
    _, lookup = fetch_ha_context()
    return lookup

def execute_device_control(state, device_name):
    """
    Erwartet eine Entity-ID.
    """
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    target_entities = []

    # 1. Prüfen auf "ALL" / "ALLE"
    if device_name.upper() in ["ALL", "ALLE", "ALLES", "LICHTER"]:
        # Fallback: Wenn User 'Alle Lichter' sagt, suchen wir alle Lichter raus
        for eid in global_state.AVAILABLE_LIGHTS.values():
             if eid.startswith(("light.", "switch.")):
                 target_entities.append(eid)
        device_desc = "Alle Geräte"
    
    # 2. Prüfen auf direkte Entity ID (Der Standardfall jetzt!)
    elif "." in device_name:
        # Wir vertrauen dem LLM, dass die ID existiert.
        target_entities.append(device_name)
        device_desc = device_name
        
    else:
        # 3. Notfall-Fallback: Falls LLM doch einen Namen geschickt hat
        # Versuchen wir ihn im Lookup zu finden
        for name, eid in global_state.AVAILABLE_LIGHTS.items():
            if device_name.lower() == name.lower():
                target_entities.append(eid)
                device_desc = name
                break
        
        if not target_entities:
            return f"Fehler: '{device_name}' ist keine gültige Entity ID."

    messages = []
    
    # Trennen nach Typ (Buttons vs Rest)
    buttons = [e for e in target_entities if e.startswith(("button.", "input_button."))]
    rest = [e for e in target_entities if e not in buttons]

    if buttons:
        try:
            url = f"{HA_URL}/api/services/button/press"
            session.post(url, headers=headers, json={"entity_id": buttons}, timeout=5)
            messages.append("Taster gedrückt.")
        except Exception as e: messages.append(f"Fehler: {e}")

    if rest:
        # Press auf Licht = an
        if state == "press": service_cmd = "turn_on"
        else: service_cmd = "turn_on" if state == "on" else "turn_off"
        
        url = f"{HA_URL}/api/services/homeassistant/{service_cmd}"
        try:
            print(f"[HA] {rest} -> {service_cmd}")
            session.post(url, headers=headers, json={"entity_id": rest}, timeout=5)
            verb = "an" if service_cmd == "turn_on" else "aus"
            messages.append(f"Ok, {verb}.")
        except Exception as e: messages.append(f"Fehler: {e}")

    return " ".join(messages)

def execute_media_control(command, device_name=None, volume_level=None):
    # Auch hier: Wir erwarten eine Entity ID
    target = device_name
    
    # Fallback: Wenn None, suche Plexamp
    if not target:
        for eid in global_state.AVAILABLE_LIGHTS.values():
            if "plexamp" in eid.lower() and eid.startswith("media_player."):
                target = eid
                break
    
    if not target or "." not in target:
         return "Kein gültiger Media Player (ID benötigt)."

    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}

    if command in ["volume_up", "volume_down"]:
        try:
            r = session.get(f"{HA_URL}/api/states/{target}", headers=headers, timeout=3)
            current_vol = r.json().get('attributes', {}).get('volume_level', 0.5)
            step = VOLUME_STEP / 100.0
            new_vol = min(1.0, current_vol + step) if command == "volume_up" else max(0.0, current_vol - step)
            command = "volume_set"
            volume_level = new_vol * 100 
        except: pass
        
    service = {
        "play": "media_play", "pause": "media_pause", "stop": "media_stop", 
        "next": "media_next_track", "previous": "media_previous_track", "volume_set": "volume_set",
        "play_pause": "media_play_pause"
    }.get(command, "media_play_pause")
    
    payload = {"entity_id": target}
    if command == "volume_set" and volume_level is not None: 
        payload["volume_level"] = float(volume_level) / 100.0

    try:
        session.post(f"{HA_URL}/api/services/media_player/{service}", headers=headers, json=payload, timeout=5)
        return "Ok."
    except: return "Fehler."

def execute_play_music(category, name, library="Music", device_name=None):
    target = device_name
    if not target:
        for eid in global_state.AVAILABLE_LIGHTS.values():
            if "plexamp" in eid.lower() and eid.startswith("media_player."):
                target = eid
                break
    
    if not target: return "Kein Player gefunden."
    
    media_content_type = "MUSIC"
    media_content_id = ""
    
    if category == "station":
        media_content_id = "plex://0dde0d976875a3be29886e3143dcc9d14c91aa7d/library/sections/6/stations/1"
        media_content_type = "station"
    else:
        payload = {}
        if category == "playlist": payload = {"playlist_name": name}
        elif category == "artist": payload = {"library_name": library, "artist_name": name}
        elif category == "album": payload = {"library_name": library, "album_name": name}
        elif category == "track": payload = {"library_name": library, "track_name": name}
        
        media_content_id = f"plex://{json.dumps(payload, separators=(',', ':'))}"

    url = f"{HA_URL}/api/services/media_player/play_media"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    
    try:
        session.post(url, headers=headers, json={"entity_id": target, "media_content_type": media_content_type, "media_content_id": media_content_id}, timeout=10)
        return "Läuft."
    except: return "Fehler."

def get_ha_device_state(device_name):
    # Strikt: Erwarte ID
    if "." not in device_name:
         return "Bitte Entity ID verwenden."
         
    url = f"{HA_URL}/api/states/{device_name}"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}

    try:
        response = session.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            return f"{device_name}: {data['state']} {data.get('attributes', {})}"
        return "Nicht gefunden."
    except Exception as e: return f"Fehler: {e}"

def get_ha_calendar_events(count=5, days=0):
    # Unverändert, da keine komplexe Logik
    calendar_entities = [eid for eid in global_state.AVAILABLE_LIGHTS.values() if eid.startswith("calendar.")]
    if not calendar_entities: return "Keine Kalender."
    now = datetime.datetime.now()
    end = now.replace(hour=23, minute=59) if days == 0 else now + datetime.timedelta(days=days)
    all_events = []
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    for cal_id in calendar_entities:
        url = f"{HA_URL}/api/calendars/{cal_id}?start={urllib.parse.quote(now.isoformat())}&end={urllib.parse.quote(end.isoformat())}"
        try:
            r = session.get(url, headers=headers, timeout=3)
            if r.status_code == 200: all_events.extend(r.json())
        except: pass
    output = ""
    for event in all_events[:count]:
        start = event['start'].get('dateTime', event['start'].get('date'))
        output += f"- {event.get('summary')} @ {start}\n"
    return output or "Keine Termine."

def add_ha_calendar_event(summary, start_time_iso, duration_minutes=60):
    # Unverändert
    target = None
    for eid in global_state.AVAILABLE_LIGHTS.values():
        if "paulvolk" in eid.lower() and eid.startswith("calendar."): target = eid; break
    if not target: 
        for eid in global_state.AVAILABLE_LIGHTS.values(): 
            if eid.startswith("calendar."): target = eid; break
            
    if not target: return "Kein Kalender."
    url = f"{HA_URL}/api/services/calendar/create_event"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    try:
        start_dt = datetime.datetime.fromisoformat(start_time_iso)
        end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
        payload = {"entity_id": target, "summary": summary, "start_date_time": start_dt.isoformat(), "end_date_time": end_dt.isoformat()}
        session.post(url, headers=headers, json=payload, timeout=5)
        return "Termin erstellt."
    except: return "Fehler."
    
def manage_shopping_list(action, item=None):
    # Unverändert, da ID hier keine Rolle spielt
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    if action == "get":
        try:
            r = session.get(f"{HA_URL}/api/shopping_list", headers=headers, timeout=5)
            items = [e['name'] for e in r.json() if not e['complete']]
            return "Liste: " + ", ".join(items) if items else "Leer."
        except: return "Fehler."
    elif action == "add" and item:
        try:
            session.post(f"{HA_URL}/api/services/shopping_list/add_item", headers=headers, json={"name": item})
            return "Ok."
        except: return "Fehler."
    elif action == "remove" and item:
        try:
            r = session.get(f"{HA_URL}/api/shopping_list", headers=headers)
            for entry in r.json():
                if entry['name'].lower() == item.lower() and not entry['complete']:
                    session.post(f"{HA_URL}/api/services/shopping_list/complete_item", headers=headers, json={"name": entry['name']})
                    return "Abgehakt."
            return "Nicht gefunden."
        except: return "Fehler."
    return "???"

def send_notification(message, title="Jarvis", url=None, image_url=None, priority="normal"):
    """
    Universelles Tool, um Inhalte an das Smartphone zu senden.
    Priority: 'high' (bricht durch 'Bitte nicht stören'), 'normal'.
    """
    # Sende an alle mobile_apps oder spezifisch an dein Handy
    service_url = f"{HA_URL}/api/services/notify/notify" 
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    
    data_payload = {}
    
    # 1. Klick-Aktion (URL öffnen)
    if url:
        data_payload["clickAction"] = url
        # Optional: Button hinzufügen
        data_payload["actions"] = [{"action": "URI", "title": "Öffnen", "uri": url}]

    # 2. Bild anzeigen (z.B. von einer Kamera oder aus dem Web)
    if image_url:
        data_payload["image"] = image_url
    
    # 3. Priorität (TTL / Channel)
    if priority == "high":
        data_payload["ttl"] = 0
        data_payload["priority"] = "high"
        data_payload["channel"] = "alarm_stream" # Android spezifisch für Alarm-Sound

    payload = {
        "message": message,
        "title": title,
        "data": data_payload
    }

    try:
        response = session.post(service_url, headers=headers, json=payload, timeout=5)
        if response.status_code == 200:
            return "Inhalt erfolgreich an das Display gesendet."
        return f"Fehler beim Senden: {response.status_code}"
    except Exception as e:
        return f"Fehler: {e}"

def get_weather_forecast(type="hourly", entity_id=None):
    """
    Ruft die Vorhersage ab. Optimiert für HA >= 2024.3 (service_response).
    """
    # 1. Entität bestimmen
    target = entity_id
    if not target:
        possible = [eid for eid in global_state.AVAILABLE_LIGHTS.values() if eid.startswith("weather.")]
        priority = [p for p in possible if "open_meteo" in p or "home" in p]
        target = priority[0] if priority else (possible[0] if possible else None)

    if not target:
        return "Keine Wetter-Entität gefunden."

    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    limit = 12 if type == "hourly" else 5

    # --- STRATEGIE A: Moderner API Call mit return_response ---
    # WICHTIG: Das ?return_response=true ist für die Datenrückgabe zwingend.
    url_service = f"{HA_URL}/api/services/weather/get_forecasts?return_response=true"
    
    try:
        # Entity_id muss laut HA-Standard oft als Liste übergeben werden
        payload = {"entity_id": [target], "type": type}
        response = session.post(url_service, headers=headers, json=payload, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            # Dein Log zeigt: Die Daten liegen in 'service_response' -> 'entity_id' -> 'forecast'
            forecast_list = data.get("service_response", {}).get(target, {}).get('forecast', [])
            
            if forecast_list:
                return json.dumps(forecast_list[:limit], indent=2)
    except Exception as e:
        print(f"[Weather Debug] Modern API failed: {e}")

    # --- STRATEGIE B: Legacy Fallback (Attribut) ---
    url_state = f"{HA_URL}/api/states/{target}"
    try:
        r = session.get(url_state, headers=headers, timeout=5)
        if r.status_code == 200:
            forecast_list = r.json().get('attributes', {}).get('forecast', [])
            if forecast_list:
                return json.dumps(forecast_list[:limit], indent=2)
    except Exception:
        pass

    return f"Fehler: Konnte keine Wetterdaten für {target} abrufen."

def get_input_text_state(entity_id):
    """Reads the mailbox content."""
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    try:
        r = session.get(f"{HA_URL}/api/states/{entity_id}", headers=headers, timeout=2)
        if r.status_code == 200:
            state = r.json().get('state', '')
            return state if state not in ["unknown", "unavailable"] else ""
    except: pass
    return ""

def clear_input_text(entity_id):
    """Empties the mailbox."""
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    try:
        session.post(f"{HA_URL}/api/services/input_text/set_value", headers=headers, json={"entity_id": entity_id, "value": ""}, timeout=2)
    except: pass

def get_all_person_locations():
    """
    Fetches the status and coordinates of ALL 'person.*' entities.
    Returns a string like:
    - Paul: home (Lat: 48.123, Lon: 11.456)
    - Anna: work (Lat: 48.987, Lon: 11.654)
    """
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    try:
        # We fetch all states and filter python-side to save API calls
        r = session.get(f"{HA_URL}/api/states", headers=headers, timeout=3)
        if r.status_code == 200:
            people = []
            for entity in r.json():
                eid = entity['entity_id']
                if eid.startswith("person."):
                    name = entity.get('attributes', {}).get('friendly_name', eid)
                    state = entity['state']
                    
                    # Get GPS if available
                    lat = entity.get('attributes', {}).get('latitude')
                    lon = entity.get('attributes', {}).get('longitude')
                    
                    if lat and lon:
                        geo_str = f"(GPS: {lat}, {lon})"
                    else:
                        geo_str = "(No GPS)"
                        
                    people.append(f"- {name}: {state} {geo_str}")
            
            return "\n".join(people) if people else "Keine Personen gefunden."
    except Exception as e:
        print(f"[HA Error] Person Fetch: {e}")
        return "Fehler beim Abrufen der Standorte."
    
def set_state(entity_id, state_value, attributes=None):
    """
    Manually sets the state of an entity in Home Assistant via API.
    Used to update the 'sensor.jarvis_last_response' for the dashboard.
    """
    try:
        url = f"{HA_URL}/api/states/{entity_id}"
        headers = {
            "Authorization": "Bearer " + HA_TOKEN,
            "content-type": "application/json",
        }
        
        payload = {"state": str(state_value)}
        if attributes:
            payload["attributes"] = attributes
            
        r = session.post(url, headers=headers, json=payload, timeout=2)
        
        if r.status_code not in [200, 201]:
            print(f" [HA Error] Failed to set state: {r.status_code} - {r.text}")
            
    except Exception as e:
        print(f" [HA Error] set_state failed: {e}")

def create_ha_automation(entity_id, summary, target_value, condition_type="state_match", operator=None):
    """
    Creates a native Home Assistant automation via API.
    """
    import time
    
    # 1. Generate unique ID for the automation
    auto_id = f"jarvis_wakeup_{int(time.time())}"
    
    # 2. Build Trigger
    trigger = []
    
    if condition_type == "state_match":
        trigger.append({
            "platform": "state",
            "entity_id": entity_id,
            "to": target_value
        })
    elif condition_type == "numeric":
        t = {
            "platform": "numeric_state",
            "entity_id": entity_id
        }
        # HA expects 'above' or 'below'
        op = operator if operator else ">"
        if ">" in op: t["above"] = float(target_value)
        elif "<" in op: t["below"] = float(target_value)
        trigger.append(t)
    elif condition_type == "geolocation":
        # Complex: If raw coords, we use a template trigger
        # target_value expected: "lat,lon,radius"
        try:
            parts = target_value.split(',')
            lat = float(parts[0])
            lon = float(parts[1])
            radius = float(parts[2])
            
            # Using Haversine formula in Jinja2 template is heavy, but standard for HA templates
            # We assume the entity_id has latitude/longitude attributes (like person.x or device_tracker.y)
            template = f"""
            {{{{ distance(states.{entity_id}, {lat}, {lon}) * 1000 <= {radius} }}}}
            """
            trigger.append({
                "platform": "template",
                "value_template": template.strip()
            })
        except:
            return "Fehler: Geolocation Format muss 'lat,lon,radius' sein."

    # 3. Build Action (Write to Mailbox)
    action = [
        {
            "service": "input_text.set_value",
            "data": {
                "entity_id": "input_text.jarvis_chat",
                "value": f"INTERNAL_WAKEUP_TRIGGER|{auto_id}|{summary}"
            }
        },
        # Optional: Self-destruct automation? HA API doesn't easily support "delete self" inside automation.
        # We leave it active. User or Jarvis clean up later? 
        # For now, we assume it's a one-off notification basically.
    ]

    payload = {
        "alias": f"Jarvis: {summary}",
        "description": "Created automatically by Jarvis AI.",
        "trigger": trigger,
        "action": action,
        "mode": "single"
    }

    url = f"{HA_URL}/api/config/automation/config/{auto_id}"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    
    try:
        r = session.post(url, headers=headers, json=payload, timeout=5)
        if r.status_code in [200, 201]:
            return f"Automation erstellt (ID: {auto_id}). Ich werde benachrichtigt, wenn '{summary}' eintritt."
        else:
            return f"Fehler beim Erstellen der Automation: {r.status_code} {r.text}"
    except Exception as e:
        return f"API Fehler: {e}"

def delete_ha_automation(auto_id):
    """
    Deletes an automation via API.
    """
    if not auto_id: return "Keine ID."
    
    url = f"{HA_URL}/api/config/automation/config/{auto_id}"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    
    try:
        r = session.delete(url, headers=headers, timeout=5)
        if r.status_code == 200:
            return f"Automation {auto_id} gelöscht."
        return f"Fehler beim Löschen: {r.status_code}"
    except Exception as e:
        return f"Fehler: {e}"