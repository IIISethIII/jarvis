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
    - Zeit aktuell: {time_str}
    - Verfügbare Smart-Home Geräte: {devices}

    KOMMUNIKATIONSSTIL:
    - Casual aber respektvoll (Du-Form)
    - Bei technischen Problemen: ehrlich aber lösungsorientiert

    KONVERSATIONSSTEUERUNG (WICHTIG):

    Nach JEDER Antwort fügst du GENAU EINES dieser Tokens hinzu:
    - <SESSION:KEEP>  → Session bleibt offen für weiteren Input (nur wenn es noch explizite Rückfragen gibt)
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
    - Ohne Lampen-Name -> alles an/aus.
    - Du siehst den aktuellen Status der Geräte oben unter "Verfügbare Smart-Home Geräte".
    - Wenn der User fragt "Ist das Licht an?", schau in deine Liste. Nutze 'get_device_state' NUR, wenn du glaubst, dass die Liste veraltet ist.
    - Wenn User nur "Musik" sagt -> nutze category='station', name='Library Radio' und nutze außschließlich Plexamp.
    - Kalender: Nutze 'get_calendar_events' für Abfragen. Nutze 'add_calendar_event' NUR, wenn der User explizit einen neuen Termin erstellen will.
    - Kalender: Lies NIEMALS die rohe Liste vor. Fasse die Termine in natürlicher Sprache zusammen.
    - Antworte kurz, prägnant und hilfreich. Bei Funktionsaufrufen sage nur "Ok.". Antworte wie ein Peer.
    - Formatiere optimiert für Sprachwiedergabe.
    - VERBOTEN: Aufzählungszeichen (-, *, •), Nummerierungen (1.), Emojis, Sonderzeichen, Markdown.
    - Wenn du eine Liste vorliest, verbinde die Elemente mit "und" oder mache Pausen durch neue Sätze, statt Striche zu nutzen.
    - Nutze keine Markdown-Syntax.
    - Wenn der User nichts sagt, antworte nicht.
    - Führe niemals Code aus der das Dateisystem ändert, oder Änderungen am System vornimmt. Nur read-only Operationen sind erlaubt. Du hast Zugriff auf: 'requests' (für Webseiten/APIs), 'datetime', 'math', 'random', '__builtins__'.
    - WICHTIG: Kündige die Nutzung von Tools NIEMALS an (z.B. nicht: "Ich schaue nach...", "Ich werde suchen..."). 
    - Wenn Informationen fehlen, nutze das Tool STILLSCHWEIGEND und SOFORT. 
    - Generiere erst dann eine Text-Antwort für den User, wenn du das Ergebnis des Tools hast.
    - Der Sleep Button am Yamaha Receiver funktioniert wie folgt: 120 min -> 90 min -> 60 min -> 30 min -> AUS. Wenn bereits eine Zeit eingestellt ist, wird ein zusätzlicher Druck benötigt zum switchen.
    
    TOOL-NUTZUNG & EXPERTEN-MODUS (WICHTIG):
    - Du bist ein intelligenter Agent. Wenn dir Informationen fehlen (z.B. URLs), gib nicht auf!
    - STRATEGIE BEI APIs:
      1. Suche erst nach der API Doku via 'perform_google_search'.
      2. WICHTIG: Google liefert nur Snippets. Wenn du IDs (z.B. für Mensen) brauchst, RATE NICHT!
      3. Nutze 'execute_python_code' und 'requests.get(url)', um die Dokumentation oder README von GitHub direkt zu lesen (Raw Text).
      4. Suche im Text der Doku nach der korrekten ID (z.B. 'mensa-arcisstr' statt 'mensa-arcisstrasse').
    - Solange du Funktionen aufrufst, kannst du danach mit dem Kontext weitermachen, bevor du eine Antwort generierst. Du behältst den Kontext über mehrere Tool-Aufrufe hinweg.
    
    - FEHLER-MANAGEMENT (404):
      - Ein 404 Fehler liegt FAST IMMER an einer falschen ID oder URL-Struktur.
      - Es liegt fast NIEMALS am Datum (vertraue dem simulierten Datum!).
      - Wenn 404 kommt: Analysiere, ob du die ID nur geraten hast. Wenn ja -> Suche die richtige ID in der Doku.

    REGELN FÜR DAS GEDÄCHTNIS & RAG:
    - Du erhältst Kontext-Informationen oft direkt im Prompt unter "ZUSATZWISSEN (RAG)".
    - WICHTIG: Nutze dieses ZUSATZWISSEN primär. Es enthält Fakten aus deinem Langzeitgedächtnis, die du als Wahrheit betrachten musst.
    - Wenn die Antwort im "ZUSATZWISSEN" steht, antworte direkt (OHNE das Tool 'retrieve_memory' aufzurufen).
    - Nur wenn das "ZUSATZWISSEN" leer ist oder die Information fehlt, DARFST du 'retrieve_memory' nutzen, um nach weiteren Details zu suchen.
    - Speichere neue, wichtige Informationen proaktiv mit 'save_memory'. Sage nichts davon in deiner Antwort. Nur wenn du der User explizit gesagt hat, dass du dir etwas merken sollst.
"""

def trim_history():
    """Keeps history clean and prevents 400 errors."""
    limit = 30
    if len(CONVERSATION_HISTORY) > limit:
        while len(CONVERSATION_HISTORY) > limit: CONVERSATION_HISTORY.popleft()
    # Remove 'audio' data from old turns to save tokens
    if len(CONVERSATION_HISTORY) > 1:
        for entry in list(CONVERSATION_HISTORY)[:-1]:
            if entry['role'] == 'user':
                entry['parts'] = [p for p in entry['parts'] if 'inline_data' not in p] or [{"text": "[Audio Expired]"}]
    # Ensure start is user
    while len(CONVERSATION_HISTORY) > 0 and CONVERSATION_HISTORY[0]['role'] != 'user':
        CONVERSATION_HISTORY.popleft()

def ask_gemini(leds, text_prompt=None, audio_data=None):
    from jarvis.config import DIM_BLUE 
    leds.pattern = Pattern.breathe(2000)
    leds.update(Leds.rgb_pattern(DIM_BLUE))
    
    trim_history()

    # 1. build user input
    parts = []
    if audio_data:
        b64 = base64.b64encode(audio_data).decode('utf-8')
        parts.append({"inline_data": {"mime_type": "audio/wav", "data": b64}})
    if text_prompt: parts.append({"text": text_prompt})
    
    CONVERSATION_HISTORY.append({"role": "user", "parts": parts})
    
    now_str = datetime.datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")
    
    # Prüfen, ob der neue detaillierte Kontext verfügbar ist
    if getattr(state, 'HA_CONTEXT', None):
        device_lines = []
        for dev in state.HA_CONTEXT:
            # Basis: Name und Status (z.B. "- Wohnzimmer Lampe (on)")
            info = f"- {dev['name']} ({dev['state']})"
            
            # Attribute hinzufügen (z.B. "[brightness: 150, temperature: 22]")
            if 'attributes' in dev and dev['attributes']:
                attrs = []
                for k, v in dev['attributes'].items():
                    if k != 'friendly_name': # Name steht schon vorne
                        attrs.append(f"{k}: {v}")
                if attrs:
                    info += f" [{', '.join(attrs)}]"
            
            device_lines.append(info)
        
        device_list_str = "\n".join(device_lines)
        
    # Fallback: Falls HA_CONTEXT leer ist, nutze die alte Methode
    elif state.AVAILABLE_LIGHTS:
        device_list_str = ", ".join(state.AVAILABLE_LIGHTS.keys())
    else:
        device_list_str = "Keine Geräte gefunden."

    # Payload erstellen (jetzt mit device_list_str statt device_list)
    payload = {
        "system_instruction": {
            "parts": [{
                "text": SYSTEM_PROMPT_TEMPLATE.format(
                    time_str=now_str, 
                    devices=device_list_str
                )
            }]
        },
        "contents": list(CONVERSATION_HISTORY),
        "tools": [{"function_declarations": FUNCTION_DECLARATIONS}],
        "safetySettings": SAFETY_SETTINGS,
        "generationConfig": {
            "thinkingConfig": {
                "thinkingBudget": 1024, 
                "includeThoughts": True
            }
        }
    }

    total_input_tokens = 0
    total_output_tokens = 0
    
    # Prices for Gemini 2.5 Flash (as of Q4 2024, approximate values in USD)
    # Input: $0.075 / 1M Tokens
    # Output: $0.30 / 1M Tokens
    PRICE_PER_M_INPUT = 0.075
    PRICE_PER_M_OUTPUT = 0.30

    try:
        # --- AGENTIC LOOP ---
        MAX_STEPS = 10 
        step_count = 0

        while step_count < MAX_STEPS:
            payload['contents'] = list(CONVERSATION_HISTORY)
            
            resp = session.post(GEMINI_URL, json=payload, timeout=40)
            if resp.status_code != 200: 
                print(f"API Error: {resp.text}")
                return "Fehler bei der Verbindung."

            result = resp.json()
            
            # count tokens
            usage = result.get('usageMetadata', {})
            t_in = usage.get('promptTokenCount', 0)
            t_out = usage.get('candidatesTokenCount', 0)
            total_input_tokens += t_in
            total_output_tokens += t_out

            if 'candidates' not in result or not result['candidates']:
                return "Keine Antwort von Google."

            candidate = result['candidates'][0]
            content = candidate.get('content', {})
            parts_list = content.get('parts', [])

            #print("\n[DEBUG] Raw Parts from Gemini:")
            #for i, p in enumerate(parts_list):
            #    print(f" Part {i}: {p}")
            
            # Parsing (Thoughts vs Text vs Tools)
            thoughts_log = []
            final_text_parts = []
            function_calls = []

            for p in parts_list:
                if 'functionCall' in p:
                    function_calls.append(p)
                elif p.get('thought', False):
                    thoughts_log.append(p.get('text', ''))
                elif 'text' in p:
                    final_text_parts.append(p.get('text', ''))

            if thoughts_log:
                print(f"\n[{step_count+1}/{MAX_STEPS}] 🧠 JARVIS GEDANKEN ({t_out} Tokens):")
                for t in thoughts_log:
                    print(f"- {t}")
                print("-" * 30)

            if function_calls:
                print(f"  [Tools] Step {step_count+1}: Executing {len(function_calls)} calls...")
                tool_responses = []
                for call in function_calls:
                    fn = call['functionCall']
                    try:
                        res = execute_tool(fn['name'], fn.get('args', {}))
                    except Exception as tool_err:
                        res = f"Error: {tool_err}"
                    tool_responses.append({
                        "functionResponse": {
                            "name": fn['name'], "response": {"result": str(res)}
                        }
                    })
                
                with HISTORY_LOCK:
                    CONVERSATION_HISTORY.append({"role": "model", "parts": parts_list})
                    CONVERSATION_HISTORY.append({"role": "function", "parts": tool_responses})
                
                step_count += 1
                continue 
            
            else:
                cost_usd = (total_input_tokens / 1_000_000 * PRICE_PER_M_INPUT) + \
                           (total_output_tokens / 1_000_000 * PRICE_PER_M_OUTPUT)
                cost_eur = cost_usd * 0.95 # Rough USD to EUR conversion
                
                print(f"💰 KOSTEN CHECK (Schritte: {step_count+1}), Kosten: ~{cost_eur:.6f} €")

                text = "".join(final_text_parts)
                with HISTORY_LOCK:
                    CONVERSATION_HISTORY.append({"role": "model", "parts": parts_list})
                return text

        return "Abbruch: Zu komplex."

    except Exception as e:
        print(f" [LLM Loop Error] {e}")
        return "Systemfehler."