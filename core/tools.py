# jarvis/core/tools.py
import jarvis.services.ha as ha
import jarvis.services.system as system
import jarvis.services.timer as timer
import jarvis.services.google as google
import jarvis.services.sfx as sfx
import jarvis.services.memory as memory
import jarvis.services.navigation as navigation
from jarvis import config
from jarvis.core.mcp import mcp_client

# 1. Definitions
FUNCTION_DECLARATIONS = [
    {
        "name": "save_memory",
        "description": "Speichert explizit einen Fakt oder Wunsch des Users für die Zukunft. Nutze dies nur, wenn der User sagt 'Merk dir X' oder 'Ich mag Y'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "text": { "type": "STRING", "description": "Der Fakt (z.B. 'Der Türcode ist 1234')." }
            },
            "required": ["text"]
        }
    },
    {
        "name": "retrieve_memory",
        "description": "Sucht aktiv nach Details in vergangenen Gesprächen, falls du etwas nicht im aktuellen Kontext findest.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "search_query": { "type": "STRING", "description": "Suchbegriff (z.B. 'WLAN Passwort', 'Was habe ich gestern gegessen?')." }
            },
            "required": ["search_query"]
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
    # {
    #    "name": "search_google_maps",
    #    "description": "Sucht nach Orten, Adressen, Entfernungen, Öffnungszeiten oder Navigation auf Google Maps.",
    #    "parameters": {
    #        "type": "OBJECT",
    #        "properties": { 
    #            "query": { "type": "STRING", "description": "Was gesucht werden soll (z.B. 'nächster Italiener', 'Weg zum Bahnhof')" } 
    #        },
    #        "required": ["query"]
    #    }
    #},
    {
        "name": "control_device",
        "description": "Schaltet Geräte an/aus. WICHTIG: Nutze NUR die entity_id!",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "state": { "type": "STRING", "enum": ["on", "off", "press"] },
                "device_name": { 
                    "type": "STRING", 
                    "description": "Die EXAKTE 'entity_id' aus der Geräteliste (z.B. 'light.wohnzimmer' oder 'switch.steckdose'). KEINE Friendly Names!" 
                }
            },
            "required": ["state", "device_name"]
        }
    },
    {
        "name": "control_media",
        "description": "Steuert Musikwiedergabe.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": { 
                    "type": "STRING", 
                    "enum": ["play", "pause", "play_pause", "stop", "next", "previous", "volume_up", "volume_down", "volume_set"]
                },
                "device_name": { "type": "STRING", "description": "Die EXAKTE 'entity_id' des Media Players (z.B. media_player.plex...)." },
                "volume_level": { "type": "NUMBER", "description": "0-100" }
            },
            "required": ["command"]
        }
    },
    {
        "name": "get_device_state",
        "description": "Prüft Status.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "device_name": { "type": "STRING", "description": "Die EXAKTE 'entity_id' (z.B. sensor.temp)." }
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
        "description": "Spielt Musik ab.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": { "type": "STRING", "enum": ["artist", "album", "playlist", "genre", "track", "station"] },
                "name": { "type": "STRING" },
                "library": { "type": "STRING", "enum": ["Music", "Audiobooks"] },
                "device_name": { "type": "STRING", "description": "Die entity_id des Players." }
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
    #{
    #    "name": "perform_google_search",
    #    "description": "Suche nach Fakten oder Wissen und allen Informationen die du nicht weißt.",
    #    "parameters": {
    #        "type": "OBJECT",
    #        "properties": { "query": { "type": "STRING" } },
    #        "required": ["query"]
    #    }
    #},
    # {
    #    "name": "get_calendar_events",
    #    "description": "Liest Termine. Nutze days=0 für 'heute', days=1 für 'heute und morgen', days=7 für 'die Woche'.",
    #    "parameters": {
    #        "type": "OBJECT",
    #        "properties": {
    #            "count": { "type": "INTEGER", "description": "Max Anzahl (Default 5)" },
    #            "days": { "type": "INTEGER", "description": "0=Heute, 1=Morgen mit dazu, 7=Woche" }
    #        }
    #    }
    #},
    #{
    #    "name": "add_calendar_event",
    #    "description": "Erstellt einen Termin. Datum muss ISO sein.",
    #    "parameters": {
    #        "type": "OBJECT",
    #        "properties": {
    #            "summary": { "type": "STRING", "description": "Titel" },
    #            "start_time_iso": { "type": "STRING", "description": "ISO Format YYYY-MM-DDTHH:MM:SS" },
    #            "duration_minutes": { "type": "INTEGER" }
    #        },
    #        "required": ["summary", "start_time_iso"]
    #    }
    #},
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
    {
        "name": "send_to_phone",
        "description": "Nutze dieses Tool PROAKTIV, wann immer eine Antwort visuelle Elemente enthält (Bilder), zu lang zum Vorlesen ist (Rezepte, Code, lange Listen) oder eine Navigation erfordert. Du kannst Text, Links (Maps, Webseiten) und Bild-URLs senden.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "message": { "type": "STRING", "description": "Der Text der Nachricht (kurz und prägnant)." },
                "title": { "type": "STRING", "description": "Titel der Nachricht." },
                "url": { "type": "STRING", "description": "Optional: URL die geöffnet wird (z.B. Google Maps Link, Webseite, HomeAssistant Dashboard Link)." },
                "image_url": { "type": "STRING", "description": "Optional: URL zu einem Bild, das direkt in der Notification angezeigt werden soll." },
                "priority": { "type": "STRING", "enum": ["normal", "high"], "description": "Nutze 'high' nur für Alarme oder extrem wichtige Warnungen." }
            },
            "required": ["message"]
        }
    },
    {
        "name": "plan_outdoor_route",
        "description": "Erstellt eine Komoot-Route (Fahrrad/Wandern). Gibt Dauer, Distanz und URL zurück. WICHTIG: Sendet NICHT automatisch. Du musst danach 'send_to_phone' mit der generierten URL aufrufen!",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "destination": { "type": "STRING", "description": "Das Ziel (Stadt, Adresse, POI)." },
                "sport": { 
                    "type": "STRING", 
                    "enum": ["rennrad", "fahrrad", "mtb", "wandern", "joggen"],
                    "description": "Die Sportart. Default ist 'fahrrad'."
                },
                "start": { "type": "STRING", "description": "Optional: Ein anderer Startpunkt als Zuhause." }
            },
            "required": ["destination"]
        }
    },
    {
        "name": "get_weather_forecast",
        "description": "Ruft die detaillierte Wettervorhersage ab. WICHTIG: Nutze dies IMMER für Fragen wie 'Regnet es gleich?', 'Kann ich joggen?', 'Wie wird das Wetter morgen?'. Der normale Status reicht dafür nicht.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "type": { 
                    "type": "STRING", 
                    "enum": ["hourly", "daily"],
                    "description": "Nutze 'hourly' für heute/kurzfristig (Regenwahrscheinlichkeit) und 'daily' für die nächsten Tage." 
                },
                "entity_id": { 
                    "type": "STRING", 
                    "description": "Optional: Die Wetter-Entität (z.B. weather.open_meteo). Falls leer, wird automatisch eine gesucht." 
                }
            },
            "required": ["type"]
        }
    },
    {
        "name": "schedule_wakeup",
        "description": "Erlaubt es Jarvis, selbst zu entscheiden, wann er wieder aufwachen soll. Nutze dies, wenn du später noch etwas erledigen musst oder proaktiv sein willst.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "minutes": { "type": "INTEGER", "description": "Dauer in Minuten bis zum Wakeup." },
                "reason": { "type": "STRING", "description": "Grund für den Wakeup (als Notiz an dich selbst)." }
            },
            "required": ["minutes", "reason"]
        }
    },
    {
        "name": "schedule_conditional_wakeup",
        "description": "Erlaubt es Jarvis, aufzuwachen, wenn ein HomeAssistant-Gerät einen bestimmten Zustand hat oder eine Bedingung erfüllt ist (z.B. 'Wenn ich nach Hause komme', 'Wenn das Licht ausgeht'). Erstellt eine native HomeAssistant Automation.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "entity_id": { "type": "STRING", "description": "Die HomeAssistant Entity ID (z.B. person.paul, light.kitchen)." },
                "summary": { "type": "STRING", "description": "Grund für den Wakeup." },
                "target_value": { "type": "STRING", "description": "Der Zielwert (on/off/home/not_home) oder 'lat,lon,radius' bei Geo-Conditions." },
                "condition_type": { 
                    "type": "STRING", 
                    "enum": ["state_match", "numeric", "geolocation"],
                    "description": "Art der Bedingung. Default ist 'state_match'."
                },
                "operator": { "type": "STRING", "enum": [">", "<", "==", ">=", "<="], "description": "Nur für 'numeric' Conditions." }
            },
            "required": ["entity_id", "summary", "target_value"]
        }
    },
    {
        "name": "delete_wakeup_automation",
        "description": "Löscht eine Wakeup-Automation. Nutze dies, wenn die Automation ihren Zweck erfüllt hat, damit sie nicht immer wieder feuert.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "auto_id": { "type": "STRING", "description": "Die ID der Automation (wird dir beim Wakeup mitgeteilt)." }
            },
            "required": ["auto_id"]
        }
    },
    {
        "name": "get_ha_history",
        "description": "Ruft historische Daten (Verlauf) für Home Assistant Entitäten ab. Nutze dies, um herauszufinden, wann etwas passiert ist oder wie der Verlauf war.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "entity_ids": { 
                    "type": "ARRAY", 
                    "items": { "type": "STRING" }, 
                    "description": "Die Entitäts-IDs, als Array z.B. ['person.paul', 'light.wohnzimmer']." 
                },
                "start_time": { "type": "STRING", "description": "Startzeitpunkt im ISO-Format (z.B. '2023-10-01T12:00:00')." },
                "end_time": { "type": "STRING", "description": "Endzeitpunkt im ISO-Format (optional)." },
                "minimal_response": { "type": "BOOLEAN", "description": "True für kompakte Antwort ohne lange JSON Attribute (empfohlen)." }
            },
            "required": ["entity_ids", "start_time"]
        }
    },
    {
        "name": "end_conversation",
        "description": "Beendet die aktuelle Konversation. Nutze dies, wenn der User meint, dass er fertig ist (z.B. 'Danke' Das wars', 'Tschüss', 'Machs gut'). Bestätige immer mit einer kurzen Nachricht, dass die Konversation beendet wird. Zum Beispiel: 'Gern geschehen! Ok. Tschau.'",
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
    'save_memory': memory.save_memory_tool,
    'retrieve_memory': memory.search_memory_tool,
    'send_to_phone': ha.send_notification,
    'plan_outdoor_route': navigation.handle_route_planning,
    'get_weather_forecast': ha.get_weather_forecast,
    'schedule_wakeup': system.schedule_wakeup,
    'schedule_conditional_wakeup': ha.create_ha_automation,
    'delete_wakeup_automation': ha.delete_ha_automation,
    'get_ha_history': ha.get_ha_history,
    'end_conversation': lambda **kwargs: "Konversation wird beendet.",
}

def execute_tool(name, args, silent_mode=False):
    """Dispatches the function call to the correct service."""
    print(f"  [DEBUG] Tool Call: {name} | Args: {args}")
    
    # 1. NEW: Check if this is a remote MCP tool
    if name in mcp_client.mcp_tools_cache:
        try:
            result = mcp_client.execute_sync(name, args)
            if not silent_mode:
                sfx.play(config.SOUND_SUCCESS)
            print(f"  [DEBUG] MCP Result: {result}")
            return result
        except Exception as e:
            return f"MCP Error executing {name}: {str(e)}"

    if name in TOOL_IMPLEMENTATIONS:
        try:
            result = TOOL_IMPLEMENTATIONS[name](**args)
            if not silent_mode and not name.startswith("get_") and name != "perform_google_search":
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