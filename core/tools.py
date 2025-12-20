# jarvis/core/tools.py
from jarvis.services import ha, system, timer, google

# 1. Definitions
FUNCTION_DECLARATIONS = [
    {
        "name": "control_light",
        "description": "Schaltet Lichter oder Schalter an oder aus.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "state": { "type": "STRING", "enum": ["on", "off"] },
                "lamp_name": { "type": "STRING", "description": "Name der Lampe" }
            },
            "required": ["state"]
        }
    },
    {
        "name": "control_media",
        "description": "Steuert Musikwiedergabe UND Musik-Lautstärke auf Plex/externen Playern.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": { 
                    "type": "STRING", 
                    "enum": ["play", "pause", "play_pause", "stop", "next", "previous", "volume_up", "volume_down", "volume_set"],
                    "description": "Befehl. Für 'lauter' nutze volume_up, für 'leiser' volume_down." 
                },
                "device_name": { "type": "STRING", "description": "Name des Players" },
                "volume_level": { "type": "NUMBER", "description": "Nur für volume_set nötig (0-100)" }
            },
            "required": ["command"]
        }
    },
    {
        "name": "get_device_state",
        "description": "Prüft den aktuellen Status (An/Aus, Lautstärke, Attribute) eines Geräts.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device_name": { "type": "STRING", "description": "Name des Geräts, z.B. 'Stehlampe' oder 'Wohnzimmer'" }
            },
            "required": ["device_name"]
        }
    },
    {
        "name": "manage_shopping_list",
        "description": "Verwaltet die Einkaufsliste. Kann Dinge hinzufügen, löschen oder vorlesen.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": { 
                    "type": "STRING", 
                    "enum": ["add", "get", "remove"],
                    "description": "'add' zum Hinzufügen, 'remove' zum Löschen/Abhaken, 'get' zum Vorlesen."
                },
                "item": { 
                    "type": "STRING", 
                    "description": "Produktname (z.B. 'Milch')." 
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "play_specific_music",
        "description": "Spielt Musik oder Hörbücher ab (Starten, nicht steuern).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": { "type": "STRING", "enum": ["artist", "album", "playlist", "genre", "track", "station"] },
                "name": { "type": "STRING" },
                "library": { "type": "STRING", "enum": ["Music", "Audiobooks"] },
                "device_name": { "type": "STRING" }
            },
            "required": ["category", "name", "library"]
        }
    },
    {
        "name": "set_system_volume",
        "description": "Ändert NUR die Systemlautstärke (Stimme von Jarvis) des Raspberry Pi.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "volume_level": { "type": "NUMBER", "description": "Prozent 0-100" }
            },
            "required": ["volume_level"]
        }
    },
    {
        "name": "perform_google_search",
        "description": "Suche nach Fakten oder Wissen und allen Informationen die du nicht weißt.",
        "parameters": {
            "type": "OBJECT",
            "properties": { "query": { "type": "STRING" } },
            "required": ["query"]
        }
    },
    {
        "name": "get_calendar_events",
        "description": "Liest Termine. Nutze days=0 für 'heute', days=1 für 'heute und morgen', days=7 für 'die Woche'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "count": { "type": "INTEGER", "description": "Max Anzahl (Default 5)" },
                "days": { "type": "INTEGER", "description": "0=Heute, 1=Morgen mit dazu, 7=Woche" }
            }
        }
    },
    {
        "name": "add_calendar_event",
        "description": "Erstellt einen Termin. Datum muss ISO sein.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "summary": { "type": "STRING", "description": "Titel" },
                "start_time_iso": { "type": "STRING", "description": "ISO Format YYYY-MM-DDTHH:MM:SS" },
                "duration_minutes": { "type": "INTEGER" }
            },
            "required": ["summary", "start_time_iso"]
        }
    },
    {
        "name": "manage_timer_alarm",
        "description": "Setzt einen Timer/Wecker oder löscht ihn.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": { 
                    "type": "STRING", 
                    "enum": ["set_timer", "stop_alarm"],
                    "description": "Nutze 'stop_alarm' um klingelnde Wecker zu stoppen ODER laufende Timer zu löschen."
                },
                "seconds": { "type": "INTEGER", "description": "Dauer in Sekunden bis zum Alarm." }
            },
            "required": ["action"]
        }
    },
    {
        "name": "restart_service",
        "description": "Startet NUR die Jarvis-Software (den Service) neu. Nutze dies bei 'Skript neu starten', 'Jarvis neu starten' oder 'Service restart'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {}, 
            "required": []
        }
    },
]

# 2. Implementation Map
TOOL_IMPLEMENTATIONS = {
    'control_light': ha.execute_light_control,
    'control_media': ha.execute_media_control,
    'get_device_state': ha.get_ha_device_state,
    'get_calendar_events': ha.get_ha_calendar_events,
    'add_calendar_event': ha.add_ha_calendar_event,
    'play_specific_music': ha.execute_play_music,
    'manage_shopping_list': ha.manage_shopping_list,
    'manage_timer_alarm': timer.manage_timer_alarm,
    'restart_service': system.restart_service,
    'set_system_volume': system.set_system_volume,
    'perform_google_search': google.perform_google_search_internal
}

def execute_tool(name, args):
    """Dispatches the function call to the correct service."""
    print(f"  [DEBUG] Tool Call: {name} | Args: {args}")
    if name in TOOL_IMPLEMENTATIONS:
        try:
            result = TOOL_IMPLEMENTATIONS[name](**args)
            print(f"  [DEBUG] Tool Result: {result}")
            return result
        except Exception as e:
            error_msg = f"Error executing {name}: {str(e)}"
            print(f"  [DEBUG] Tool Error: {error_msg}")
            return error_msg
    
    # Fallback, wenn Funktion nicht in der Liste ist
    print(f"  [DEBUG] Error: Function '{name}' not found in TOOL_IMPLEMENTATIONS!")
    return "Funktion unbekannt"