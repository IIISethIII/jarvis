import subprocess
from google.genai import types
from core.ha import HomeAssistant
from core import memory
from core import openclaw
from core import workspace

# Initialize a global instance of the HA wrapper
ha_client = HomeAssistant()

def get_pi_temperature():
    """Returns the current CPU temperature of the Raspberry Pi."""
    try:
        temp = subprocess.check_output(['vcgencmd', 'measure_temp']).decode('utf-8')
        return f"Pi Temperature: {temp.strip()}"
    except Exception as e:
        return f"Error: {e}"

import json

def call_ha_service(domain: str, service: str, entity_id: str, extra_json: str = None):
    """Calls any Home Assistant service."""
    try:
        payload = {"entity_id": entity_id}
        if extra_json:
            try:
                payload.update(json.loads(extra_json))
            except json.JSONDecodeError:
                pass # Fallback if parsing fails
        result = ha_client.call_service(domain, service, payload)
        return "Success" if result is not None else "Failed to contact Home Assistant"
    except Exception as e:
        return f"Error: {e}"

def get_ha_details(entity_ids: list = None, query: str = "", service_domain: str = ""):
    from core import ha_context
    return ha_context.details(entity_ids, query, service_domain)

def remember(note: str, session_id: str = ""):
    """Queues a note for the learner (core/learner.py), which updates the workspace files after the session."""
    if not (note or "").strip():
        return "Error: note is empty."
    workspace.add_note(note, session_id)
    return "Noted. It will be worked into your files after this conversation; follow it right away until then."

def load_skills(names: list):
    return workspace.load_skills(names)

def search_archive(query: str):
    return memory.search_archive(query)

def ask_openclaw(task: str):
    return openclaw.ask(task)

def manage_timer(action: str, seconds: int = 0, time: str = "", date: str = "", label: str = "", timer_id: str = ""):
    """Sets, lists or cancels timers and alarms (see core/timers.py)."""
    from core.timers import timer_manager
    if action == "set_timer":
        return timer_manager.set_timer(seconds, label)
    if action == "set_alarm":
        return timer_manager.set_alarm(time, date, label)
    if action == "list":
        return timer_manager.list_timers()
    if action == "cancel":
        return timer_manager.cancel(str(timer_id), label)
    return f"Fehler: Unbekannte action '{action}'."

def set_speaker_volume(volume: int):
    """Sets the system speaker volume (0-100%)."""
    from core.audio import get_bonnet_card_index
    try:
        # Clamp volume
        vol = max(0, min(100, int(volume)))
        card_idx = get_bonnet_card_index()
        if card_idx is None:
            return "Fehler: Voice Bonnet Soundkarte nicht gefunden."
            
        subprocess.run(['amixer', '-c', str(card_idx), 'sset', 'Speaker', f'{vol}%'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # Attempt to save state persistently (may fail on some setups, which is fine)
        subprocess.run(['sudo', 'alsactl', 'store'], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return f"Lautstärke auf {vol}% gesetzt."
    except Exception as e:
        return f"Error: {e}"

FUNCTION_DECLARATIONS = [
    types.FunctionDeclaration(
        name="get_pi_temperature",
        description="Gibt die aktuelle CPU Temperatur des Raspberry Pi zurück.",
    ),
    types.FunctionDeclaration(
        name="set_speaker_volume",
        description="Ändert die Lautstärke des eigenen Lautsprechers von Jarvis (0-100%). Nur wenn der User ausdrücklich 'Systemlautstärke', 'deine Lautstärke' oder 'Lautsprecher' sagt, nicht für Musik.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "volume": types.Schema(
                    type=types.Type.INTEGER,
                    description="Die gewünschte Lautstärke in Prozent (0 bis 100).",
                ),
            },
            required=["volume"],
        ),
    ),
    types.FunctionDeclaration(
        name="call_ha_service",
        description="Ruft einen Home Assistant Service auf. (z.B. domain='light', service='turn_on', entity_id='light.stehlampe', extra_json='{\"brightness\": 255}')",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "domain": types.Schema(
                    type=types.Type.STRING,
                    description="Die HA Domain (z.B. light, media_player, script, climate)",
                ),
                "service": types.Schema(
                    type=types.Type.STRING,
                    description="Der HA Service (z.B. turn_on, media_play, volume_set, set_temperature)",
                ),
                "entity_id": types.Schema(
                    type=types.Type.STRING,
                    description="Die HA entity_id (z.B. 'light.stehlampe')",
                ),
                "extra_json": types.Schema(
                    type=types.Type.STRING,
                    description="Zusätzliche Parameter als JSON String (optional, z.B. '{\"volume_level\": 0.5}')",
                ),
            },
            required=["domain", "service", "entity_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_ha_details",
        description="Reads Home Assistant details that are not in your device overview: the current state with all attributes (forecasts, menus, lists, departures), a search over all entities, or the parameters of a domain's services.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "entity_ids": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="Exact entity_ids whose current state and all attributes you need.",
                ),
                "query": types.Schema(
                    type=types.Type.STRING,
                    description="Optional: part of an entity_id or name to search for (e.g. 'kitchen', 'mensa').",
                ),
                "service_domain": types.Schema(
                    type=types.Type.STRING,
                    description="Optional: a domain (e.g. 'light', 'climate') whose services and their parameters you need.",
                ),
            },
        ),
    ),
    types.FunctionDeclaration(
        name="remember",
        description="Passes on everything you should remember, forget, change or learn: facts and preferences of the user, house rules, your personality, corrections of how you do something, 'undo that'. A reviewer works it into your files after the conversation. Never for unclear fragments.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "note": types.Schema(
                    type=types.Type.STRING,
                    description="The request in the user's words and language, plus the context needed to understand it (e.g. what 'that' refers to).",
                ),
            },
            required=["note"],
        ),
    ),
    types.FunctionDeclaration(
        name="load_skills",
        description="Loads one or more of your learned skills (procedures, listed under SKILLS) before you act.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "names": types.Schema(
                    type=types.Type.ARRAY,
                    items=types.Schema(type=types.Type.STRING),
                    description="Skill names from the SKILLS list.",
                ),
            },
            required=["names"],
        ),
    ),
    types.FunctionDeclaration(
        name="search_archive",
        description="Durchsucht das Langzeitgedächtnis (Archiv) nach vergangenen Konversationen. Verwende dies, wenn der Benutzer nach vergangenen Gesprächen fragt.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "query": types.Schema(
                    type=types.Type.STRING,
                    description="Das Suchwort oder der Satz (z.B. 'WLAN Passwort' oder 'Kaffee')",
                ),
            },
            required=["query"],
        ),
    ),
    types.FunctionDeclaration(
        name="manage_timer",
        description="Stellt, listet oder löscht Timer und Wecker. Sie klingeln über den Lautsprecher von Jarvis.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "action": types.Schema(
                    type=types.Type.STRING,
                    enum=["set_timer", "set_alarm", "list", "cancel"],
                    description="'set_timer' (Dauer), 'set_alarm' (Uhrzeit), 'list' (aktive anzeigen), 'cancel' (löschen).",
                ),
                "seconds": types.Schema(
                    type=types.Type.INTEGER,
                    description="Nur set_timer: Dauer in Sekunden (z.B. 600 für 10 Minuten).",
                ),
                "time": types.Schema(
                    type=types.Type.STRING,
                    description="Nur set_alarm: Uhrzeit im 24h-Format HH:MM (z.B. '06:30').",
                ),
                "date": types.Schema(
                    type=types.Type.STRING,
                    description="Nur set_alarm, optional: Datum YYYY-MM-DD. Ohne Datum: nächstes Vorkommen der Uhrzeit.",
                ),
                "label": types.Schema(
                    type=types.Type.STRING,
                    description="Optionaler Name (z.B. 'Nudeln'). Bei cancel: löscht Timer, deren Name dies enthält.",
                ),
                "timer_id": types.Schema(
                    type=types.Type.STRING,
                    description="Nur cancel: ID des Timers (z.B. '3') oder 'all' für alle.",
                ),
            },
            required=["action"],
        ),
    ),
    types.FunctionDeclaration(
        name="end_conversation",
        description="Beendet die Konversation sofort. Rufe dies auf, wenn der Benutzer 'Tschüss', 'Stop', 'Danke' sagt oder andeutet, dass das Gespräch vorbei ist. Nicht bei Beleidigungen oder Flüchen, darauf antwortest du.",
    ),
]

# Optional: only offered to Gemini when OPENCLAW_URL and OPENCLAW_TOKEN are set
if openclaw.enabled():
    FUNCTION_DECLARATIONS.append(types.FunctionDeclaration(
        name="ask_openclaw",
        description=f"Fragt {openclaw.NAME}, einen KI-Agenten mit mehr Fähigkeiten als du (gründliche Recherche, Webseiten, mehrstufige Aufgaben, eigenes Gedächtnis). Nutze das immer, wenn du etwas nicht weißt oder deine anderen Tools nicht weiterhelfen. Die Antwort dauert oft 10 bis 60 Sekunden.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "task": types.Schema(
                    type=types.Type.STRING,
                    description=f"Die Frage des Users möglichst im Wortlaut und in seiner Sprache, ergänzt um Kontext aus dem Gespräch, den {openclaw.NAME} nicht kennen kann (er hört nicht mit). Zeiträume und Daten nicht selbst umrechnen.",
                ),
            },
            required=["task"],
        ),
    ))

def end_conversation():
    """Ends the conversation."""
    return "<END_CONVERSATION>"

TOOL_IMPLEMENTATIONS = {
    'get_pi_temperature': get_pi_temperature,
    'set_speaker_volume': set_speaker_volume,
    'call_ha_service': call_ha_service,
    'get_ha_details': get_ha_details,
    'remember': remember,
    'load_skills': load_skills,
    'search_archive': search_archive,
    'manage_timer': manage_timer,
    'ask_openclaw': ask_openclaw,
    'end_conversation': end_conversation,
}

def execute_tool(name, args):
    """Dispatches the function call to the correct service."""
    print(f"  [DEBUG] Executing Tool: {name} | Args: {args}", flush=True)
    
    if name in TOOL_IMPLEMENTATIONS:
        try:
            result = TOOL_IMPLEMENTATIONS[name](**args)
            text = str(result)
            print(f"  [DEBUG] Tool Result: {text[:500]}{'…' if len(text) > 500 else ''}", flush=True)
            return result
        except Exception as e:
            error_msg = f"Error executing {name}: {str(e)}"
            print(f"  [DEBUG] Tool Error: {error_msg}", flush=True)
            return error_msg
    
    return f"Fehler: Funktion {name} nicht gefunden."
