# Jarvis V2 - Migration Guide (Blueprint)

## Architecture Overview

### Concept & Purpose
Jarvis V2 is an always-on, locally-hosted smart voice assistant designed to run on a Raspberry Pi (presumably with a Google AIY Voice Bonnet, given the `BonnetHardware` and `aiy.leds` imports). It acts as a bridge between the physical world (microphone, speaker, hardware button), the smart home (via Home Assistant), and advanced LLMs (specifically Google's Gemini).

A defining feature of this architecture is its **always-listening, event-driven main loop** combined with a **"Two-Brain" hybrid processing system** for handling user input.

### Core Architectural Components

1. **Audio Ingestion & Watchdog**
   - **Multiprocessing**: Audio from the microphone is continuously read in a separate daemon process (`audio_worker`) and pushed into a thread-safe queue. This prevents the main thread from blocking on I/O.
   - **Watchdog**: The main loop actively monitors this queue. If the mic goes silent (queue is empty for too long), it increments a fail counter and will eventually forcefully restart the audio subsystem or the entire `systemd` service to self-heal.

2. **Wake Word Engine (`openwakeword`)**
   - The main loop continuously feeds chunks of audio from the queue into an `openwakeword` model (`hey_jarvis`). Upon detecting a confidence score > 0.5, it interrupts background tasks, plays a wake sound, and opens a session.

3. **Hardware Interface (AIY Bonnet)**
   - **LED Proxy**: A wrapper (`LedProxy`) translates logical states into physical LED pulses/colors (e.g., listening, processing, alarms).
   - **Hardware Button**: Tied to an interrupt event (`on_button_press`). It serves as a physical cancel button to stop alarms, abort active LLM processing, or simply flash a random color when idle.
   - **Media Dipping**: Before listening or speaking, Jarvis reaches out to Home Assistant to lower the volume of background music (specifically a Hifiberry Plexamp), restoring it when done.

4. **The "Two-Brain Hybrid" LLM Router**
   - **Fast Brain (Audio Mode)**: When triggered by voice (the wake word), the system routes into the `JarvisHybridRouter`. This is an asynchronous, likely WebSocket-based streaming connection to Gemini Live, allowing for low-latency, native voice-in/voice-out conversation.
   - **Slow Brain (Text Mode)**: When triggered silently (via a Home Assistant text input, or an autonomous internal routine), it falls back to a traditional pipeline: Context Retrieval -> Text Prompt to `llm.ask_gemini` -> TTS via `google.speak_text`.

5. **Memory & State Engine (`state.py` & `memory`)**
   - Memory is categorized into Core facts (`core.md`), episodic daily logs (`episodic.md`), and vector embeddings (`Mem0`/LiteLLM) for semantic retrieval.
   - **Nightly Maintenance**: At 4:00 AM, the system triggers `memory.dream()` to process and compress the day's episodic memories in the background.

6. **External Integrations & MCP**
   - **Home Assistant (HA)**: deeply integrated. Jarvis fetches the full HA context on startup, runs a background thread to keep it updated, and monitors a specific text entity (`input_text.jarvis_chat`) to act as a "Mailbox" for remote commands.
   - **Octopus MCP Server**: On startup, Jarvis connects to a Model Context Protocol (MCP) server. It fetches dynamic tools and injects them into Gemini's function declarations (`FUNCTION_DECLARATIONS.extend(dynamic_tools)`), making the assistant infinitely extensible without hardcoding new Python tools.

### Primary Entry and Exit Points

- **Entry Point (`main.py -> main()`)**: 
  Initializes audio, background timers, wake word models, and connects to HA/MCP. It then enters an infinite `while True:` loop.
- **Main Loop Tick Priority**:
  1. **Autonomous Wakeups**: Checks if it's a new day or time to run a scheduled background routine.
  2. **Mic Watchdog**: Grabs the next audio chunk; increments fail counter if empty.
  3. **Mailbox Check**: Polls Home Assistant for remote text inputs or internal self-destructing triggers.
  4. **Active Session Execution**: If a session was opened via text or voice, it blocks to execute the "Text Brain" or "Fast Audio Brain", then cleans up the session.
  5. **Wake Word Detection**: Passes the latest audio chunk to `openwakeword`.
- **Exit Points**:
  - Catches `SIGTERM` (usually sent by `systemctl stop jarvis`) to gracefully turn off LEDs, restore music volume, and exit.
  - Hard exits via `system.restart_service()` if the microphone driver fails beyond recovery.

## State & Data Management

### Runtime State (`state.py` & `utils.py`)
- **In-Memory Blackboard**: `state.py` acts as a global namespace for cross-thread synchronization. It tracks flags like `IS_PROCESSING`, `CANCEL_REQUESTED`, active timers, alarm processes, and cached Home Assistant context (`HA_CONTEXT`, `AVAILABLE_LIGHTS`).
- **Session Management**: Controls interaction windows via `open_session(seconds)` and `SESSION_OPEN_UNTIL`. If the session is active, audio frames are routed to the LLM instead of the wake word engine.
- **Connection Pooling**: `utils.py` maintains a global `requests.Session()` with connection pooling. This drastically speeds up frequent HTTP requests (like talking to Home Assistant or external REST APIs).

### Persistent Data & Habits (`data/habits.json`)
- Stores dynamically tracked user behaviors and timestamps (e.g., `morning_start`, `bedtime`, `detected_patterns`).
- The `routine.tracker` (referenced by the memory system) uses this file to analyze when the user typically leaves for work, sleeps, or listens to music, forming the basis for Jarvis's autonomous context awareness.

### The Memory System (`services/memory.py` & Mem0)
The memory architecture is entirely backed by **Mem0**, a specialized vector memory layer utilizing ChromaDB and LiteLLM (configured with Gemini 2.5 Flash). It conceptually divides memory into two streams:

1. **Core Memory (`user_id="paul_core"`)**:
   - Represents the "BIOS" or permanent facts about the user.
   - **Retrieval**: Automatically fetched on *every* interaction via `get_hybrid_context()` and injected into the LLM system prompt.
   - **Updates**: Can be updated explicitly via the `save_memory_tool`, or implicitly during the nightly `dream()` cycle when the daily routine summary is injected. Mem0's internal LLM logic automatically decides whether to append new facts or overwrite outdated ones.

2. **Episodic Archive (`user_id="paul_archive"`)**:
   - The "Daily Log" of every interaction. 
   - **Storage**: The `save_interaction()` function runs in a background thread, saving the exact user query and assistant response prepended with a timestamp (e.g., `[2026-07-22 11:44] ...`). This prevents I/O operations from blocking the conversational flow.
   - **Retrieval**: This archive is *not* loaded into context automatically (to save tokens). Instead, the assistant has access to a `search_memory_tool` which performs a semantic vector search across the archive if the user asks something like "What did we talk about yesterday?".

3. **Nightly Dreaming (`dream()`)**:
   - At 4:00 AM, `main.py` triggers `memory.dream()`.
   - It runs `routine.tracker.analyze_routine()` to synthesize the events and behaviors of the past day based on smart home data and interactions.
   - The resulting insights are pushed into the `paul_core` Mem0 profile, effectively distilling short-term episodic observations into long-term semantic Core Memory.

## Core Engine & Orchestration (`core/`)

The core of Jarvis V2 handles the complex task of juggling real-time audio streaming, asynchronous WebSockets, synchronous heavy LLM reasoning, and dynamic tool execution. It achieves this through a threaded, asynchronous architecture that bridges two different Gemini API paradigms.

### 1. The Fast Brain (`core/live.py` - `JarvisHybridRouter`)
This is the real-time, low-latency conversational engine.
- **Architecture**: It uses the `google-genai` SDK to establish an asynchronous WebSocket connection to the Gemini Live API.
- **Execution**: When the wake word triggers in `main.py`, it blocks the main loop but executes `asyncio.run(router.start_session())` to run concurrently.
- **Concurrency**: Inside the session, it runs three massive concurrent async tasks:
  1. `_mic_send_loop`: Continuously pulls audio chunks from the `multiprocessing.Queue` and pushes them to the WebSocket.
  2. `_receive_loop`: Listens for Gemini's audio responses, text transcripts, and tool calls.
  3. **Audio Playback**: Streams the received 24kHz PCM audio directly to the speaker using `pyaudio`.
- **Tool Interception**: If Gemini calls a "local" tool (like turning on a light), the router intercepts it in `_handle_local_tool`, executes it instantly in the background, and returns the result to keep the audio conversation flowing smoothly without dropping the WebSocket.

### 2. The Slow Brain (`core/llm.py` - `ask_gemini`)
This is the heavy, agentic reasoning engine used when deep thought is required or when triggered silently by text/autonomous wakeups.
- **Agentic Loop**: It runs a synchronous `while` loop (up to 10 steps) using standard `requests.post` to the Gemini REST API.
- **Context Injection**: Before starting, it builds a massive system prompt dynamically injecting:
  - Live Home Assistant device states and metadata.
  - GPS locations of people in the household.
  - Routine habits and the "Wakeup Reason".
- **Thinking Mode**: It leverages Gemini's `thinkingConfig` to enable internal reasoning/Chain-of-Thought before emitting tool calls or text.
- **Delegation Bridge**: If the Fast Brain is active but encounters a complex intent, it calls a specific tool named `delegate_to_backend`. The Fast Brain then uses `loop.run_in_executor` to spawn the Slow Brain in a separate background thread. This allows the Slow Brain to "think" for 10-15 seconds while the Fast Brain keeps the WebSocket alive and can verbally bridge the gap (e.g., "Let me check that for you...").

### 3. Dynamic Tool Protocol (`core/mcp.py` & `core/tools.py`)
- **`tools.py`**: A static registry of local Python functions mapped to JSON schema declarations. The `execute_tool` router dispatches function calls here.
- **`mcp.py` (`MCPManager`)**: An advanced bridge to the open-source Model Context Protocol (MCP).
  - Runs its own isolated `asyncio` event loop in a daemon thread.
  - Maintains persistent Server-Sent Events (SSE) connections to external tool servers (e.g., Google Calendar, Maps, Search).
  - Provides thread-safe synchronous wrappers (`get_gemini_tools_sync`, `execute_sync`) so the Slow Brain can dynamically discover and execute tools hosted entirely outside the Python codebase.

## Services & Features (`services/`)

The `services/` directory contains all the modular, domain-specific capabilities that the core engine can access either programmatically or via LLM tool calls.

### Home Assistant Integration (`ha.py`)
This massive wrapper acts as the bridge between Jarvis and the smart home.
- **Purpose**: To fetch environmental context and execute actions on devices.
- **Inputs/Outputs**: Primarily communicates via REST API using a long-lived `HA_TOKEN`. It expects Home Assistant entity IDs and states.
- **Key Logic**: 
  - Automatically fetches states of lights, media players, and phones.
  - Handles "Media Dipping" (lowering background music volume when Jarvis is listening).
  - Can fetch Home Assistant history logs.
  - Dynamically creates and deletes Home Assistant Automations (used as a clever hack to trigger "Autonomous Wakeups" for Jarvis at specific times).

### Routine & Habit Tracking (`routine.py`)
- **Purpose**: To give Jarvis a sense of time and user behavior.
- **Logic**: Every night at 4:00 AM, `RoutineTracker.analyze_routine()` triggers. It fetches the last 48 hours of Home Assistant history (specifically targeting phone chargers, GPS locations, and media players). It resolves raw GPS coordinates to Point-of-Interest (POI) names using Google, builds a chronological timeline, and generates a summarized "Habits" report. This report is injected into the Core Memory to inform the LLM of the user's current schedule.

### Google APIs & Location (`google.py` & `navigation.py`)
- **Purpose**: Handle audio synthesis, text transcription, and spatial awareness.
- **Key Logic (`google.py`)**: Contains fallback `speak_text_gemini` functions (for the Slow Brain TTS), but its most interesting role is `resolve_location_name(address/coords)` which uses Google Maps APIs to convert raw GPS data from Home Assistant into readable places for the LLM (e.g., converting coordinates to "At Home" or "At TUM").
- **Key Logic (`navigation.py`)**: Uses routing APIs to calculate distances and generates custom `komoot` URLs so the assistant can send a bike route directly to the user's phone notification tray via Home Assistant.

### Hardware & OS Control (`hardware.py` & `system.py`)
- **Purpose**: Low-level Linux and device control.
- **`hardware.py`**: A wrapper (`BonnetHardware`) for the Google AIY Voice Bonnet. Manages the physical button interrupts and PWM signaling for the RGB LED (used to indicate thinking/listening/alarm states).
- **`system.py`**: Interacts with the Linux OS directly.
  - Scans `/proc/asound/cards` to dynamically find the bonnet soundcard and runs `amixer` commands to optimize microphone gain.
  - Handles the `restart_service()` command (killing and restarting the systemd service if the mic fails).
  - Contains a powerful `run_local_python` tool that allows the LLM to execute arbitrary Python code locally in a sandboxed `exec()` environment.

### Audio Feedback & Alarms (`sfx.py` & `timer.py`)
- **`sfx.py`**: Manages `.ogg` file playback for UI sounds (e.g., the wake chime, thinking loops, success pings) using an audio mixer.
- **`timer.py`**: A lightweight background thread (`background_timer_check`) that continuously checks `state.ACTIVE_TIMERS`. When a timestamp is reached, it triggers the alarm loop in `sfx.py` and flashes the LEDs red until the user presses the hardware button.

## External Integrations & Hardware

Jarvis V2 relies on a highly specific mix of local hardware components and cloud APIs. Understanding these dependencies is critical for a successful rewrite or migration to new hardware.

### Hardware Setup & Audio Quirks (`.asoundrc`)
- **Compute**: Assumed to be a Raspberry Pi (given the Linux ALSA environment and AIY Voice Bonnet).
- **Microphone Array**: A Sony PS3 Eye Camera.
  - **Quirk**: The ALSA config (`.asoundrc`) is heavily customized for this exact microphone. It uses a `dsnoop` plugin to buffer 4 channels at 48kHz (preventing I/O stuttering), downmixes them to mono via a `route` plugin, and resamples it to 16kHz for `openwakeword` and the Gemini Live API.
- **Speaker & LED Interface**: Google AIY Voice Bonnet.
  - Controlled by the `BonnetHardware` proxy class in Python.
  - **Quirk**: The ALSA config creates a software mixer (`dmix`) for the bonnet output. This is a critical configuration: it allows multiple Python threads (like `pygame.mixer` for SFX and `pyaudio` for the Gemini Live stream) to play sound simultaneously without throwing a "Device busy" Linux audio error.

### External Cloud APIs
1. **Google Gemini API (The Engine)**:
   - **Fast Brain (Voice)**: Uses the `google-genai` SDK (`v1alpha` endpoint) to open a WebSocket connection. It streams raw 16kHz PCM audio up and receives 24kHz PCM audio back.
   - **Slow Brain (Text/Agent)**: Uses standard REST `requests.post` to the `generativelanguage.googleapis.com` endpoint.
   - **Quirk (API Key Cycling)**: To bypass rate limits, `config.py` implements an `itertools.cycle` over a comma-separated list of multiple `GEMINI_KEYS`.
2. **Google Cloud TTS (Fallback Voice)**:
   - Used primarily by the "Slow Brain" when it needs to synthesize speech from text output.
   - Configured in `google.py` (and validated in `test_tts.py`) to use the specific `de-DE-Journey-D` voice, requesting `LINEAR16` audio encoding via the `texttospeech.googleapis.com` REST API.
3. **Home Assistant API**:
   - Communicates exclusively via standard REST using a Long-Lived Access Token (`HA_TOKEN`).
   - Acts as the primary orchestrator for the smart home environment, including custom scripts for "Media Dipping" (lowering the volume of the `hifiberry_plexamp_volume` sensor while Jarvis is listening).
4. **Model Context Protocol (MCP)**:
   - Jarvis expects an external MCP server running locally (`http://mcp.local`) to provide dynamic tools (like Calendar, Maps, Search) via Server-Sent Events (SSE). Without this, the assistant loses access to its dynamic toolset.

## Miscellaneous & Error Handling

To ensure the rewrite remains as robust and self-healing as the original Python implementation, pay special attention to these fallback patterns and lifecycle hooks present in the codebase:

### 1. The Microphone Watchdog (Self-Healing)
Audio dropouts are common on Linux when running long-lived streams. The `main.py` loop implements a strict watchdog:
- It waits a maximum of 3 seconds for audio (`audio_queue.get(timeout=3)`).
- If it times out, it kills and restarts the daemonized `audio_proc` sub-process.
- If it fails 5 times consecutively, it executes `system.restart_service()` to let `systemd` completely reboot the entire application environment.

### 2. Startup and Shutdown Hooks (`SIGTERM`)
- **Shutdown**: The system registers a signal handler for `SIGTERM` (which is sent when you run `sudo systemctl stop jarvis`). Inside `handle_sigterm`, it explicitly:
  1. Turns off the LEDs (`leds.update(leds.rgb_off())`) to prevent them from staying stuck on.
  2. Runs `restore_volume()` to ensure Home Assistant music isn't left permanently dipped or muted.
  3. Force exits cleanly with `os._exit(0)`.
- **Startup Fallbacks**: The application uses "graceful degradation". If the MCP server fails to connect on startup, it logs an error but continues running the main assistant without dynamic tools, ensuring basic voice functionality is always available.

### 3. Audio Echo Cancellation (The "Flush" Hack)
Because the microphone is physically close to the speaker, Jarvis can accidentally hear its own TTS output and trigger a feedback loop.
- **Quirk**: You will notice calls to `flush_queue(audio_queue, duration=2.5)` spread across `main.py`. The system intentionally drops/ignores all microphone input for exactly 2.5 seconds after speaking to let the room echo die down before listening again. 

### 4. Connection Resilience
- **MCP Reconnects**: The `MCPManager` catches specific exceptions (`TimeoutError`, `ConnectionClosed`) and explicitly tears down and respawns the `asyncio` task for the failing server, ensuring tools come back online even if the external server restarts.
- **WebSocket Graceful Exits**: In the Fast Brain (`live.py`), the websocket loop specifically catches and ignores `APIError 1000` and `ConnectionClosedOK` exceptions, as these are normal closure codes sent by the Gemini server when a session ends gracefully, preventing unnecessary stack traces.

---

## Deployment & Environment Setup

To fully port this project to a new codebase, you must retain the environmental configuration that runs the Python code. This section documents the external dependencies and the OS-level boot configuration.

### 1. Python Environment (`requirements.txt`)
The `requirements.txt` file contains the essential packages, but there are a few notable quirks to remember when creating your new virtual environment:
- **Google AIY Hardware Module**: The line `/home/pi/aiyprojects` is a local directory link. This is the proprietary Google Python package required to interface with the Voice Bonnet LEDs and Button. You must preserve this directory on your Raspberry Pi and `pip install` it into the new virtual environment.
- **Dependency Discrepancy**: The file lists `pvporcupine` and `pvcobra` (Picovoice wake word engines), but the current codebase actually utilizes `openwakeword`. When rewriting, you can safely drop the Picovoice dependencies and rely solely on `openwakeword`.
- **Core Dependencies**: `pyaudio`, `pygame` (for SFX), `google-genai` (for the Live API WebSocket), `mem0ai` & `chromadb` (for the vector database), and the standard `mcp` library.

### 2. The `systemd` Service File
Jarvis is designed to be a headless daemon that starts automatically when the Raspberry Pi boots. It is controlled by a Linux `systemd` service (likely located at `/etc/systemd/system/jarvis.service`). Because the Python code actively executes `sudo systemctl restart jarvis.service` when the microphone fails, you *must* create an identical service file for the rewrite.

**Template configuration for your rewrite:**
```ini
[Unit]
Description=Jarvis V3 Voice Assistant
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=pi  # (or whichever user has audio group permissions)
WorkingDirectory=/home/pi/jarvis_v3
# Execute the python script using the specific virtual environment:
ExecStart=/home/pi/jarvis_v3/venv/bin/python main.py
Restart=always
RestartSec=5

# Important: ALSA audio often requires the process to have specific environment variables
Environment=XDG_RUNTIME_DIR=/run/user/1000
Environment=PULSE_SERVER=unix:/run/user/1000/pulse/native

[Install]
WantedBy=multi-user.target
```
*Note: Make sure your `pi` user is a member of the `audio` group so `pyaudio` and `pygame` can access the ALSA `.asoundrc` devices without `sudo`.*

---

## Future Rewrite Plan (V3 API Wrapper)

For the upcoming rewrite, the goal is to shift primarily to the "Live API" (WebSockets) for maximum speed and lower latency, temporarily dropping the "Slow Brain" while keeping the architecture open to re-add it later. Additionally, the system should support swappable LLM providers (e.g., Gemini vs. OpenAI).

### The "Adapter" Architecture Pattern
To achieve this easily, you should implement the **Adapter Pattern**. 

Instead of hardcoding `google-genai` directly into `main.py` or the core engine, you create an abstract interface (e.g., `VoiceAssistantProvider`). 

1. **`VoiceAssistantProvider` Interface**:
   This abstract base class mandates that any LLM must provide specific standard functions:
   - `connect()`: Establish the websocket.
   - `send_audio(pcm_data)`: Push raw microphone audio up to the API.
   - `receive_stream()`: An async generator that yields either audio to play, text transcripts, or tool calls.
   - `register_tools(tools_list)`: Converts your local Python tools into whatever JSON schema the specific API requires.

2. **The Concrete Implementations**:
   - `GeminiLiveProvider`: Implements the interface using `google-genai` and `v1alpha`.
   - `OpenAILiveProvider`: Implements the interface using the `openai` SDK and its Realtime API.

3. **The Core Engine Orchestrator**:
   You create an `engine.py` that takes the generic provider. It constantly pulls from your `multiprocessing.Queue` and feeds it to `provider.send_audio()`. When `provider.receive_stream()` yields a tool call, the engine executes it locally and sends the result back. If you want to add the "Slow Brain" back later, you simply add a `delegate_to_slow_brain()` hook inside this engine.

### Existing Libraries vs. Custom Wrapper
There are some off-the-shelf libraries designed to abstract realtime voice AI (like **`pipecat-ai`** or **`LiveKit`**). 

**Recommendation:** For your specific use case, **building a custom lightweight wrapper is highly recommended over using those libraries.**
- Frameworks like Pipecat or LiveKit are heavily geared toward WebRTC (running in the cloud or browser) and try to aggressively manage the audio routing themselves.
- Because Jarvis relies on very specific, quirky local Linux ALSA hardware settings (`dsnoop`, `dmix`, and specific ALSA commands), those heavy frameworks often conflict with the audio drivers and break. 
- Writing your own lightweight `VoiceAssistantProvider` adapter wrapper ensures you keep 100% control over the Raspberry Pi hardware and ALSA queues, while still getting the benefit of easily swapping between OpenAI and Gemini.

---

## Hardcoded Configurations & Endpoints

To ensure the new rewrite connects seamlessly to your existing infrastructure, here is a consolidated list of all hardcoded IPs, URLs, specific entity IDs, and coordinates found across the V2 codebase that you will likely need to port over.

### 1. Local Network Endpoints
- **MCP Server Base URL**: `http://mcp.local` (Located in `core/mcp.py`). 
  - *Note*: The system explicitly expects the following server names to be active on this host: `["gcal", "maps", "search"]`.
- **Home Assistant URL**: Typically `http://homeassistant.local:8123` (Loaded via `HA_URL` in `.env` or `config.py`).

### 2. Hardcoded Home Assistant Entities
These specific entities are hardcoded into the Python logic and must exist in your Home Assistant instance for full functionality:
- `sensor.hifiberry_plexamp_volume`: Read during the `lower_volume()` and `restore_volume()` steps in `main.py` to "dip" the background music.
- `input_text.jarvis_chat`: The "Mailbox" entity polled by `main.py` to trigger Jarvis remotely via text.
- `sensor.jarvis_last_response`: Updated by Jarvis in `main.py` (via `ha.set_state`) so your HA dashboard displays what Jarvis just said.

### 3. External API Endpoints
- **Nominatim (OpenStreetMap)**: `https://nominatim.openstreetmap.org/search` (Used in `navigation.py` for geocoding).
- **OSRM Routing**: `http://router.project-osrm.org/route/v1/cycling/` (Used in `navigation.py` to estimate bike routes).
- **Komoot**: `https://www.komoot.de/plan` (Used in `navigation.py` to generate clickable bike route URLs).
- **Google Cloud TTS**: `https://texttospeech.googleapis.com/v1/text:synthesize` (Used in `google.py` as a fallback TTS engine, with voice `de-DE-Journey-D`).

### 4. Static Coordinates & System Settings
- **Home Location**: 
  - Latitude: `48.15354648083181`
  - Longitude: `11.559096247217958`
  - *(Listed as "Massmann" in `config.py`)*
- **Wake Word Model**: `hey_jarvis` (openWakeWord searches for this exact file string).
- **Audio Constants**: `16000` Hz (RATE), `1` Channel, `512` Chunk Size.
