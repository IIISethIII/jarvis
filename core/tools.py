# jarvis/core/tools.py
from jarvis.services import ha, system, timer, google, sfx, memory
from jarvis import config

# 1. Definitions
FUNCTION_DECLARATIONS = [
    {
        "name": "save_memory",
        "description": "Speichert wichtige Fakten, Vorlieben oder Informationen über den User langfristig ab. Nutze dies, wenn der User sagt 'Merk dir das'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": { "type": "STRING", "description": "Der Fakt (z.B. 'Paul trinkt Kaffee schwarz')." }
            },
            "required": ["text"]
        }
    },
    {
        "name": "retrieve_memory",
        "description": "Sucht aktiv im Langzeitgedächtnis. Nutze dies bei Fragen nach Wissen ('WLAN Code?') ODER bei Begrüßungen ('Guten Morgen'), um User-Routinen zu checken.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "search_query": { "type": "STRING", "description": "Suchbegriff (z.B. 'WLAN Passwort', 'Morgen Routine')." }
            },
            "required": ["search_query"]
        }
    },
    {
        "name": "delete_memory",
        "description": "Löscht eine spezifische Information aus dem Langzeitgedächtnis. Nutze dies, wenn der User sagt, dass etwas falsch ist oder sich geändert hat (z.B. 'Vergiss mein altes Passwort').",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "topic": {
                    "type": "STRING",
                    "description": "Das Thema oder der Inhalt, der gelöscht werden soll (z.B. 'Wlan Passwort' oder 'Mein Alter')."
                }
            },
            "required": ["topic"]
        }
    },
    {
        "name": "execute_python_code",
        "description": "Führt Python-Code aus. Nutze dies für Berechnungen, Datenverarbeitung UND um via 'requests' externe Webseiten, APIs oder Rohdaten abzurufen (Web-Scraping). Schreibe das Ergebnis mit print() in den Output.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "code": { 
                    "type": "STRING", 
                    "description": "Der Python-Code. Du kannst 'import requests' nutzen, um GET/POST Anfragen zu senden. Beispiel: 'r = requests.get(url); print(r.text[:500])'." 
                }
            },
            "required": ["code"]
        }
    },
    {
        "name": "search_google_maps",
        "description": "Sucht nach Orten, Adressen, Entfernungen, Öffnungszeiten oder Navigation auf Google Maps.",
        "parameters": {
            "type": "OBJECT",
            "properties": { 
                "query": { "type": "STRING", "description": "Was gesucht werden soll (z.B. 'nächster Italiener', 'Weg zum Bahnhof')" } 
            },
            "required": ["query"]
        }
    },
{
        "name": "control_device",
        "description": "Schaltet Geräte, Lichter, Skripte, Szenen oder Schalter. Nutze 'press' für Taster oder Knöpfe.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "state": { "type": "STRING", "enum": ["on", "off", "press"] },
                "device_name": { "type": "STRING", "description": "Name des Geräts oder der Entität" }
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
    'control_device': ha.execute_device_control,
    'control_media': ha.execute_media_control,
    'get_device_state': ha.get_ha_device_state,
    'get_calendar_events': ha.get_ha_calendar_events,
    'add_calendar_event': ha.add_ha_calendar_event,
    'play_specific_music': ha.execute_play_music,
    'manage_shopping_list': ha.manage_shopping_list,
    'manage_timer_alarm': timer.manage_timer_alarm,
    'restart_service': system.restart_service,
    'set_system_volume': system.set_system_volume,
    'perform_google_search': google.perform_google_search_internal,
    'search_google_maps': google.perform_maps_search,
    'execute_python_code': system.run_local_python,
    'save_memory': memory.save_memory,
    'retrieve_memory': memory.retrieve_relevant_memories,
    'delete_memory': memory.delete_memory,
}

def execute_tool(name, args):
    """Dispatches the function call to the correct service."""
    print(f"  [DEBUG] Tool Call: {name} | Args: {args}")
    if name in TOOL_IMPLEMENTATIONS:
        try:
            result = TOOL_IMPLEMENTATIONS[name](**args)
            if not name.startswith("get_") and name != "perform_google_search":
                sfx.play(config.SOUND_SUCCESS)
            print(f"  [DEBUG] Tool Result: {result}")
            return result
        except Exception as e:
            error_msg = f"Error executing {name}: {str(e)}"
            print(f"  [DEBUG] Tool Error: {error_msg}")
            return error_msg
    
    # Fallback, wenn Funktion nicht in der Liste ist
    print(f"  [DEBUG] Error: Function '{name}' not found in TOOL_IMPLEMENTATIONS!")
    return "Funktion unbekannt"