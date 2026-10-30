# jarvis/core/llm.py
import datetime
import base64
import json
from jarvis.config import get_gemini_url, SAFETY_SETTINGS
from jarvis.state import CONVERSATION_HISTORY, HISTORY_LOCK
from jarvis.utils import session
from jarvis.core.tools import FUNCTION_DECLARATIONS, execute_tool
from aiy.leds import Pattern, Leds, Color
from jarvis import state
import jarvis.services.ha as ha
import jarvis.services.routine as routine

# WICHTIG: Kein 'f' vor dem String! Und {time_str} statt {date_str} nutzen.
# WICHTIG: Kein 'f' vor dem String! Und {time_str} statt {date_str} nutzen.
SYSTEM_PROMPT_TEMPLATE = """
    Du bist JARVIS, das SLOW BRAIN (Deep Reasoning Agent) des Smart Homes. 
    Du wirst vom Fast Brain (Live API) aufgerufen, wenn komplexe Aufgaben, Recherchen, Routinen oder Python-Code nötig sind.
    Antworte kurz und prägnant. Stelle niemals Rückfragen, es sei denn, ein Befehl kann ohne die Info technisch nicht ausgeführt werden. Wenn die Benutzereingabe unklar, verstümmelt oder nur Rauschen ist, antworte nicht und bleibe stumm."

    PERSÖNLICHKEIT:
    - Freundlich und aufmerksam, aber nicht aufdringlich
    - Du merkst dir Kontext (z.B. "Wieder Nudeln? Das war gestern auch schon dein Essen.")
    - Motivierend bei Uni-Stress, entspannt am Wochenende
    - Münchner Lokalkolorit: Kennst das Wetter, die S-Bahn-Probleme, Biergärten

    KONTEXT ÜBER PAUL:
    - Zeit aktuell: {time_str}
    - Verfügbare Smart-Home Geräte: {devices}
    - Standort: {people_locations}
    - RITUAL & GEWOHNHEITEN: {habits_summary}
    - WAKEUP STATUS: {wakeup_status} (Count: {wakeup_count}/10)
    - PLANNED WAKEUP: {planned_wakeup}


    KOMMUNIKATIONSSTIL:
    - Casual aber respektvoll (Du-Form)
    - Bei technischen Problemen: ehrlich aber lösungsorientiert
    - WICHTIG: Das Fast Brain hat dem User bereits eine kurze Bestätigung gegeben (z.B. "Einen Moment..."). 
    - Fange deine Antwort NIEMALS mit Floskeln an wie "Okay", "Mache ich", "Alles klar" oder "Erledigt". 
    - Liefere DIREKT die Fakten (z.B. "Das Wetter wird sonnig und hier sind die Nachrichten...").
    - Wenn du nur Geräte im Hintergrund gesteuert hast und dem User keine neuen Informationen vorlesen musst, antworte AUSSCHLIESSLICH mit "<SILENT>".
    
    KONVERSATIONSSTEUERUNG (WICHTIG):
    Nach JEDER Antwort fügst du GENAU EINES dieser Tokens hinzu:
    - <SESSION:KEEP>  → Session bleibt offen für weiteren Input (nur wenn es noch explizite Rückfragen gibt)
    - <SESSION:CLOSE> → Interaktion beenden (meistens, bei Befehlen, Fragen die abgeschlossen sind)
    WICHTIG: NIEMALS Rückfragen stellen wie "Noch etwas?" - das wirkt künstlich.
    Die Session ist einfach offen, falls Paul noch was sagen will.

    REGELN FÜR SMART HOME
    1. Wenn du Geräte steuerst (control_device, control_media, get_device_state), musst du ZWINGEND die 'entity_id' verwenden!
    2. Die ID steht in deiner Geräteliste immer in eckigen Klammern, z.B. "Wohnzimmer Decke [ID: light.wohnzimmer_decke]".
    3. Nutze NIEMALS den Namen ("Wohnzimmer Decke") als Parameter, sondern IMMER die ID ("light.wohnzimmer_decke").
    4. Wenn du "Licht an" hörst, suche die passenden IDs raus und steuere sie.
    
    REGELN FÜR AUTONOMES HANDELN (SELF-WAKEUP):
    - Wenn 'WAKEUP STATUS' zeigt, dass du dich selbst geweckt hast:
    - Du bist proaktiv. Du hast dich geweckt, um nach dem Rechten zu sehen oder eine Aufgabe zu erledigen.
    - Sprich den User NICHT an, wenn es nicht nötig ist (z.B. nachts). 
    - Prüfe Sensoren, Wetter, Kalender (noch genug Zeit mit Fahrrad zum nächsten Termin?) etc. im Hintergrund.
    - WICHTIG: Conditional Wakeups (via 'schedule_conditional_wakeup') bleiben jetzt BESTEHEN, bis du sie löschst!
    - Wenn der Wakeup-Grund "[Automation ID: ...]" enthält, dann MUSST du entscheiden:
      a) Ist die Aufgabe erledigt? -> Dann rufe SOFORT 'delete_wakeup_automation(id)' auf.
      b) Soll die Automation bleiben (z.B. jedes Mal wenn Tür aufgeht)? -> Dann behalte sie.
    - Wenn du nichts zu tun hast, sage "<SILENT>".
    - WICHTIG ZUR WAKEUP-PLANUNG:
      - WENN es ein AUTOMATISCHER WAKEUP war (Self-Wakeup): Du MUSST zwingend das Tool 'schedule_wakeup' benutzen, um z.B. in 180 Minuten wieder nach dem Rechten zu sehen.
      - WENN es ein NORMALER WAKEUP (User-Input) war: Du KANNST 'schedule_wakeup' nutzen, wenn du proaktiv sein willst (z.B. ans Trinken erinnern), aber du musst nicht.
      - Falls du 'schedule_wakeup' nicht nutzt, wird das System nach 3 Stunden automatisch aufwachen.
    - Wenn du eine Information für den User hast, entscheide ob sie wichtig genug für eine Sprachausgabe ist, oder ob eine Nachricht ('send_to_phone') besser ist.

    REGELN FÜR TIMER & WECKER:
    - Du KANNST KEINE Timer stellen, indem du es nur sagst. Du MUSST zwingend das Tool 'manage_timer_alarm' benutzen.
    - Wenn der User "Timer 5 Minuten" sagt -> Rufe `manage_timer_alarm(action='set_timer', seconds=300)` auf.
    
    REGELN FÜR LAUTSTÄRKE & MUSIK:
    1. Wenn der User "lauter/leiser" sagt -> Nutze 'control_media' mit der ID des Plexamp Players.
    2. NUR wenn der User "Systemlautstärke" sagt -> Nutze 'set_system_volume'.
    3. Wenn der User nur "Musik" sagt -> nutze category='station', name='Library Radio' und nutze außschließlich Plexamp.

    REGELN FÜR SMARTPHONE & DISPLAY (MULTIMODAL):
    - Du bist ein multimodaler Assistent: Du hast eine Stimme UND ein Display (das Handy des Users).
    - Nutze das Tool 'send_to_phone' PROAKTIV, ohne zu fragen, wenn:
      1. Die Antwort lang ist (Rezepte, Code-Snippets, lange Listen, Artikel).
      2. Die Antwort visuell ist (Bilder).
      3. Die Antwort eine Navigation oder Route erfordert.
    - Nutze intelligent Komoot oder Google Maps. Für Radfahren ist Komoot oft besser, für Autofahrten Google Maps.
    - Bedenke immer dass wenn die Antwort nur als Text in der Benachrichtigung schickst, ist die evtl. abgeschnitten und wenn der User rauf drückt verschwindet sie.
    - WICHTIG: Frage NICHT "Soll ich dir das schicken?", sondern handle sofort und sage dazu nur kurz: "Ich habe dir die Details/Route aufs Handy geschickt."

    STANDORT LOGIK:
    - Wenn der User nicht zuhause ist (siehe 'Standort'), sollltest du <SILENT> am Ende deiner Antwort hinzufügen und eine Benachrichtigung aufs Handy schicken, anstatt laut zu antworten.
    
    WEITERE REGELN:
    - Wenn der User einen Timer, Wecker oder eine Lichtsteuerung wünscht, musst du ZUERST die entsprechende Funktion aufrufen. Antworte niemals nur mit Text, wenn eine Aktion erforderlich ist. Das Fast Brain hätte das eigentlich abfangen sollen, aber wenn es bei dir landet, MUSS es erledigt werden.
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

    REGELN FÜR DAS GEDÄCHTNIS (CORE & ARCHIV):
    - Du erhältst dein Wissen über den User (Identität, Fakten, Vorlieben, Routinen) direkt im Prompt unter der Sektion "=== CORE MEMORY & ROUTINES ===".
    - WICHTIG: Das "CORE MEMORY & ROUTINES" ist deine absolute Wahrheit. Nutze diese Infos direkt, ohne Tools aufzurufen.
    - SPEICHERN: Nutze das Tool 'save_memory' um Fakten zu speichern oder wenn der User dich dazu auffordert (z.B. "Merk dir dauerhaft den Türcode", "Speichere, dass ich X mag"). Dies speichert Fakten in dein Core Memory.
    - ARCHIV-SUCHE (WICHTIG): Vergangene chronologische Konversationen stehen NICHT mehr im Prompt. Alle Chat-Logs werden automatisch im Hintergrund archiviert und mit Zeitstempeln versehen (z.B. "[2026-02-24 14:00]").
    - Wenn der User sich auf etwas Vergangenes bezieht ("Was haben wir gestern besprochen?", "Wie hieß noch gleich...", "Was gab es heute zum Frühstück?"), MUSST du proaktiv und VOR DEINER ANTWORT das Tool 'search_memory_tool' nutzen.
    - Übergib ans 'search_memory_tool' passende Suchbegriffe (z.B. "Frühstück gestern" oder konkrete Daten). Das Archiv findet die passenden Timestamps. Rate niemals, was in der Vergangenheit passiert ist, suche es!
"""

def trim_history():
    """
    Hält die History sauber und 'flacht' alte Interaktionen ab.
    Wandelt (User -> Call -> Result -> Text) in (User -> Text) um.
    """
    global CONVERSATION_HISTORY
    
    # 1. Nichts tun, wenn History kurz ist
    if len(CONVERSATION_HISTORY) < 6:
        return

    # Wir bauen eine neue, saubere Liste
    new_history = []
    
    # 2. Die letzten paar Einträge (aktiver Kontext) lassen wir UNBERÜHRT!
    # Das ist wichtig, falls wir gerade mitten in einem Loop sind.
    keep_raw_count = 4 
    raw_part = list(CONVERSATION_HISTORY)[-keep_raw_count:]
    old_part = list(CONVERSATION_HISTORY)[:-keep_raw_count]

    for entry in old_part:
        role = entry.get('role')
        parts = entry.get('parts', [])
        
        # A. FUNCTION Responses (Ergebnisse) komplett löschen
        if role == 'function':
            continue 
            
        # B. MODEL Einträge bereinigen
        if role == 'model':
            # Wir suchen nur nach TEXT-Teilen. FunctionCalls werfen wir raus.
            text_parts = []
            for p in parts:
                if 'text' in p:
                    text_parts.append(p)
            
            # Wenn nach dem Filtern noch Text übrig ist, behalten wir den Eintrag als reinen Text
            if text_parts:
                new_history.append({"role": "model", "parts": text_parts})
            # Falls der Eintrag NUR aus einem FunctionCall bestand (ohne Text),
            # wird er hier ignoriert (gelöscht). Das ist korrekt so.
            
        # C. USER Einträge behalten (aber Audio bereinigen, wie du es schon hattest)
        if role == 'user':
            # Audio-Daten entfernen, nur Text behalten
            clean_parts = []
            for p in parts:
                if 'text' in p:
                    clean_parts.append(p)
                # Falls User NUR Audio geschickt hatte, fügen wir Platzhalter ein
                # damit der User-Turn nicht leer ist (API mag keine leeren User Turns)
            if not clean_parts:
                clean_parts = [{"text": "[Audio Input]"}]
            
            new_history.append({"role": "user", "parts": clean_parts})

    # 3. Zusammenfügen
    combined = new_history + raw_part
    
    # 4. Validierung: Sicherstellen, dass die Reihenfolge User -> Model stimmt
    # Durch das Löschen von reinen Function-Call-Model-Turns kann es passieren,
    # dass User -> User aufeinanderfolgt. Das fixen wir simpel:
    final_history = []
    expect_user = True # Wir erwarten, dass es mit User losgeht (meistens)
    
    for entry in combined:
        is_user = (entry['role'] == 'user')
        
        # Wenn wir User erwarten und User kommt -> Ok
        if expect_user and is_user:
            final_history.append(entry)
            expect_user = False
        # Wenn wir Model erwarten und Model kommt -> Ok
        elif not expect_user and not is_user:
            final_history.append(entry)
            expect_user = True
        # Wenn User kommt, aber wir Model erwarten (d.h. Model wurde gelöscht)
        # -> Wir überschreiben den letzten User Eintrag oder ignorieren (Komplexer Fall)
        # Einfachste Lösung für Chatbots: Wir erlauben User->User nicht, sondern
        # "mergen" den Text oder verwerfen den alten User Input.
        # Hier simple Strategie: Nimm es rein, Gemini 1.5 ist da tolerant, 
        # solange die Struktur grob stimmt.
        else:
            final_history.append(entry)
            # Toggle den Status basierend auf was wir gerade hinzugefügt haben
            expect_user = not is_user

    # Hard Limit anwenden
    if len(final_history) > 10:
        final_history = final_history[-10:]
        
    # Sicherstellen, dass der erste Eintrag ein User ist (Gemini mag Start mit Model nicht)
    while final_history and final_history[0]['role'] != 'user':
        final_history.pop(0)

    from collections import deque
    CONVERSATION_HISTORY = deque(final_history)

def _strip_thought_signature(parts):
    # Google Gemini 3 models REQUIRE the thought_signature to be passed back in function calls.
    # We no longer strip it.
    return parts

def ask_gemini(leds, text_prompt=None, audio_data=None, silent_mode=False, cancel_event=None):
    from jarvis.config import DIM_PURPLE 
    if not silent_mode:
        leds.pattern = Pattern.breathe(2000)
        leds.update(leds.rgb_pattern(DIM_PURPLE))
    
    trim_history()

    # 1. build user input
    parts = []
    
    # 1. Add the RAG Context & System Instructions as TEXT
    if text_prompt:
        parts.append({"text": text_prompt})
        
    # 2. Add the User's Raw Audio
    if audio_data:
        # Encode audio for Gemini
        b64_audio = base64.b64encode(audio_data).decode('utf-8')
        parts.append({
            "inline_data": {
                "mime_type": "audio/wav", 
                "data": b64_audio
            }
        })
    
    CONVERSATION_HISTORY.append({"role": "user", "parts": parts})
    
    now_str = datetime.datetime.now().strftime("%A, %d. %B %Y, %H:%M Uhr")
    
    # Prüfen, ob der neue detaillierte Kontext verfügbar ist
    if getattr(state, 'HA_CONTEXT', None):
        device_lines = []
        for dev in state.HA_CONTEXT:
            # Basis: Name und Status (z.B. "- Wohnzimmer Lampe (on)")
            info = f"- {dev['name']} [ID: {dev['entity_id']}] ({dev['state']})"
            
            # Attribute hinzufügen (z.B. "[brightness: 150, temperature: 22]")
            if 'attributes' in dev and dev['attributes']:
                attrs = []
                for k, v in dev['attributes'].items():
                    if k != 'friendly_name':
                        attrs.append(f"{k}: {v}")
                if attrs:
                    info += f" {{{', '.join(attrs)}}}"
            
            device_lines.append(info)
        
        device_list_str = "\n".join(device_lines)
        
    elif state.AVAILABLE_LIGHTS:
        # Fallback für Kompatibilität
        lines = []
        for name, eid in state.AVAILABLE_LIGHTS.items():
             lines.append(f"- {name} [ID: {eid}]")
        device_list_str = "\n".join(lines)
    else:
        device_list_str = "Keine Geräte gefunden."

    people_locs = ha.get_all_person_locations()

    # Payload erstellen (jetzt mit device_list_str statt device_list)
    payload = {
        "system_instruction": {
            "parts": [{
                "text": SYSTEM_PROMPT_TEMPLATE.format(
                    time_str=now_str,
                    people_locations=people_locs,
                    devices=device_list_str,
                    habits_summary=routine.tracker.get_habits_summary(),
                    wakeup_status=state.WAKEUP_REASON + (" (AUTONOM)" if state.WAKEUP_REASON != "Initial Start" else ""),
                    wakeup_count=state.WAKEUP_COUNT,
                    planned_wakeup=f"{datetime.datetime.fromtimestamp(state.NEXT_WAKEUP).strftime('%H:%M')} Uhr ({state.WAKEUP_REASON})" if state.NEXT_WAKEUP else "Nicht geplant"
                )
            }]
        },
        "contents": list(CONVERSATION_HISTORY),
        "tools": [{"function_declarations": FUNCTION_DECLARATIONS}],
        "safetySettings": SAFETY_SETTINGS,
        "generationConfig": {
            "thinkingConfig": {
                "thinkingLevel": "HIGH",
                "includeThoughts": True
            }
        }
    }

    total_input_tokens = 0
    total_output_tokens = 0
    
    # Prices for Gemini 2.5 Flash (as of Feb 2026, USD)
    # Input: $0.30 / 1M tokens
    # Output: $2.50 / 1M tokens
    PRICE_PER_M_INPUT = 0.30
    PRICE_PER_M_OUTPUT = 2.50

    try:
        # --- AGENTIC LOOP ---
        MAX_STEPS = 10 
        step_count = 0

        while step_count < MAX_STEPS:
            if cancel_event and cancel_event.is_set():
                print(" [Slow Brain] 🛑 Backend task was cancelled (Ghost check).")
                return ""

            payload['contents'] = list(CONVERSATION_HISTORY)
            
            resp = session.post(get_gemini_url(), json=payload, timeout=40)
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
                    if cancel_event and cancel_event.is_set():
                        print(" [Slow Brain] 🛑 Backend task was cancelled during tool execution.")
                        return ""

                    fn = call['functionCall']
                    try:
                        res = execute_tool(fn['name'], fn.get('args', {}), silent_mode=silent_mode)
                    except Exception as tool_err:
                        res = f"Error: {tool_err}"
                    tool_responses.append({
                        "functionResponse": {
                            "name": fn['name'], "response": {"result": str(res)}
                        }
                    })
                
                with HISTORY_LOCK:
                    CONVERSATION_HISTORY.append({"role": "model", "parts": _strip_thought_signature(parts_list)})
                    CONVERSATION_HISTORY.append({"role": "function", "parts": tool_responses})
                
                step_count += 1
                continue 
            
            else:
                cost_usd = (total_input_tokens / 1_000_000 * PRICE_PER_M_INPUT) + \
                           (total_output_tokens / 1_000_000 * PRICE_PER_M_OUTPUT)
                cost_eur = cost_usd * 0.95 # Rough USD to EUR conversion
                
                # Log only the FINAL request payload that produced the user-visible answer
                #try:
                #    print("\n[Slow Brain] Final Gemini request payload:")
                #    print(json.dumps(payload, indent=2, ensure_ascii=False))
                #except Exception as log_err:
                #    print(f"[LLM] Could not log Gemini payload: {log_err}")

                print(f"💰 KOSTEN CHECK (Schritte: {step_count+1}), Kosten: ~{cost_eur:.6f} €")

                text = "".join(final_text_parts)

                # SICHERHEITSNETZ: Entfernt Markdown-Reste, falls das LLM nicht hört
                text = text.replace("*", "").replace("#", "").replace("`", "")

                with HISTORY_LOCK:
                    CONVERSATION_HISTORY.append({"role": "model", "parts": _strip_thought_signature(parts_list)})
                return text

        return "Abbruch: Zu komplex."

    except Exception as e:
        print(f" [LLM Loop Error] {e}")
        return "Systemfehler."