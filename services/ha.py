# jarvis/services/ha.py
import datetime
import urllib.parse
import json
from jarvis.config import HA_URL, HA_TOKEN, VOLUME_STEP
from jarvis.utils import session
from jarvis import state as global_state 

def fetch_ha_entities():
    """Fetches valid entities from HA."""
    if not HA_URL or not HA_TOKEN: return {}
    url = HA_URL + "/api/states"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    
    try:
        response = session.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            devices = {}
            for entity in response.json():
                eid = entity['entity_id']
                # Filtert LEDs und IR-Blaster heraus
                if entity['state'] == "unavailable" or eid.endswith("_led") or "ir_blaster" in eid: continue
                name = entity.get('attributes', {}).get('friendly_name', eid)
                
                if any(eid.startswith(p) for p in ["light.", "switch.", "media_player.", "calendar."]):
                    devices[name] = eid
            return devices
    except Exception as e:
        print(f"[HA Error] {e}")
    return {}

def execute_light_control(state, lamp_name="ALLE"):
    target_entities = []
    
    if lamp_name.upper() in ["ALLE", "ALL", "ALLES"]:
        target_entities = [eid for _, eid in global_state.AVAILABLE_LIGHTS.items() if eid.startswith(("light.", "switch."))]
        device_desc = "alle Lampen"
    else:
        for name, eid in global_state.AVAILABLE_LIGHTS.items():
            if lamp_name.lower() in name.lower() or name.lower() in lamp_name.lower():
                target_entities.append(eid)
                device_desc = lamp_name
                break
    
    if not target_entities: return f"Gerät '{lamp_name}' nicht gefunden."
    
    service = "turn_on" if state == "on" else "turn_off"
    url = f"{HA_URL}/api/services/homeassistant/{service}"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    try:
        session.post(url, headers=headers, json={"entity_id": target_entities}, timeout=5)
        return f"Ok, {device_desc} {state}."
    except: return "Home Assistant Fehler."

def get_ha_calendar_events(count=5, days=0):
    calendar_entities = [eid for _, eid in global_state.AVAILABLE_LIGHTS.items() if eid.startswith("calendar.")]
    if not calendar_entities: return "Keine Kalender gefunden."
    
    now = datetime.datetime.now()
    end = now.replace(hour=23, minute=59) if days == 0 else now + datetime.timedelta(days=days)
    
    all_events = []
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}

    for cal_id in calendar_entities:
        url = f"{HA_URL}/api/calendars/{cal_id}?start={urllib.parse.quote(now.isoformat())}&end={urllib.parse.quote(end.isoformat())}"
        try:
            r = session.get(url, headers=headers, timeout=3)
            if r.status_code == 200:
                data = r.json()
                for e in data: 
                    e['source'] = cal_id.split('.')[1]
                all_events.extend(data)
        except: pass
        
    output = ""
    for event in all_events[:count]:
        start = event['start'].get('dateTime', event['start'].get('date'))
        output += f"- {event.get('summary')} ({event.get('source')}) @ {start}\n"
    return output or "Keine Termine."

def add_ha_calendar_event(summary, start_time_iso, duration_minutes=60):
    target_calendar = None
    
    for name, eid in global_state.AVAILABLE_LIGHTS.items():
        if "paulvolk" in eid.lower():
            target_calendar = eid
            break
    if not target_calendar:
        for name, eid in global_state.AVAILABLE_LIGHTS.items():
            if eid.startswith("calendar."):
                target_calendar = eid
                break
                
    if not target_calendar: return "Kein Schreib-Kalender gefunden."

    url = f"{HA_URL}/api/services/calendar/create_event"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    
    try:
        try:
            start_dt = datetime.datetime.fromisoformat(start_time_iso)
        except:
            return "Ungültiges Startdatum."
        
        end_dt = start_dt + datetime.timedelta(minutes=duration_minutes)
        payload = {
            "entity_id": target_calendar, "summary": summary,
            "start_date_time": start_dt.isoformat(), "end_date_time": end_dt.isoformat()
        }
        session.post(url, headers=headers, json=payload, timeout=5)
        return f"Termin in {target_calendar.replace('calendar.', '')} erstellt."
    except Exception as e: return f"Fehler: {e}"

def execute_media_control(command, device_name=None, volume_level=None):
    target = None
    
    # 1. Zielgerät intelligent suchen
    if device_name:
        # User nennt Name -> Suche danach
        for d_name, eid in global_state.AVAILABLE_LIGHTS.items():
            if device_name.lower() in d_name.lower() or d_name.lower() in device_name.lower():
                target = eid
                break
    else:
        # Kein Name -> Suche zuerst nach Plex/Plexamp
        for d_name, eid in global_state.AVAILABLE_LIGHTS.items():
            if eid.startswith("media_player.") and ("plex" in eid.lower() or "plexamp" in eid.lower()):
                target = eid
                break
        
        # Fallback: Irgendein Player
        if not target:
            for d_name, eid in global_state.AVAILABLE_LIGHTS.items(): 
                if eid.startswith("media_player."):
                    target = eid
                    break

    if not target: return "Player nicht gefunden."

    # DEBUG: Damit du im Log siehst, wen er steuert
    print(f"  [DEBUG] Sende Media-Befehl '{command}' an: {target}")

    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}

    # Spezialfall: Lautstärke relativ ändern
    if command in ["volume_up", "volume_down"]:
        try:
            get_url = f"{HA_URL}/api/states/{target}"
            r = session.get(get_url, headers=headers, timeout=5)
            if r.status_code == 200:
                current_vol = r.json().get('attributes', {}).get('volume_level', 0.5)
                step = VOLUME_STEP / 100.0
                new_vol = min(1.0, current_vol + step) if command == "volume_up" else max(0.0, current_vol - step)
                
                # Wir mappen das auf 'volume_set' um
                command = "volume_set"
                volume_level = new_vol * 100 
            else: return "Fehler beim Lesen der Lautstärke."
        except: return "Fehler beim Berechnen."
        
    service = {
        "play": "media_play", "pause": "media_pause", "stop": "media_stop", 
        "next": "media_next_track", "previous": "media_previous_track", "volume_set": "volume_set"
    }.get(command, "media_play_pause")
    
    payload = {"entity_id": target}
    if command == "volume_set" and volume_level is not None: 
        val = float(volume_level)
        payload["volume_level"] = val / 100 if val > 1.0 else val

    try:
        session.post(f"{HA_URL}/api/services/media_player/{service}", headers=headers, json=payload, timeout=5)
        return "Ok."
    except: return "Fehler."

def execute_play_music(category, name, library="Music", device_name=None):
    target_entity = None
    
    if device_name:
        for d_name, eid in global_state.AVAILABLE_LIGHTS.items():
            if device_name.lower() in d_name.lower() or d_name.lower() in device_name.lower():
                target_entity = eid
                break
    else:
        # Bevorzugt Plex
        for d_name, eid in global_state.AVAILABLE_LIGHTS.items():
            if eid.startswith("media_player.") and ("plex" in eid.lower() or "plexamp" in eid.lower()):
                target_entity = eid
                break
        if not target_entity:
            for d_name, eid in global_state.AVAILABLE_LIGHTS.items(): 
                if eid.startswith("media_player."):
                    target_entity = eid
                    break
    
    if not target_entity: return "Kein Musikplayer gefunden."
    
    print(f"  [DEBUG] Sende Musik an: {target_entity}")

    media_content_type = "MUSIC"
    media_content_id = ""
    
    if category == "station" or (name and "radio" in name.lower()):
        print(f"  [Plex] Starte Library Radio via URI")
        media_content_id = "plex://0dde0d976875a3be29886e3143dcc9d14c91aa7d/library/sections/6/stations/1"
        media_content_type = "station"
        name = "Library Radio" 
    else:
        if library not in ["Music", "Audiobooks"]: library = "Music"
        print(f"  [Plex] Suche '{name}' (Kat: {category}) in '{library}'")
        
        payload = {}
        if category == "playlist": payload = {"playlist_name": name}
        elif category == "artist": payload = {"library_name": library, "artist_name": name}
        elif category == "album": payload = {"library_name": library, "album_name": name}
        elif category == "genre": payload = {"library_name": library, "genre_name": name}
        elif category == "track": payload = {"library_name": library, "track_name": name}
        
        media_content_id = f"plex://{json.dumps(payload, separators=(',', ':'))}"
        media_content_type = "MUSIC"

    url = f"{HA_URL}/api/services/media_player/play_media"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    data = {"entity_id": target_entity, "media_content_type": media_content_type, "media_content_id": media_content_id}

    try:
        r = session.post(url, headers=headers, json=data, timeout=10)
        if r.status_code == 200: return f"Viel Spaß mit {name}."
        else:
            print(f"HA Error: {r.status_code} - {r.text}")
            return "Home Assistant konnte das nicht abspielen."
    except Exception as e:
        print(e)
        return "Verbindungsfehler."

def get_ha_device_state(device_name):
    target_entity = None
    real_name = device_name

    for name, eid in global_state.AVAILABLE_LIGHTS.items():
        if device_name.lower() in name.lower() or name.lower() in device_name.lower():
            target_entity = eid
            real_name = name
            break
    
    if not target_entity: return f"Ich konnte das Gerät '{device_name}' nicht finden."

    url = f"{HA_URL}/api/states/{target_entity}"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}

    try:
        response = session.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            data = response.json()
            state_val = data.get('state', 'unknown')
            attributes = data.get('attributes', {})
            
            info = f"Status von {real_name}: {state_val}."
            if 'brightness' in attributes:
                percent = int((attributes['brightness'] / 255) * 100)
                info += f" Helligkeit: {percent}%."
            if 'media_title' in attributes:
                info += f" Spielt gerade: {attributes.get('media_artist', '')} - {attributes['media_title']}."
            if 'volume_level' in attributes:
                vol = int(attributes['volume_level'] * 100)
                info += f" Lautstärke: {vol}%."
            return info
        else: return "Konnte Status nicht von Home Assistant lesen."
    except Exception as e: return f"Fehler bei der Statusabfrage: {e}"

def manage_shopping_list(action, item=None):
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    if action == "get":
        url = f"{HA_URL}/api/shopping_list"
        try:
            response = session.get(url, headers=headers, timeout=5)
            if response.status_code == 200:
                data = response.json()
                active_items = [entry['name'] for entry in data if not entry['complete']]
                if not active_items: return "Deine Einkaufsliste ist leer."
                return "Auf der Liste steht: " + ", ".join(active_items)
            return "Fehler beim Abrufen."
        except Exception as e: return f"Fehler: {e}"
    elif action == "add" and item:
        url = f"{HA_URL}/api/services/shopping_list/add_item"
        try:
            session.post(url, headers=headers, json={"name": item}, timeout=5)
            return f"Ok, {item} steht drauf."
        except Exception as e: return f"Fehler: {e}"
    elif action == "remove" and item:
        url_get = f"{HA_URL}/api/shopping_list"
        try:
            r = session.get(url_get, headers=headers, timeout=3)
            if r.status_code == 200:
                data = r.json()
                found_real_name = None
                for entry in data:
                    if not entry['complete'] and entry['name'].lower() == item.lower():
                        found_real_name = entry['name']
                        break
                if found_real_name:
                    url_complete = f"{HA_URL}/api/services/shopping_list/complete_item"
                    session.post(url_complete, headers=headers, json={"name": found_real_name}, timeout=5)
                    return f"Alles klar, {found_real_name} ist abgehakt."
                else: return f"{item} habe ich nicht auf der Liste gefunden."
            else: return "Konnte Liste nicht prüfen."
        except Exception as e: return f"Fehler beim Löschen: {e}"
    return "Funktion nicht verstanden."