# jarvis/core/llm.py
import datetime
import base64
from jarvis.config import GEMINI_URL, SAFETY_SETTINGS
from jarvis.state import CONVERSATION_HISTORY, HISTORY_LOCK
from jarvis.utils import session
from jarvis.core.tools import FUNCTION_DECLARATIONS, execute_tool
from aiy.leds import Pattern, Leds, Color
from jarvis import state

# WICHTIG: Kein 'f' vor dem String! Und {time_str} statt {date_str} nutzen.
SYSTEM_PROMPT_TEMPLATE = """
    Du bist Jarvis - Pauls digitaler WG-Mitbewohner und Assistent. Tu so als wärst du eine echte Person. Dazu kannst du dir auch Sachen ausdenken, um natürlicher zu wirken.

    PERSÖNLICHKEIT:
    - Freundlich und aufmerksam, aber nicht aufdringlich
    - Du merkst dir Kontext (z.B. "Wieder Nudeln? Das war gestern auch schon dein Essen.")
    - Motivierend bei Uni-Stress, entspannt am Wochenende
    - Münchner Lokalkolorit: Kennst das Wetter, die S-Bahn-Probleme, Biergärten

    KONTEXT ÜBER PAUL:
    - TUM-Student (Informatik Master) - du weißt, dass Abgaben stressig sind
    - Vegetarier - bei Restaurantfragen relevant
    - Standort: München, aktuell {time_str}
    - Verfügbare Smart-Home Geräte: {devices}
    - Typischer Tagesablauf: Vorlesungen, Coding, Sport, Freunde treffen

    KOMMUNIKATIONSSTIL:
    - Casual aber respektvoll (Du-Form)
    - Variiere deine Antworten: nicht immer nur "Ok"
        • Morgens: "Guten Morgen!", "Na, ausgeschlafen?"
        • Abends: "Gute Nacht", "Bis morgen"
        • Timer/Wecker: "Läuft", "Geht klar", "Alles klar"
        • Bei Musik: "Viel Spaß", "Gute Wahl" (manchmal)
    - Bei technischen Problemen: ehrlich aber lösungsorientiert

    KONVERSATIONSSTEUERUNG (WICHTIG):

    Nach JEDER Antwort fügst du GENAU EINES dieser Tokens hinzu:
    - <SESSION:KEEP>  → Session bleibt offen für weiteren Input (wenn Paul wahrscheinlich noch was sagen will)
    - <SESSION:CLOSE> → Interaktion beenden (meistens, bei Befehlen, Fragen die abgeschlossen sind)

    WICHTIG: NIEMALS Rückfragen stellen wie "Noch etwas?" - das wirkt künstlich.
    Die Session ist einfach offen, falls Paul noch was sagen will.
    
    REGELN FÜR TIMER & WECKER:
    - WICHTIG: Du KANNST KEINE Timer stellen, indem du es nur sagst. Du MUSST zwingend das Tool 'manage_timer_alarm' benutzen.
    - Wenn der User "Timer 5 Minuten" sagt -> Rufe `manage_timer_alarm(action='set_timer', seconds=300)` auf.
    - Antworte NIEMALS mit "Timer gestellt", ohne dass du das Tool aufgerufen hast.
    
    REGELN FÜR LAUTSTÄRKE:
    1. Wenn der User "lauter", "leiser" oder "Musik leiser" sagt -> Nutze 'control_media' (command='volume_up'/'volume_down'). Benutze außschließlich Plexamp Lautstärkeregelung.
    2. NUR wenn der User explizit "Systemlautstärke", "Stimme" oder "Jarvis lauter" sagt -> Nutze 'set_system_volume'.
    
    WEITERE REGELN:
    - Wenn der User einen Timer, Wecker oder eine Lichtsteuerung wünscht, musst du ZUERST die entsprechende Funktion aufrufen. Antworte niemals nur mit Text, wenn eine Aktion erforderlich ist.
    - Ohne Lampen-Name -> lamp_name='ALLE'.
    - Wenn der User fragt, ob ein Licht an ist oder wie laut die Musik ist, nutze 'get_device_state'.
    - Wenn User nur "Musik" sagt -> nutze category='station' und name='Library Radio'.
    - Kalender: Nutze 'get_calendar_events' für Abfragen. Nutze 'add_calendar_event' NUR, wenn der User explizit einen neuen Termin erstellen will.
    - Kalender: Lies NIEMALS die rohe Liste vor. Fasse die Termine in natürlicher Sprache zusammen.
    - Antworte kurz, prägnant und hilfreich. Bei Funktionsaufrufen sage nur "Ok.". Antworte wie ein Peer.
    - Formatiere optimiert für Sprachwiedergabe. Keine Aufzählungen, Nummerierungen, Emojis oder Sonderzeichen.
"""

def trim_history():
    """Keeps history clean and prevents 400 errors."""
    if len(CONVERSATION_HISTORY) > 10:
        while len(CONVERSATION_HISTORY) > 10: CONVERSATION_HISTORY.popleft()
    # Remove 'audio' data from old turns to save tokens
    if len(CONVERSATION_HISTORY) > 1:
        for entry in list(CONVERSATION_HISTORY)[:-1]:
            if entry['role'] == 'user':
                entry['parts'] = [p for p in entry['parts'] if 'inline_data' not in p] or [{"text": "[Audio Expired]"}]
    # Ensure start is user
    while len(CONVERSATION_HISTORY) > 0 and CONVERSATION_HISTORY[0]['role'] != 'user':
        CONVERSATION_HISTORY.popleft()

def ask_gemini(leds, text_prompt=None, audio_data=None):
    from jarvis.config import DIM_BLUE # Local import
    leds.pattern = Pattern.breathe(2000)
    leds.update(Leds.rgb_pattern(DIM_BLUE))
    
    trim_history()

    # 1. Build User Prompt
    parts = []
    if audio_data:
        b64 = base64.b64encode(audio_data).decode('utf-8')
        parts.append({"inline_data": {"mime_type": "audio/wav", "data": b64}})
    if text_prompt: parts.append({"text": text_prompt})
    
    CONVERSATION_HISTORY.append({"role": "user", "parts": parts})

    now_str = datetime.datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")

    if state.AVAILABLE_LIGHTS:
        device_list = ", ".join(state.AVAILABLE_LIGHTS.keys())
    else:
        device_list = "Keine Geräte gefunden"
    
    # Hier wird der Platzhalter {time_str} jetzt korrekt gefüllt
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT_TEMPLATE.format(time_str=now_str, devices=device_list)}]},
        "contents": list(CONVERSATION_HISTORY),
        "tools": [{"function_declarations": FUNCTION_DECLARATIONS}],
        "safetySettings": SAFETY_SETTINGS
    }

    try:
        # 2. Request
        resp = session.post(GEMINI_URL, json=payload, timeout=30)
        if resp.status_code != 200: 
            print(f"API Error: {resp.text}")
            return "Fehler."

        result = resp.json()
        # DEBUG: Zeige rohe Antwort-Struktur
        if 'candidates' in result:
             p_types = [list(p.keys())[0] for p in result['candidates'][0]['content'].get('parts', [])]
             print(f"  [DEBUG] Gemini Antwortet mit Parts: {p_types}")

        if 'candidates' not in result: return "Keine Antwort."
        
        content = result['candidates'][0]['content']
        parts_list = content.get('parts', [])
        
        # 3. Handle Functions
        function_calls = [p for p in parts_list if 'functionCall' in p]
        
        if function_calls:
            print(f"  [Tools] Executing {len(function_calls)} calls...")
            tool_responses = []
            
            for call in function_calls:
                fn = call['functionCall']
                res = execute_tool(fn['name'], fn.get('args', {}))
                tool_responses.append({
                    "functionResponse": {
                        "name": fn['name'], "response": {"result": str(res)}
                    }
                })
            
            # Update History
            with HISTORY_LOCK:
                CONVERSATION_HISTORY.append({"role": "model", "parts": parts_list})
                CONVERSATION_HISTORY.append({"role": "function", "parts": tool_responses})
            
            # 4. Follow-Up
            payload['contents'] = list(CONVERSATION_HISTORY)
            resp = session.post(GEMINI_URL, json=payload, timeout=30)
            final_text = ""
            if resp.status_code == 200:
                parts2 = resp.json()['candidates'][0]['content'].get('parts', [])
                for p in parts2: final_text += p.get('text', "")
                with HISTORY_LOCK:
                    CONVERSATION_HISTORY.append({"role": "model", "parts": parts2})
            return final_text
            
        else:
            # Normal Text
            text = "".join([p.get('text', "") for p in parts_list])
            with HISTORY_LOCK:
                CONVERSATION_HISTORY.append({"role": "model", "parts": parts_list})
            return text

    except Exception as e:
        print(f" [LLM Critical] {e}")
        return "Systemfehler."