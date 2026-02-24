import asyncio
import queue
import pyaudio
import traceback
import base64
import time
from google import genai
from google.genai import types

from jarvis import config, state
from jarvis.core import llm
from jarvis.services import memory
from aiy.leds import Color
from jarvis.core.tools import execute_tool, FUNCTION_DECLARATIONS

# Rough pricing for Gemini 2.5 Flash Live (Fast Brain), as of Feb 2026 (USD)
# Uses the same token pricing as standard Gemini 2.5 Flash.
LIVE_PRICE_PER_M_INPUT = 0.30
LIVE_PRICE_PER_M_OUTPUT = 2.50

# --- 1. The Tool Declaration (Fast Brain) ---
# We reuse the declarations from llm.py for simple local tools
FAST_TOOLS = [
    "control_device", "control_media", "get_device_state", "set_system_volume", "manage_timer_alarm",
    "save_memory", "retrieve_memory",
    "schedule_wakeup", "schedule_conditional_wakeup", "delete_wakeup_automation",
    "perform_google_search", "search_google_maps", "get_weather_forecast",
    "manage_shopping_list", "plan_outdoor_route", "get_ha_history", "get_calendar_events", "send_to_phone", "restart_service",
    "end_conversation"
]

def build_live_api_tools():
    declarations = []
    
    # Delegate Tool (Slow Brain)
    declarations.append(
        types.FunctionDeclaration(
            name="delegate_to_backend",
            description="DELEGATE COMPLEX OR MULTI-STEP REQUESTS: MUST be called for ANY task that requires multiple steps, logic, reasoning, python code execution, or deep analysis. The Live API is ONLY for single-step, immediate actions. If the user asks a complex question, wants to run a routine ('Gute Nacht'), or requests anything that isn't a simple read/write to an existing tool, you MUST delegate it here.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "user_intent": types.Schema(
                        type="STRING",
                        description="The exact, full transcription of what the user just said or asked."
                    )
                },
                required=["user_intent"]
            ),
            behavior="NON_BLOCKING"
        )
    )
    
    def dict_to_schema(d):
        if not isinstance(d, dict):
            return d
        kwargs = {}
        if "type" in d: kwargs["type"] = d["type"]
        if "description" in d: kwargs["description"] = d["description"]
        if "enum" in d: kwargs["enum"] = d["enum"]
        if "required" in d: kwargs["required"] = d["required"]
        if "properties" in d:
            kwargs["properties"] = {k: dict_to_schema(v) for k, v in d["properties"].items()}
        if "items" in d:
            kwargs["items"] = dict_to_schema(d["items"])
        return types.Schema(**kwargs)

    for f in FUNCTION_DECLARATIONS:
        if f["name"] in FAST_TOOLS:
            declarations.append(
                types.FunctionDeclaration(
                    name=f["name"],
                    description=f.get("description", ""),
                    parameters=dict_to_schema(f.get("parameters"))
                )
            )
            
    return types.Tool(function_declarations=declarations)

live_api_tools = build_live_api_tools()

def get_dynamic_system_instruction(time_since_last_activity):
    # Load Core Memory & Routines from Mem0 via memory service
    core_content = ""
    try:
        from jarvis.services import memory
        # get_hybrid_context now returns precisely the formatted paul_core strings
        core_content = memory.get_hybrid_context(None)
    except Exception as e:
        print(f" [Fast Brain] Could not load Core Memory from Mem0: {e}")

    if getattr(state, 'HA_CONTEXT', None):
        device_lines = []
        for dev in state.HA_CONTEXT:
            info = f"- {dev['name']} [ID: {dev['entity_id']}] ({dev['state']})"
            device_lines.append(info)
        device_list_str = "\n".join(device_lines)
    elif state.AVAILABLE_LIGHTS:
        lines = []
        for name, eid in state.AVAILABLE_LIGHTS.items():
            lines.append(f"- {name} [ID: {eid}]")
        device_list_str = "\n".join(lines)
    else:
        device_list_str = "Keine Geräte gefunden."

    text = f'''Du bist Jarvis, das superschnelle Fast Brain des Smart Homes.
Antworte immer in der Sprache des Nutzers (meistens Deutsch). Antworte als Peer, kurz und prägnant.

=== CORE MEMORY (DEIN WISSEN ÜBER DEN USER) ===
{core_content}

=== VERFÜGBARE GERÄTE (STATE) ===
{device_list_str}
'''

    # --- Fast Brain Short-Term Memory Injection ---
    # We only inject history if the last interaction was within 15 minutes.
    if time_since_last_activity <= 900 and getattr(state, 'CONVERSATION_HISTORY', None):
        history_lines = []
        # Take the last 6 turns (approx 3 user/assistant pairs) to avoid huge prompts
        import itertools
        # Safely slice the deque
        recent_history = list(itertools.islice(state.CONVERSATION_HISTORY, max(0, len(state.CONVERSATION_HISTORY) - 6), len(state.CONVERSATION_HISTORY)))
        for turn in recent_history:
            role = "Jarvis" if turn.get("role") == "model" else "Paul"
            # Attempt to extract text content, fallback to str representation
            parts = turn.get("parts", [])
            if isinstance(parts, list) and len(parts) > 0:
                try:
                    content = parts[0].get("text", str(parts[0]))
                except AttributeError:
                     content = str(parts[0])
            else:
                 content = str(parts)
            history_lines.append(f"{role}: {content}")
            
        if history_lines:
            text += "\n=== RECENT CONVERSATION (LETZTE 15 MIN) ===\n"
            text += "(Beziehe dich darauf, falls der User etwas aus dem direkten Kontext fragt)\n"
            text += "\n".join(history_lines) + "\n"

    text += '''
=== CRITICAL RULES (FAST BRAIN VS SLOW BRAIN) ===
Du bist für EINFACHE, SOFORTIGE Aktionen zuständig.
1. FAST BRAIN AUFGABEN:
   - Geräte schalten (control_device, control_media) MUSS mit entity_id aufgerufen werden!
   - Wettervorhersage abrufen (get_weather_forecast)
   - Termine lesen (get_calendar_events)
   - Timer/Wecker setzen (manage_timer_alarm)
   - Erinnerungen speichern (save_memory)
   - Erinnerungen suchen (retrieve_memory) - WICHTIG: Nutze dies, wenn der User nach Dingen fragt, die in der Vergangenheit liegen!
2. SLOW BRAIN (DELEGATE):
   - Wenn eine Aufgabe MEHRERE SCHRITTE erfordert (z.B. "Recherchiere X und fasse zusammen", oder "Starte meine Gute Nacht Routine").
   - Wenn Python-Code ausgeführt werden muss (Berechnungen, Deep Web-Scraping).
   - In diesen Fällen rufst du ZWINGEND "delegate_to_backend" auf. Versuche NIEMALS komplexe Routinen selbst auszuführen!
3. KOMMUNIKATION:
   - Nach erfolgreichem Toolaufruf antworte ultrakurz (z.B. "Ok", "Erledigt", "Licht ist an").
   - Wenn "delegate_to_backend" antwortet, lies die exakte Antwort flüssig vor, füge KEINE Floskeln hinzu.'''

    return types.Content(role="system", parts=[types.Part.from_text(text=text)])

class JarvisHybridRouter:
    def __init__(self, leds, audio_queue):
        self.leds = leds
        self.audio_queue = audio_queue
        self._active_backend_task = None
        self.is_playing_audio = False
        self.last_audio_end_time = 0.0
        # Tracks the last time there was any interaction on the Live websocket
        # (user audio sent OR model/tool response received). Used for idle timeout.
        self.last_activity_time = time.time()

        # Accumulated token usage for the Live API (Fast Brain) session.
        self.live_input_tokens = 0
        self.live_output_tokens = 0
        
        # Explicitly pass the API key from config since it's not set in the environment by default
        # config.GEMINI_KEYS is a list of keys, we use the first one for the Live API
        api_key = config.GEMINI_KEYS[0] if hasattr(config, 'GEMINI_KEYS') and config.GEMINI_KEYS else None
        self.client = genai.Client(http_options={"api_version": "v1alpha"}, api_key=api_key) 
        
        # Audio output stream (Gemini Live API TTS outputs 24kHz PCM by default)
        self.pa = pyaudio.PyAudio()
        self.out_stream = self.pa.open(
            format=pyaudio.paInt16,
            channels=1,
            rate=24000,
            output=True
        )
        
        self.should_close = False
        self.close_after_turn = False
        self._current_user_transcript = []
        self._waiting_for_tts_completion = False
        # Maximum idle time (in seconds) for a Live API Fast Brain session.
        # If there is no interaction between user and model (no audio sent, no
        # server responses) for this duration, the session is auto-closed.
        self.session_timeout_seconds = 7   

    async def _handle_local_tool(self, session, tool_call):
        """Executes a simple HA tool directly and returns the response to the Live API immediately."""
        try:
            call_id = tool_call.id
            name = tool_call.name
            
            args = tool_call.args
            if hasattr(args, '__dict__'):
                args_dict = vars(args)
            elif isinstance(args, dict):
                args_dict = args
            elif hasattr(args, 'items'):
                args_dict = dict(args.items())
            else:
                args_dict = dict(args)
            
            # Filter any internal fields
            args_dict = {k: v for k, v in args_dict.items() if not k.startswith('_')}
            
            loop = asyncio.get_running_loop()
            print(f" [Fast Brain] Executing Local Tool: {name} with args {args_dict}")
            
            # Execute tool synchronously in executor
            result = await loop.run_in_executor(None, execute_tool, name, args_dict, True)
            
            # Send result back
            function_response = types.FunctionResponse(
                id=call_id,
                name=name,
                response={
                    "result": str(result),
                    "scheduling": "INTERRUPT" 
                }
            )
            await session.send_tool_response(function_responses=[function_response])
            
            # Close only if the backend reasoning hasn't also been triggered
            active_task = self._active_backend_task
            if active_task and not active_task.done():
                print(" [Fast Brain] Handed local result back to Fast Brain. Backend task is active, keeping session open.")
            else:
                print(" [Fast Brain] Handed local result back to Fast Brain. Awaiting TTS response.")
                self._waiting_for_tts_completion = True
                self._active_backend_task = None
        except Exception as e:
            print(f"[Router Error] Could not execute local tool: {e}")

    async def _handle_slow_brain_tool(self, session, tool_call):
        """Catches the tool call and routes it to the heavy agentic backend in a thread."""
        try:
            args = tool_call.args
            user_intent = args.get("user_intent", "")
            call_id = tool_call.id
            
            # Cancel any existing stale backend task (prevent ghost duplicates)
            if self._active_backend_task and not self._active_backend_task.done():
                self._active_backend_task.cancel()
                
            self._active_backend_task = asyncio.create_task(
                 self._run_backend_reasoning(session, user_intent, call_id)
            )
        except Exception as e:
            print(f"[Router Error] Could not delegate to backend: {e}")

    async def _run_backend_reasoning(self, session, user_intent: str, call_id: str):
        """The Slow Brain loop executing your existing agentic workflow non-blockingly."""
        try:
            print(f" [Slow Brain] Processing Intent: {user_intent}")
            
            # Show "thinking" color
            self.leds.update(self.leds.rgb_pattern(config.DIM_PURPLE))
            
            # Wrap standard sync logic
            hybrid_context = memory.get_hybrid_context(user_intent)
            final_prompt = f"{hybrid_context}\n\nUSER AUDIO TRANSCRIPT:\n{user_intent}\n\n(Antworte dem User.)"
            
            # Run blocking task in executor so the websocket loop stays alive
            loop = asyncio.get_running_loop()
            response_text = await loop.run_in_executor(
                None, 
                llm.ask_gemini, 
                self.leds, 
                final_prompt, 
                None,  # WAV data not needed, Live API already transcribed it
                True   # silent_mode=True to prevent duplicate TTS in llm.py
            )
            
            if "<SESSION:CLOSE>" in response_text:
                 self.close_after_turn = True
            
            # Save final interactions
            clean_resp = response_text.replace("<SESSION:KEEP>", "").replace("<SESSION:CLOSE>", "").strip()
            if "<SILENT>" in clean_resp:
                clean_resp = clean_resp.replace("<SILENT>", "").strip()

            memory.save_interaction(user_intent, clean_resp)
            
            if not clean_resp:
                clean_resp = "Erledigt."

            # Format the output as a literal FunctionResponse for the new SDK
            function_response = types.FunctionResponse(
                id=call_id,
                name="delegate_to_backend",
                response={
                    "result": clean_resp,
                    "scheduling": "INTERRUPT" 
                }
            )
            
            # Set to Active (Blue) before sending the TTS payload back
            self.leds.update(Color.BLUE)
            
            self._waiting_for_tts_completion = True
            await session.send_tool_response(function_responses=[function_response])
            print(" [Slow Brain] Handed result back to Fast Brain.")
             
        except asyncio.CancelledError:
             print(" [Slow Brain] 🛑 Cancelled logic early due to user interruption.")
             raise
        except Exception as e:
             traceback.print_exc()

    async def _mic_send_loop(self, session):
        """Pulls audio from the existing multiprocessing.Queue and pushed to websocket."""
        try:
            print(" [Fast Brain] Mic Send Loop Started", flush=True)
            while True:  # KEEP RUNNING to prevent FIRST_COMPLETED from killing the receive loop early
                await asyncio.sleep(0.01) # Yield to event loop
                
                if state.CANCEL_REQUESTED:
                    print(" [Fast Brain] Button interrupt detected! Closing session immediately.", flush=True)
                    state.CANCEL_REQUESTED = False
                    self.should_close = True
                    break
                
                # Drain queue non-blocking
                chunks = []
                while not self.audio_queue.empty():
                    try:
                        chunks.append(self.audio_queue.get_nowait())
                    except queue.Empty:
                        break
                
                if self.should_close:
                    # Session closing, clear queue
                    continue
                
                if chunks:
                    pcm_data = b''.join(chunks)
                    
                    # If we're currently playing audio from the model, or if we just finished within the last 0.6 seconds, duck the mic input to prevent feedback loops and overwhelming the user with loud audio.
                    if self.is_playing_audio or (time.time() - self.last_audio_end_time < 0.6):
                        try:
                            import audioop
                            # Dämpfe das Signal auf 10% 
                            pcm_data = audioop.mul(pcm_data, 2, 0.1)
                            # print(f" [Fast Brain] Mic Ducked ({len(pcm_data)} bytes)", flush=True)
                        except Exception as e:
                            print(f"[Fast Brain] Ducking failed: {e}")
                            
                    # print(f" [Fast Brain] Sent {len(pcm_data)} bytes of audio", flush=True)
                    # For Python SDK we must send exactly types.Blob directly rather than JSON dicts
                    try:
                        await session.send_realtime_input(audio=types.Blob(data=pcm_data, mime_type=f"audio/pcm;rate={config.RATE}"))
                    except Exception as e:
                        if e.__class__.__name__ == 'APIError' and '1000' in str(e):
                            pass
                        elif e.__class__.__name__ == 'ConnectionClosedOK':
                            pass
                        else:
                            print(f" [Fast Brain] Error sending audio chunk: {e}", flush=True)
        except asyncio.CancelledError:
            print(" [Fast Brain] Mic Send Loop Cancelled", flush=True)
        except Exception as e:
            if e.__class__.__name__ == 'APIError' and '1000' in str(e):
                pass
            elif e.__class__.__name__ == 'ConnectionClosedOK':
                pass
            else:
                print(f" [Fast Brain] Mic Send Loop Error: {e}", flush=True)
                traceback.print_exc()

    async def _receive_loop(self, session):
        """Listens for AI server events (audio, tool calls, interruptions)."""
        loop = asyncio.get_running_loop()
        try:
            print(" [Fast Brain] Receive Loop Started", flush=True)
            while True:
                turn = session.receive()
                async for response in turn:
                    # Any response from the server (tool call, thoughts, audio, etc.)
                    # counts as activity and should reset the idle timer.
                    self.last_activity_time = time.time()

                    # Track token usage from the Live API (if provided by the SDK).
                    usage = getattr(response, "usage_metadata", None) or getattr(response, "usageMetadata", None)
                    if usage is not None:
                        # Prefer snake_case attributes; fall back to camelCase if needed.
                        in_tokens = getattr(usage, "prompt_token_count", None)
                        if in_tokens is None:
                            in_tokens = getattr(usage, "promptTokenCount", 0)
                        out_tokens = getattr(usage, "candidates_token_count", None)
                        if out_tokens is None:
                            out_tokens = getattr(usage, "candidatesTokenCount", 0)
                        try:
                            self.live_input_tokens += int(in_tokens or 0)
                            self.live_output_tokens += int(out_tokens or 0)
                        except Exception:
                            # Don't let logging issues break the session.
                            pass

                    server_content = response.server_content
                    if not server_content:
                        # Could be tool calls
                        if response.tool_call:
                            for call in response.tool_call.function_calls:
                                print(f" [Fast Brain] Tool Call: {call.name}", flush=True)
                                if call.name == "delegate_to_backend":
                                    await self._handle_slow_brain_tool(session, call)
                                else:
                                    await self._handle_local_tool(session, call)
                        continue
                    
                    # The Interruption Kill Switch
                    if server_content.interrupted:
                        print(" [Fast Brain] 🛑 User Interrupted!")
                        if self._active_backend_task and not self._active_backend_task.done():
                            print(" [Fast Brain] Killing Slow Brain task...")
                            self._active_backend_task.cancel()
                            self._active_backend_task = None
                        continue
                    
                    # 1. Capture user's input transcription if available
                    if hasattr(server_content, "input_transcription") and server_content.input_transcription:
                        if server_content.input_transcription.text:
                            # We might get partial transcripts, we append them locally and dump them on model_turn
                            if not hasattr(self, "_current_user_transcript"):
                                self._current_user_transcript = []
                            self._current_user_transcript.append(server_content.input_transcription.text)

                    # 2. Extract text content from the model to build conversation history
                    if server_content.model_turn:
                        self._waiting_for_tts_completion = False
                        model_text_parts = []
                        for part in server_content.model_turn.parts:
                            if part.text:
                                model_text_parts.append(part.text)
                            # Play audio bytes received from the model TTS
                            # Use isinstance for bytes check as per newer docs
                            if part.inline_data and isinstance(part.inline_data.data, bytes):
                                self.is_playing_audio = True
                                try:
                                    await loop.run_in_executor(None, self.out_stream.write, part.inline_data.data)
                                finally:
                                    self.is_playing_audio = False
                                    self.last_audio_end_time = time.time()
                                    
                        # Append the Fast Brain's response to the global history
                        if model_text_parts:
                            full_text = "".join(model_text_parts)
                            user_text = "[Spracheingabe]"
                            # If we captured actual transcriptions, use them instead of the placeholder
                            if hasattr(self, "_current_user_transcript") and self._current_user_transcript:
                                user_text = " ".join(self._current_user_transcript).strip()
                                self._current_user_transcript = [] # reset for next turn
                                
                            with state.HISTORY_LOCK:
                                state.CONVERSATION_HISTORY.append({"role": "user", "parts": [{"text": user_text}]})
                                state.CONVERSATION_HISTORY.append({"role": "model", "parts": [{"text": full_text}]})

                    # Check if turn is complete AND backend requested session close
                    if server_content.turn_complete:
                        self._waiting_for_tts_completion = False
                        if self.should_close or self.close_after_turn:
                            print(" [Fast Brain] Turn complete and Backend closed session. Disconnecting.")
                            self.should_close = True
                            return # Exit loop
                
                # If we broke out cleanly, check should_close
                if self.should_close:
                    break

                
        except asyncio.CancelledError:
            print(" [Fast Brain] Receive Loop Cancelled")
        except Exception as e:
            if e.__class__.__name__ == 'APIError' and '1000' in str(e):
                pass
            elif e.__class__.__name__ == 'ConnectionClosedOK':
                pass
            else:
                print(f" [Fast Brain] Receive Loop Error: {e}")
                traceback.print_exc()

    async def _session_timeout_watchdog(self, session):
        """Automatically closes the Live API session after a period of *inactivity*.

        If there is no interaction between user and model (no audio sent from the mic
        loop and no server responses received) for `session_timeout_seconds`, we
        proactively close the websocket session to save resources.
        """
        try:
            # Small polling loop so we can react to activity and to external closes.
            check_interval = 1.0
            while True:
                await asyncio.sleep(check_interval)
                if self.should_close:
                    # Another part of the system requested shutdown; just exit.
                    return

                # If the Slow Brain backend is currently running, we treat the
                # session as "active" regardless of mic/model activity so the
                # connection never times out while reasoning is in progress.
                if self._active_backend_task and not self._active_backend_task.done():
                    self.last_activity_time = time.time()
                    continue

                if self._waiting_for_tts_completion:
                    self.last_activity_time = time.time()
                    continue

                idle_for = time.time() - self.last_activity_time
                if idle_for >= self.session_timeout_seconds:
                    print(
                        f" [Fast Brain] Idle timeout reached "
                        f"({idle_for:.1f}s >= {self.session_timeout_seconds}s). Closing session.",
                        flush=True,
                    )
                    self.should_close = True
                    try:
                        await session.close()
                    except Exception as e:
                        print(f" [Fast Brain] Error closing session on idle timeout: {e}", flush=True)
                    return
        except asyncio.CancelledError:
            # Normal path when the session finishes earlier
            print(" [Fast Brain] Session timeout watchdog cancelled", flush=True)

    async def start_session(self):
        """Establishes the Live API session upon wake word detection."""
        self.should_close = False
        self.close_after_turn = False
        
        # Calculate time since last interaction
        time_since_last_activity = time.time() - self.last_activity_time
        
        # Reset Live API token accounting for this session.
        self.live_input_tokens = 0
        self.live_output_tokens = 0
        # Initialize last activity timestamp at session start so the idle timer
        # only kicks in after a period with no user/model interaction.
        self.last_activity_time = time.time()
        print("💡 LED: SOLID BLUE (Connecting to Live API...)", flush=True)
        self.leds.update(Color.BLUE)
        
        # Requesting transcripts in the Live API config using **kwargs since Pydantic types can be strict
        # The prompt instructed to pass dicts directly to config.
        # However, the SDK uses types.LiveConnectConfig, which might not explicitly accept these as kwargs.
        # Let's pass them dynamically to avoid validation errors if they are new in the API.
        live_config_args = {
            "response_modalities": ["AUDIO"],
            "enable_affective_dialog": True,
            "thinking_config": types.ThinkingConfig(thinking_budget=-1, include_thoughts=True),
            "system_instruction": get_dynamic_system_instruction(time_since_last_activity),
            "tools": [live_api_tools],
            "speech_config": types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Puck"
                    )
                )
            )
        }
        
        c = types.LiveConnectConfig(**live_config_args)
        
        # According to the Google docs, we can pass the config as a raw dict to avoid Pydantic issues
        raw_config = {
            "response_modalities": ["AUDIO"],
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }
        # merge the schema generated by the SDK with the raw dictionary fields we need just in case
        try:
             import json
             dumped = c.model_dump(exclude_none=True)
             dumped.update(raw_config)
             final_config = dumped
        except Exception:
             final_config = c # fallback if model_dump fails
             
        # Initialize an empty array to accumulate user transcript strings for this session
        self._current_user_transcript = []
        
        try:
            async with self.client.aio.live.connect(
                model="gemini-2.5-flash-native-audio-preview-12-2025", 
                config=final_config
            ) as session:
                print("✅ Live API Connected. Fast Brain Listening...", flush=True)
                
                send_task = asyncio.create_task(self._mic_send_loop(session))
                receive_task = asyncio.create_task(self._receive_loop(session))
                timeout_task = asyncio.create_task(self._session_timeout_watchdog(session))
                
                # Wait until either the send loop detects a close, the receive loop breaks,
                # or the timeout watchdog triggers.
                await asyncio.wait(
                    [send_task, receive_task, timeout_task], 
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # Cleanup tasks if they are still pending
                send_task.cancel()
                receive_task.cancel()
                timeout_task.cancel()
                
        except Exception as e:
            print(f"Connection Lost: {e}", flush=True)
            traceback.print_exc()
        finally:
            # Log approximate Live API cost for this Fast Brain session (if any tokens were used).
            try:
                if self.live_input_tokens or self.live_output_tokens:
                    cost_usd = (self.live_input_tokens / 1_000_000 * LIVE_PRICE_PER_M_INPUT) + \
                               (self.live_output_tokens / 1_000_000 * LIVE_PRICE_PER_M_OUTPUT)
                    cost_eur = cost_usd * 0.95  # Rough USD to EUR conversion
                    print(
                        f"💰 LIVE KOSTEN CHECK (Fast Brain): "
                        f"~{cost_eur:.6f} € "
                        f"(input: {self.live_input_tokens} tok, output: {self.live_output_tokens} tok)"
                    )
            except Exception as e:
                print(f" [Fast Brain] Could not log Live API cost: {e}", flush=True)

            print("💡 LED: OFF (Disconnected)", flush=True)
            self.leds.update(self.leds.rgb_off())
            
            if self._active_backend_task and not self._active_backend_task.done():
                self._active_backend_task.cancel()
            self._active_backend_task = None
                
            state.IS_PROCESSING = False
