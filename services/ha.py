# jarvis/services/ha.py
import datetime
import urllib.parse
import json
from jarvis.config import HA_URL, HA_TOKEN, VOLUME_STEP
from jarvis.utils import session
from jarvis import state as global_state 

def fetch_ha_context():
    """
    Holt den Status aller Geräte, nutzt BLACKLISTS statt Whitelists.
    Alles ist erlaubt, außer es steht auf der Liste.
    """
    if not HA_URL or not HA_TOKEN: return [], {}
    
    url = HA_URL + "/api/states"
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    
    # 1. DOMAIN BLACKLIST: Diese Kategorien komplett ignorieren
    ignored_domains = [
        "update",                   # System-Updates (sehr geschwätzig)
        "automation",               # Automatisierungen (Interne Logik)
        "zone",                     # Geozonen (meist statisch)
        "sun",                      # Sonnenstand (macht oft den Kontext voll)
        "conversation",             # HA Voice Agent
        "tts",                      # Text-to-Speech Status
        "image",                    # Bild-Entitäten
        "stt",                      # Speech-to-Text
        "persistent_notification",  # HA Benachrichtigungen
        "event",                    # Events (Button clicks etc.)
        "camera",                   # Kamerabilder (URLs nützen LLM nichts)
        "binary_sensor",            # Viele technische Sensoren
    ]
    
    # 2. ATTRIBUTE BLACKLIST: Diese Felder aus den Attributen löschen
    ignored_attributes = [
        "supported_features", "icon", "entity_picture", "device_class", "state_class",
        "friendly_name", # Wird separat als 'name' gespeichert
        "context", "last_changed", "last_updated", "last_reported", "last_triggered",
        "editable", "auto_update", "release_url", "release_summary", "installed_version", 
        "latest_version", "in_progress", "display_precision", "attribution",
        "options", "effect_list", "sound_mode_list", "source_list" # Listen können sehr lang sein!
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

                # Grundsätzliche Filter (immer sinnvoll)
                if state_val in ["unavailable", "unknown"]: continue
                if eid.endswith("_led"): continue # Deine spezifischen LED-Filter
                
                # --- BLACKLIST CHECKS ---
                
                # 1. Domain prüfen
                if domain in ignored_domains: continue

                # 2. Spezialfall Sensoren: 
                # Da es oft 100+ Sensoren gibt (Signalstärke, etc.), ist hier ein 
                # zusätzlicher Namensfilter oft ratsam. Wenn du ALLES willst, 
                # kommentiere die nächsten 3 Zeilen aus.
                if domain == "sensor":
                    # Ignoriere technische Sensoren
                    if any(x in eid.lower() for x in ["uptime", "signal", "strength", "processor", "memory", "kb/s", "kib/s"]):
                        continue

                # --- Aufbau für LLM Context ---
                clean_entity = {
                    "entity_id": eid,
                    "state": state_val,
                    "name": name,
                    "attributes": {}
                }
                
                # Attribute kopieren (außer sie stehen auf der Blacklist)
                for k, v in attrs.items():
                    if k not in ignored_attributes:
                        clean_entity["attributes"][k] = v
                
                # Leere Attribute entfernen
                if not clean_entity["attributes"]: del clean_entity["attributes"]
                
                llm_context.append(clean_entity)
                device_lookup[name] = eid
                
            return llm_context, device_lookup
            
    except Exception as e:
        print(f"[HA Error] {e}")
    
    return [], {}

def fetch_ha_entities():
    """
    Wrapper-Funktion für Abwärtskompatibilität mit main.py.
    Gibt nur das Lookup-Dictionary zurück.
    """
    _, lookup = fetch_ha_context()
    return lookup

def execute_device_control(state, device_name="ALL"):
    target_entities = []
    device_desc = device_name
    
    # 1. Geräte suchen (Filter entfernt, damit Buttons gefunden werden)
    for name, eid in global_state.AVAILABLE_LIGHTS.items():
        if device_name.lower() in name.lower() or name.lower() in device_name.lower():
            target_entities.append(eid)
            device_desc = device_name

    # Fallback "ALL"
    if not target_entities and device_name == "ALL":
        for name, eid in global_state.AVAILABLE_LIGHTS.items():
            if eid.startswith(("light.", "switch.", "script.", "button.", "input_button.")):
                target_entities.append(eid)
    
    if not target_entities: return f"Gerät '{device_name}' nicht gefunden."
    
    headers = {"Authorization": "Bearer " + HA_TOKEN, "content-type": "application/json"}
    messages = []

    # 2. Entitäten sortieren
    # Buttons/Input_Buttons/Szenen/Skripte reagieren oft besser auf spezifische Dienste oder 'turn_on'
    buttons = [e for e in target_entities if e.startswith(("button.", "input_button."))]
    switches_and_lights = [e for e in target_entities if e not in buttons]

    # --- LOGIK FÜR BUTTONS ---
    # Buttons können nur gedrückt werden. Egal ob LLM "on" oder "press" sagt -> wir drücken.
    if buttons:
        if state in ["on", "press"]:
            try:
                # API Call für Buttons
                url = f"{HA_URL}/api/services/button/press"
                print(f"[HA] Pressing buttons: {buttons}")
                session.post(url, headers=headers, json={"entity_id": buttons}, timeout=5)
                messages.append(f"{len(buttons)} Taster gedrückt")
            except Exception as e:
                messages.append(f"Fehler bei Taster: {e}")
        else:
            # Man kann einen Button nicht "ausschalten"
            messages.append("Taster können nicht ausgeschaltet werden.")

    # --- LOGIK FÜR SCHALTER / LICHTER ---
    if switches_and_lights:
        # Mapping: 'press' auf einem Schalter interpretieren wir als 'turn_on'
        if state == "press":
            service_cmd = "turn_on"
        else:
            service_cmd = "turn_on" if state == "on" else "turn_off"
            
        url = f"{HA_URL}/api/services/homeassistant/{service_cmd}"
        
        try:
            print(f"[HA] Setting {switches_and_lights} to {service_cmd}")
            session.post(url, headers=headers, json={"entity_id": switches_and_lights}, timeout=5)
            verb = "an" if service_cmd == "turn_on" else "aus"
            messages.append(f"{device_desc} ist {verb}")
        except Exception as e:
            messages.append(f"Fehler bei Schalter: {e}")

    return ", ".join(messages)

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
    
    # Priorisiere persönlichen Kalender
    for name, eid in global_state.AVAILABLE_LIGHTS.items():
        if "paulvolk" in eid.lower():
            target_calendar = eid
            break
    
    # Fallback auf irgendeinen Kalender
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
        # A) ZUERST PRÜFEN: Ist es direkt eine Entity ID?
        # Wir schauen, ob der übergebene Name exakt einer ID entspricht
        if device_name in global_state.AVAILABLE_LIGHTS.values():
            target = device_name
        
        # B) Wenn nicht, suchen wir im "Friendly Name" (Fallback)
        if not target:
            for d_name, eid in global_state.AVAILABLE_LIGHTS.items():
                # Filter: Nur Media Player beachten (vermeidet Lautstärke-Regler etc.)
                if not eid.startswith("media_player."):
                    continue

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
        if device_name in global_state.AVAILABLE_LIGHTS.values():
            target_entity = device_name
            
        if not target_entity:
            for d_name, eid in global_state.AVAILABLE_LIGHTS.items():
                if device_name.lower() in d_name.lower() or d_name.lower() in device_name.lower():
                    target_entity = eid
                    break
    else:
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
    
    media_content_type = "MUSIC"
    media_content_id = ""
    
    if category == "station" or (name and "radio" in name.lower()):
        # Beispiel ID - sollte idealerweise dynamischer sein
        media_content_id = "plex://0dde0d976875a3be29886e3143dcc9d14c91aa7d/library/sections/6/stations/1"
        media_content_type = "station"
        name = "Library Radio" 
    else:
        if library not in ["Music", "Audiobooks"]: library = "Music"
        
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
        else: return "Home Assistant konnte das nicht abspielen."
    except Exception as e:
        print(e)
        return "Verbindungsfehler."

def get_ha_device_state(device_name):
    # Diese Funktion nutzt auch den globalen State für das Lookup
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
            if 'temperature' in attributes:
                info += f" Temperatur: {attributes['temperature']}°C."
            if 'humidity' in attributes:
                info += f" Luftfeuchtigkeit: {attributes['humidity']}%."
                
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