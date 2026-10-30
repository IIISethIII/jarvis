import subprocess
from google.genai import types
from core.ha import HomeAssistant
from core import memory

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

def edit_core_memory(action: str, fact: str, replace_text: str = ""):
    return memory.edit_core_memory(action, fact, replace_text)

def search_archive(query: str):
    return memory.search_archive(query)

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
        description="Ändert die Lautstärke des Lautsprechers (0-100%).",
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
        name="edit_core_memory",
        description="Fügt eine wichtige Information über den Benutzer (Core Memory) hinzu oder ersetzt eine bestehende. Verwende dies nur für permanente Fakten, Vorlieben oder Routinen.",
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "action": types.Schema(
                    type=types.Type.STRING,
                    description="'append' um einen neuen Fakt hinzuzufügen, oder 'replace' um einen bestehenden zu ersetzen.",
                ),
                "fact": types.Schema(
                    type=types.Type.STRING,
                    description="Der neue Fakt (bei append) oder der exakte alte Text der ersetzt werden soll (bei replace).",
                ),
                "replace_text": types.Schema(
                    type=types.Type.STRING,
                    description="Der neue Text (nur bei action='replace' erforderlich).",
                ),
            },
            required=["action", "fact"],
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
        name="end_conversation",
        description="Beendet die Konversation sofort. Rufe dies auf, wenn der Benutzer 'Tschüss', 'Stop', 'Danke' sagt oder andeutet, dass das Gespräch vorbei ist.",
    ),
]

def end_conversation():
    """Ends the conversation."""
    return "<END_CONVERSATION>"

TOOL_IMPLEMENTATIONS = {
    'get_pi_temperature': get_pi_temperature,
    'set_speaker_volume': set_speaker_volume,
    'call_ha_service': call_ha_service,
    'edit_core_memory': edit_core_memory,
    'search_archive': search_archive,
    'end_conversation': end_conversation,
}

def execute_tool(name, args):
    """Dispatches the function call to the correct service."""
    print(f"  [DEBUG] Executing Tool: {name} | Args: {args}", flush=True)
    
    if name in TOOL_IMPLEMENTATIONS:
        try:
            result = TOOL_IMPLEMENTATIONS[name](**args)
            print(f"  [DEBUG] Tool Result: {result}", flush=True)
            return result
        except Exception as e:
            error_msg = f"Error executing {name}: {str(e)}"
            print(f"  [DEBUG] Tool Error: {error_msg}", flush=True)
            return error_msg
    
    return f"Fehler: Funktion {name} nicht gefunden."
