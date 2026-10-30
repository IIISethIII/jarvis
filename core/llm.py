import asyncio
import os
import threading
import time
import datetime
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

class GeminiLiveProvider:
    def __init__(self):
        # Support multiple keys for rotation & quota fallback
        keys_env = os.getenv("GEMINI_KEYS") or os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_KEY")
        if keys_env:
            self.api_keys = [k.strip() for k in keys_env.split(',') if k.strip()]
        else:
            raise ValueError("GEMINI_API_KEY / GEMINI_KEYS missing in environment.")
            
        self.current_key_index = 0
        self.client = genai.Client(api_key=self.api_keys[self.current_key_index])
        self.model_name = os.getenv("GEMINI_LIVE_MODEL", "gemini-3.1-flash-live-preview")
        self.session = None
        # Set from other threads (hardware button) to end the running session
        self.cancel_event = threading.Event()
        self.is_active = False

    def request_cancel(self):
        """Ends the running session (thread-safe). Does nothing when idle."""
        if self.is_active:
            self.cancel_event.set()

    def rotate_key(self):
        """Rotates to the next Gemini API key in the pool."""
        if len(self.api_keys) > 1:
            self.current_key_index = (self.current_key_index + 1) % len(self.api_keys)
            key_preview = self.api_keys[self.current_key_index][:8] + "..."
            print(f"[LLM] Rotated to API Key #{self.current_key_index + 1} ({key_preview})")
            self.client = genai.Client(api_key=self.api_keys[self.current_key_index])

    async def connect(self, audio_manager, hardware, memory_text="", dynamic_tools=None, mock_text=None, sfx=None):
        """Connects to the Live API and handles bidirectional audio streaming."""
        self.cancel_event.clear()
        self.is_active = True
        try:
            await self._connect(audio_manager, hardware, memory_text, dynamic_tools, mock_text, sfx)
        finally:
            if self.cancel_event.is_set():
                # Drop speech that arrived between the button press and the session closing
                audio_manager.stop_playback()
            self.is_active = False
            self.cancel_event.clear()

    async def _connect(self, audio_manager, hardware, memory_text, dynamic_tools, mock_text, sfx):
        now_dt = datetime.datetime.now()
        weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
        time_str = f"{weekdays[now_dt.weekday()]}, {now_dt.strftime('%d.%m.%Y %H:%M:%S')}"

        system_prompt = f"""Du bist JARVIS, der intelligente Sprachassistent und KI-Partner im Smart Home.
Antworte kurz und prägnant wie ein echter Gesprächspartner. Stelle niemals Rückfragen, es sei denn, ein Befehl kann ohne die Info technisch nicht ausgeführt werden. Wenn die Benutzereingabe unklar, verstümmelt oder nur Rauschen ist, antworte nicht und bleibe stumm.

=== PERSÖNLICHKEIT ===
- Freundlich und aufmerksam, aber nicht aufdringlich
- Du merkst dir Kontext (z.B. "Wieder Nudeln? Das war gestern auch schon dein Essen.")
- Motivierend bei Uni-Stress, entspannt am Wochenende
- Münchner Lokalkolorit: Kennst das Wetter, die S-Bahn-Probleme, Biergärten

=== AKTUELLE ZEIT ===
- Aktuelle Zeit: {time_str}

=== KOMMUNIKATIONSSTIL & SPRACHAUSGABE ===
- Casual aber respektvoll (Du-Form im Deutschen, casual conversational style in English). Antworte IMMER in der Sprache des Nutzers (meistens Deutsch oder Englisch). Wenn der User dich auf Englisch anspricht, antworte auf Englisch. Wenn er Deutsch spricht, antworte auf Deutsch.
- Bei technischen Problemen: ehrlich aber lösungsorientiert.
- Fange deine Antwort NIEMALS mit Floskeln an wie "Okay", "Mache ich", "Alles klar" oder "Erledigt". Liefere DIREKT die Fakten (z.B. "Das Wetter wird sonnig...").
- Nach erfolgreicher Ausführung von Steuerungs-Tools (z.B. Licht geschaltet) antworte ultrakurz (z.B. "Ok.", "Erledigt.", "Licht ist an.").
- Formatiere optimiert für Sprachwiedergabe (Audio Out):
  - VERBOTEN: Aufzählungszeichen (-, *, •), Nummerierungen (1.), Emojis, Sonderzeichen, Markdown.
  - Wenn du eine Liste vorliest, verbinde die Elemente mit "und" oder mache Pausen durch neue Sätze, statt Striche zu nutzen.
- Wenn der User nichts sagt oder die Eingabe nur Geräusche/Rauschen enthält, antworte nicht.

=== KONVERSATIONSSTEUERUNG ===
- Nutze das Tool 'end_conversation', wenn die Konversation beendet werden soll, z.B. wenn der User "Danke.", "Stop!", "Tschüss", "Das war's" sagt oder andeutet, dass das Gespräch vorbei ist.
- WICHTIG: Stelle NIEMALS künstliche Rückfragen wie "Noch etwas?" oder "Kann ich noch etwas für dich tun?" – das wirkt künstlich. Die Session bleibt einfach offen, falls Paul noch etwas sagen will, oder wird bei klarem Abschluss beendet.

=== REGELN FÜR SMART HOME & TOOL-NUTZUNG ===
- Du steuerst Geräte und Services im Smart Home über das Tool 'call_ha_service' (mit domain, service, entity_id und optionalem extra_json).
- 1. Wenn du Geräte steuerst oder Services aufrufst, musst du ZWINGEND die exakte 'entity_id' verwenden!
- 2. Die exakte ID und der aktuelle Status stehen unten unter der Sektion "VERFÜGBARE SMART HOME GERÄTE".
- 3. Nutze NIEMALS den Anzeigenamen (Friendly Name, z.B. "Wohnzimmer Decke") als Parameter für entity_id, sondern IMMER die ID ("light.wohnzimmer_decke").
- 4. Wenn du "Licht an" hörst, suche die passenden IDs raus und steuere sie (z.B. domain='light', service='turn_on', entity_id='...'). Ohne spezifischen Lampen-Namen -> alles an/aus im Raum oder Haus basierend auf dem Kontext.
- 5. Wenn der User fragt "Ist das Licht an?", schau direkt in deine Geräteliste. Die Zustände sind in Echtzeit aktuell.
- 6. WICHTIG: Kündige die Nutzung von Tools NIEMALS vorher an (z.B. nicht: "Ich schaue nach...", "Ich werde schalten...", "Einen Moment..."). Wenn Informationen fehlen oder eine Aktion nötig ist, rufe das Tool STILLSCHWEIGEND und SOFORT auf. Generiere erst dann eine kurze gesprochene Antwort, wenn du das Ergebnis hast.
- 7. Wenn der User eine Aktion wünscht, musst du ZUERST die entsprechende Funktion aufrufen. Antworte niemals nur mit Text, wenn eine Aktion erforderlich ist.
- 8. Der Sleep Button am Yamaha Receiver funktioniert wie folgt: 120 min -> 90 min -> 60 min -> 30 min -> AUS. Wenn bereits eine Zeit eingestellt ist, wird ein zusätzlicher Druck benötigt zum switchen.

=== REGELN FÜR LAUTSTÄRKE & MUSIK ===
- 1. Wenn der User "lauter/leiser" sagt -> Steuere die Musikwiedergabe (via call_ha_service am Media Player oder hifiberry MCP).
- 2. NUR wenn der User explizit "Systemlautstärke", "Deine Lautstärke" oder "Lautsprecher" sagt -> Nutze 'set_speaker_volume' (ändert nur die Systemlautstärke von Jarvis am Pi).
- 3. Wenn der User nur "Musik" sagt -> nutze category='station', name='Library Radio' und nutze ausschließlich Plexamp / Hifiberry.

=== REGELN FÜR KALENDER, WEBSUCHE & EXTRA TOOLS (MCP) ===
- Kalender: Nutze Kalender-Tools für Abfragen. Erstelle neue Termine NUR, wenn der User explizit einen neuen Termin erstellen will.
- Kalender: Lies NIEMALS die rohe Liste vor. Fasse die Termine in natürlicher Sprache zusammen.
- Websuche: Wenn dir aktuelle Informationen oder Fakten fehlen, nutze Websuche- oder Karten-Tools (MCP) stillschweigend im Hintergrund.

=== REGELN FÜR DAS GEDÄCHTNIS (CORE & ARCHIV) ===
- Du erhältst dein Wissen über den User (Identität, Fakten, Vorlieben, Routinen) direkt unten im Prompt unter "WICHTIGE FAKTEN ÜBER DEN BENUTZER (CORE MEMORY)".
- WICHTIG: Das Core Memory ist deine absolute Wahrheit. Nutze diese Infos direkt, ohne Tools aufzurufen.
- SPEICHERN: Nutze das Tool 'edit_core_memory' um neue permanente Fakten oder Routinen zu speichern oder bestehende zu ersetzen (z.B. wenn der User sagt "Merk dir dauerhaft X", "Speichere, dass ich Y mag").
- ARCHIV-SUCHE (WICHTIG): Vergangene chronologische Konversationen stehen NICHT im Prompt. Alle Chat-Logs werden automatisch im Hintergrund archiviert und mit Zeitstempeln versehen (z.B. "[2026-08-01 14:00]").
- Wenn der User sich auf etwas Vergangenes bezieht ("Was haben wir gestern besprochen?", "Wie hieß noch gleich...", "Was gab es heute zum Frühstück?"), MUSST du proaktiv und VOR DEINER ANTWORT das Tool 'search_archive' nutzen.
- Übergib an 'search_archive' eine kurze Suchphrase in normaler Sprache (z.B. "Frühstück" oder "Gespräch über den Urlaub"). Die Suche versteht auch die Bedeutung, nicht nur exakte Wörter. Für einen bestimmten Tag hänge das Datum als YYYY-MM-DD an (z.B. "Frühstück 2026-08-01"), rechne "gestern" anhand der aktuellen Zeit oben um. Bei keinem Treffer versuche es mit anderen Wörtern. Bei Fragen wie "Worüber haben wir eben/zuletzt geredet?" ohne Stichwort übergib "*", das liefert die letzten Einträge. Rate niemals, was in der Vergangenheit passiert ist, suche es!
\n"""
        if memory_text:
            system_prompt += f"WICHTIGE FAKTEN ÜBER DEN BENUTZER (CORE MEMORY):\n{memory_text}\n"

        from core import memory
        recent = memory.get_recent_conversations()
        if recent:
            system_prompt += (
                "\n=== LETZTE KONVERSATIONEN (gerade eben, chronologisch) ===\n"
                "Nutze das nur als Kontext, wenn der User daran anknüpft (z.B. \"und morgen?\", \"wie viele Einwohner hat sie?\"). "
                "Erwähne es nicht von selbst und wiederhole es nicht.\n"
                f"{recent}\n"
            )

        from core import tools
        from core.tools import ha_client
        
        try:
            # Fetch HA devices so Gemini knows the entity_ids
            all_states = ha_client.fetch_all_states()
            if all_states:
                devices = []
                for s in all_states:
                    domain = s['entity_id'].split('.')[0]
                    if domain in ['automation', 'script', 'zone', 'sun', 'update', 'button', 'device_tracker']:
                        continue # Skip noisy internal entities

                    name = s['attributes'].get('friendly_name', s['entity_id'])
                    state = s['state']
                    
                    # Extract attributes but exclude massive UI blobs
                    excluded_attrs = ['friendly_name', 'icon', 'entity_picture', 'supported_features', 'options', 'restored']
                    attrs = {k: v for k, v in s['attributes'].items() if k not in excluded_attrs}
                    
                    attr_str = f" | Attrs: {attrs}" if attrs else ""
                    devices.append(f"- {name} ({s['entity_id']}): {state}{attr_str}")

                context_str = "\n".join(devices)
                system_prompt += f"\nVERFÜGBARE SMART HOME GERÄTE:\n{context_str}\n"
                
            services = ha_client.fetch_all_services()
            if services:
                services_str = ""
                for domain in services:
                    d = domain['domain']
                    # Skip internal HA domains that aren't useful for smart home control
                    if d in ['homeassistant', 'persistent_notification', 'system_log', 'ffmpeg', 'profiler', 'recorder', 'logbook', 'shopping_list', 'tts']:
                        continue
                        
                    services_str += f"\nDomain: {d}\n"
                    for srv_name, srv_data in domain.get('services', {}).items():
                        fields = list(srv_data.get('fields', {}).keys())
                        services_str += f"  - {srv_name} (Params: {', '.join(fields)})\n"
                
                system_prompt += f"\nVERFÜGBARE SMART HOME SERVICES (CONTROLS):\n{services_str}\n"
        except Exception as e:
            print(f"[LLM] Failed to fetch HA context: {e}")
        
        all_tools = list(tools.FUNCTION_DECLARATIONS)
        if dynamic_tools:
            all_tools.extend(dynamic_tools)
            
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            
            system_instruction=types.Content(parts=[types.Part.from_text(text=system_prompt)]),
            tools=[types.Tool(function_declarations=all_tools)],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Zubenelgenubi"
                    )
                )
            )
        )

        # user_parts / jarvis_parts collect the transcript fragments of the current turn (see _flush_turn)
        state = {
            'last_activity': time.time(),
            'is_closed': False,
            'user_parts': [mock_text] if mock_text else [],
            'jarvis_parts': [],
            'session_id': now_dt.strftime("%Y%m%d-%H%M%S"),
        }

        async def _do_connect(client_instance):
            key_prev = self.api_keys[self.current_key_index][:8] + "..."
            print(f"[LLM] Connecting to Gemini Live API ({self.model_name}) using key #{self.current_key_index + 1} ({key_prev})...")
            async with client_instance.aio.live.connect(model=self.model_name, config=config) as session:
                self.session = session
                print("[LLM] Connected! Start speaking.")
                hardware.set_color("purple", blink=False) # Indicate active connection
                
                if mock_text:
                    print(f"[Debug] Injecting mock text: {mock_text}")
                    await session.send_client_content(
                        turns=[types.Content(role="user", parts=[types.Part.from_text(text=mock_text)])],
                        turn_complete=True
                    )
                
                state['last_activity'] = time.time()
                
                send_task = asyncio.create_task(self._send_audio_loop(audio_manager, session, state))
                receive_task = asyncio.create_task(self._receive_audio_loop(audio_manager, session, hardware, state, sfx))
                inactivity_task = asyncio.create_task(self._inactivity_loop(state, audio_manager))
                
                done, pending = await asyncio.wait(
                    [send_task, receive_task, inactivity_task],
                    return_when=asyncio.FIRST_COMPLETED
                )
                state['is_closed'] = True
                for task in pending:
                    task.cancel()
                
                # Gracefully await cancelled tasks
                await asyncio.gather(*pending, return_exceptions=True)
                
                print(f"[Debug] Connection loop ended. Done: {[t.get_name() for t in done]}")
                for t in done:
                    exc = t.exception()
                    if exc and not isinstance(exc, asyncio.CancelledError):
                        err_str = str(exc)
                        if any(code in err_str.lower() for code in ["1000", "closed", "connectionclosedok", "connection closed"]):
                            print(f"[LLM] Stream ended gracefully: {err_str}")
                        else:
                            print(f"[Task Error] {type(exc).__name__}: {exc}")

        # Attempt connection with automatic fallback across configured API keys
        max_attempts = len(self.api_keys)
        last_error = None

        for attempt in range(max_attempts):
            try:
                connect_task = asyncio.create_task(_do_connect(self.client))
                
                # Watchdog loop to catch deadlocks during connection closing
                while not connect_task.done():
                    await asyncio.sleep(0.2)
                    if self.cancel_event.is_set() and self.session is None:
                        # Cancelled while still connecting: there are no session tasks to end gracefully yet
                        print("\n[LLM] Cancelled before the session was established.")
                        state['is_closed'] = True
                        connect_task.cancel()
                        break
                    if self._idle_seconds(state, audio_manager) > 30.0:
                        print("\n[LLM Watchdog] 30s without activity (deadlock?). Force-cancelling connection!")
                        state['is_closed'] = True
                        connect_task.cancel()
                        break
                        
                try:
                    await connect_task
                except asyncio.CancelledError:
                    pass
                
                # Successful run, rotate key for subsequent turn to balance quotas
                self.rotate_key()
                return

            except asyncio.CancelledError:
                print("[LLM] Disconnected.")
                return
            except Exception as e:
                err_msg = str(e)
                print(f"[LLM] Error with API Key #{self.current_key_index + 1}: {err_msg}")
                last_error = e
                # Check for rate limit / quota / transient error to rotate
                if any(code in err_msg.lower() for code in ["429", "quota", "resource_exhausted", "unavailable", "internal"]):
                    print("[LLM] Quota or transient error detected. Trying fallback key...")
                    self.rotate_key()
                    await asyncio.sleep(0.5)
                    continue
                else:
                    # Non-retriable error
                    import traceback
                    traceback.print_exc()
                    break
            finally:
                hardware.turn_off()
                self.session = None

        if last_error:
            # Re-raise so caller is aware after trying all keys
            raise last_error

    @staticmethod
    def _idle_seconds(state, audio_manager):
        """Seconds of silence: counted from the end of playback, frozen while speech plays or a tool runs."""
        # Gemini streams audio faster than real time, so the last chunk arrives long before playback ends
        if audio_manager.is_busy() or state.get('tool_running', False):
            return 0.0
        return time.time() - max(state['last_activity'], audio_manager.last_play_time)

    async def _inactivity_loop(self, state, audio_manager):
        """Monitors activity and closes the connection after 15s of silence or when cancelled (button)."""
        while not state.get('is_closed', False):
            await asyncio.sleep(0.2)
            if self.cancel_event.is_set():
                print("\n[LLM] Session cancelled by button.", flush=True)
                return
            if self._idle_seconds(state, audio_manager) > 15.0:
                print("\n[LLM] 15 seconds of silence detected. Closing session to save resources...")
                return

    async def _send_audio_loop(self, audio_manager, session, state):
        """Reads chunks from the mic queue and sends them to Gemini."""
        import time
        import numpy as np
        last_print = time.time()
        try:
            while not state.get('is_closed', False):
                await asyncio.sleep(0.01)
                chunks = []
                while True:
                    c = audio_manager.get_audio_chunk(timeout=0)
                    if c is None:
                        break
                    chunks.append(c)
                        
                if chunks:
                    pcm_data = b''.join(chunks)
                    
                    audio_array = np.frombuffer(pcm_data, dtype=np.int16)
                    
                    # Apply digital gain (4x) to help Gemini hear far-field speech (3m away)
                    boosted_array = np.clip(audio_array.astype(np.float32) * 4.0, -32768, 32767).astype(np.int16)
                    pcm_data = boosted_array.tobytes()
                    
                    if len(boosted_array) > 0:
                        rms = np.sqrt(np.mean(np.square(boosted_array.astype(np.float32))))
                            
                    if time.time() - last_print > 2.0:
                        rms_norm = rms / 32768.0 if 'rms' in locals() else 0.0
                        print(f"  [Mic] Sending... (Active chunks: {len(chunks)}, Normalized RMS: {rms_norm:.4f})")
                        last_print = time.time()
                        
                    await session.send_realtime_input(
                        audio=types.Blob(data=pcm_data, mime_type="audio/pcm;rate=16000")
                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            err_msg = str(e)
            if any(term in err_msg.lower() for term in ["closed", "1000", "connectionclosed", "cancel"]):
                print(f"  [Mic] Send stream closed gracefully.")
            else:
                print(f"  [Mic Error] Send loop stopped: {e}")

    @staticmethod
    def _flush_turn(state):
        """Writes the collected transcript of the current turn to the archive and resets the buffers."""
        from core import memory
        user_text = "".join(state['user_parts']).strip()
        jarvis_text = "".join(state['jarvis_parts']).strip()
        state['user_parts'].clear()
        state['jarvis_parts'].clear()
        if not user_text and not jarvis_text:
            return
        # Fire-and-forget in a worker thread so the audio loop never waits on the SD card
        asyncio.get_running_loop().run_in_executor(None, memory.log_interaction, user_text, jarvis_text, state['session_id'])

    async def _receive_audio_loop(self, audio_manager, session, hardware, state, sfx):
        """Receives 24kHz audio from Gemini and plays it through the speaker."""
        from core import tools
        import time
        try:
            while not state.get('is_closed', False):
                async for response in session.receive():
                    # Check for server GoAway message
                    go_away = getattr(response, 'go_away', None)
                    if go_away is not None:
                        print(f"\n[LLM] Received GoAway from Gemini server: {go_away}")
                        return

                    server_content = getattr(response, 'server_content', None)
                    if server_content is not None:
                        interrupted = getattr(server_content, 'interrupted', None)
                        turn_complete = getattr(server_content, 'turn_complete', None)
                        
                        if interrupted:
                            self._flush_turn(state)
                            state['last_activity'] = time.time()
                            state['is_thinking'] = False
                            hardware.set_color("orange", blink=False)
                            audio_manager.stop_playback()

                        model_turn = getattr(server_content, 'model_turn', None)
                        if model_turn is not None:
                            state['last_activity'] = time.time()
                            hardware.set_color("blue", blink=False) # Solid blue = speaking
                            for part in model_turn.parts:
                                if getattr(part, 'thought', False):
                                    if getattr(part, 'text', None):
                                        print(f"\n[Gemini Thought] {part.text}", flush=True)
                                elif hasattr(part, 'inline_data') and part.inline_data:
                                    if state.get('is_thinking', False):
                                        audio_manager.stop_playback()
                                        state['is_thinking'] = False
                                    audio_manager.play_chunk(part.inline_data.data)
                                elif hasattr(part, 'text') and part.text:
                                    print(part.text, end="", flush=True)

                        output_transcription = getattr(server_content, 'output_transcription', None)
                        if output_transcription is not None and getattr(output_transcription, 'text', None):
                            state['last_activity'] = time.time()
                            state['jarvis_parts'].append(output_transcription.text)
                            print(f"\n[Gemini Voice Transcript] {output_transcription.text}", flush=True)

                        input_transcription = getattr(server_content, 'input_transcription', None)
                        if input_transcription is not None and getattr(input_transcription, 'text', None):
                            state['last_activity'] = time.time()
                            hardware.set_color("blue", blink=True) # Pulsing blue = transcribing/thinking
                            if state['jarvis_parts']:
                                self._flush_turn(state)  # new user speech after an answer: previous exchange is done
                            state['user_parts'].append(input_transcription.text)
                            print(f"\n[User Voice Transcript] {input_transcription.text}", flush=True)
                            state['is_thinking'] = True
                            if sfx:
                                sfx.play_thinking()

                        if turn_complete:
                            # A turn that ends in a tool call has no answer yet: keep the user text until the answer is complete
                            if state['jarvis_parts']:
                                self._flush_turn(state)
                            hardware.set_color("purple", blink=False) # Solid violet = waiting for input
                            print("") # Print newline after Jarvis finishes speaking
                            
                    tool_call = getattr(response, 'tool_call', None)
                    if tool_call is not None:
                        state['last_activity'] = time.time()
                        state['is_thinking'] = False
                        state['tool_running'] = True
                        print(f"[Debug] Received tool_call: {tool_call}")
                        hardware.set_color("blue", blink=True) # Pulsing blue = thinking/executing
                        if sfx:
                            sfx.play_thinking()
                        
                        function_responses = []
                        for func_call in tool_call.function_calls:
                            func_name = func_call.name
                            func_args = func_call.args if hasattr(func_call, 'args') else {}
                            
                            print(f"[LLM] Executing Tool: {func_name} with args {func_args}")
                            if func_name in tools.TOOL_IMPLEMENTATIONS:
                                result = await asyncio.to_thread(tools.execute_tool, func_name, func_args)
                                if result == "<END_CONVERSATION>":
                                    print("\n[LLM] Conversation ended by Gemini.", flush=True)
                                    if sfx:
                                        sfx.play("success")
                                    return # Exiting this loop terminates the connection
                            else:
                                from core.mcp import mcp_client
                                result = await mcp_client.execute(func_name, func_args)
                                print(f"  [DEBUG] MCP Tool Result: {result}")
                            
                            function_responses.append(
                                types.FunctionResponse(
                                    id=func_call.id,
                                    name=func_name,
                                    response={"result": result}
                                )
                            )
                        
                        if sfx:
                            sfx.play("success")
                        
                        print("[Debug] Sending tool responses back...")
                        await session.send_tool_response(function_responses=function_responses)
                        state['tool_running'] = False
                        state['last_activity'] = time.time()
                        hardware.set_color("blue", blink=True) # Back to speaking/waiting

        except asyncio.CancelledError:
            pass
        except Exception as e:
            err_msg = str(e)
            if any(term in err_msg.lower() for term in ["closed", "1000", "connectionclosed", "cancel"]):
                print(f"[LLM] Receive stream closed gracefully.")
            else:
                print(f"[Receive Loop Error] {e}")
        finally:
            # Session ended mid-turn (end_conversation, GoAway, inactivity): keep what was said so far
            self._flush_turn(state)
