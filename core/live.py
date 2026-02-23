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

def get_dynamic_system_instruction():
    # Load Core Memory directly (we don't need the RAG part here because there is no search query yet)
    core_content = ""
    try:
        import os
        from jarvis import config
        if os.path.exists(config.CORE_FILE):
            with open(config.CORE_FILE, 'r', encoding='utf-8') as f:
                core_content = f.read()
    except Exception as e:
        print(f" [Fast Brain] Could not load Core Memory: {e}")

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
        # Maximum duration for a Live API Fast Brain session (in seconds)
        # Helps auto-close sessions that were opened accidentally (e.g. false wake word)
        self.session_timeout_seconds = 15

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
                print(" [Fast Brain] Handed local result back to Fast Brain. Terminating session.")
                self.should_close = True
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
                 self.should_close = True
            
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
                
                if self.should_close or self.is_playing_audio or (time.time() - self.last_audio_end_time < 0.4):
                    # Clear the queue but DO NOT send to live API (Mute mic while TTS plays/closing to prevent echo loops)
                    continue
                
                if chunks:
                    pcm_data = b''.join(chunks)
                    # print(f" [Fast Brain] Sent {len(pcm_data)} bytes of audio", flush=True)
                    # For Python SDK we must send exactly types.Blob directly rather than JSON dicts
                    try:
                        await session.send_realtime_input(audio=types.Blob(data=pcm_data, mime_type=f"audio/pcm;rate={config.RATE}"))
                    except Exception as e:
                        print(f" [Fast Brain] Error sending audio chunk: {e}", flush=True)
        except asyncio.CancelledError:
            print(" [Fast Brain] Mic Send Loop Cancelled", flush=True)
        except Exception as e:
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
                    
                    # Play audio bytes received from the model TTS
                    if server_content.model_turn:
                        for part in server_content.model_turn.parts:
                            # Use isinstance for bytes check as per newer docs
                            if part.inline_data and isinstance(part.inline_data.data, bytes):
                                self.is_playing_audio = True
                                try:
                                    await loop.run_in_executor(None, self.out_stream.write, part.inline_data.data)
                                finally:
                                    self.is_playing_audio = False
                                    self.last_audio_end_time = time.time()

                    # Check if turn is complete AND backend requested session close
                    if server_content.turn_complete and self.should_close:
                         print(" [Fast Brain] Turn complete and Backend closed session. Disconnecting.")
                         return # Exit loop
                
                # If we broke out cleanly, check should_close
                if self.should_close:
                    break

                
        except asyncio.CancelledError:
            print(" [Fast Brain] Receive Loop Cancelled")
        except Exception as e:
            print(f" [Fast Brain] Receive Loop Error: {e}")
            traceback.print_exc()

    async def _session_timeout_watchdog(self, session):
        """Automatically closes the Live API session after a fixed timeout.

        This is primarily a safety net for accidental wake-word activations:
        if no other logic has requested a close within `session_timeout_seconds`,
        we proactively close the websocket session to save resources.
        """
        try:
            await asyncio.sleep(self.session_timeout_seconds)
            if not self.should_close:
                print(f" [Fast Brain] Session timeout reached ({self.session_timeout_seconds}s). Closing session.", flush=True)
                self.should_close = True
                try:
                    await session.close()
                except Exception as e:
                    print(f" [Fast Brain] Error closing session on timeout: {e}", flush=True)
        except asyncio.CancelledError:
            # Normal path when the session finishes earlier
            print(" [Fast Brain] Session timeout watchdog cancelled", flush=True)

    async def start_session(self):
        """Establishes the Live API session upon wake word detection."""
        self.should_close = False
        print("💡 LED: SOLID BLUE (Connecting to Live API...)", flush=True)
        self.leds.update(Color.BLUE)
        
        c = types.LiveConnectConfig(
             response_modalities=["AUDIO"],
             enable_affective_dialog=True,
             thinking_config=types.ThinkingConfig(thinking_budget=-1, include_thoughts=True),
             system_instruction=get_dynamic_system_instruction(),
             tools=[live_api_tools],
             speech_config=types.SpeechConfig(
                 voice_config=types.VoiceConfig(
                     prebuilt_voice_config=types.PrebuiltVoiceConfig(
                         voice_name="Puck" # Options: Puck, Charon, Kore, Fenrir, Aoede
                     )
                 )
             )
        )
        
        try:
            async with self.client.aio.live.connect(
                model="gemini-2.5-flash-native-audio-preview-12-2025", 
                config=c
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
            print("💡 LED: OFF (Disconnected)", flush=True)
            self.leds.update(self.leds.rgb_off())
            
            if self._active_backend_task and not self._active_backend_task.done():
                self._active_backend_task.cancel()
            self._active_backend_task = None
                
            state.IS_PROCESSING = False
