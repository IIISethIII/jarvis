# Jarvis – project notes for Claude

Voice assistant built on the **Gemini Live API** (native voice-in/voice-out) that runs on a Raspberry Pi.
Reference hardware: Pi Zero 2W, Google AIY Voice Bonnet V2 (speaker, RGB LED, button), PS3 Eye USB mic.
Code is written on a separate dev machine and synced to the Pi; it does not fully run anywhere else (see "Testing").

Open work: [FEATURE_BACKLOG.md](FEATURE_BACKLOG.md) (features to port from v2, portability tasks).
Previous architecture (Live front-end + REST "slow brain" + Mem0) is tag `v2-final`; read old code with `git show v2-final:services/ha.py`.

## Runtime flow

1. `main.py` boots: LEDs (`core/hardware.py`), ALSA mixer levels (`core/audio.init_audio_settings`), core memory, MCP connections, `AudioManager`, `WakeWordDetector`.
2. Idle loop: mic chunks (16 kHz mono int16, 512 frames) come from a **separate process** via `multiprocessing.Queue`. Watchdogs restart the mic process on timeout, "rapid-fire" chunks, dead silence or a backed-up queue. Each chunk goes to openWakeWord (`hey_jarvis`, threshold 0.5).
3. Wake word → `asyncio.run(GeminiLiveProvider.connect(...))` (`core/llm.py`). One Live session per wake word, with three tasks:
   - `_send_audio_loop`: mic → 4x digital gain → `send_realtime_input`
   - `_receive_audio_loop`: audio (24 kHz) → playback queue, transcripts → log, tool calls → execute → `send_tool_response`
   - `_inactivity_loop`: ends the session after 15 s without activity (plus a 30 s deadlock watchdog in `connect`)
4. The session also ends when a tool returns `"<END_CONVERSATION>"` or the server sends GoAway. Then the sleep sound plays, LEDs go off, and the loop returns to idle.

Important behaviour:
- **Half-duplex:** `AudioManager.get_audio_chunk` drops mic input while audio is playing, so the user cannot interrupt by voice.
- **The system prompt is built per session** in `connect()`: static rules (German) + core memory + all HA entity states + all HA services (fetched live). Core memory is read **once at boot** in `main.py`, so edits only take effect after a restart.
- **API keys:** `GEMINI_KEYS` is a comma-separated pool. The key rotates after each session and on 429/quota errors.
- **SFX** are raw PCM (24 kHz, mono, s16le) in `assets/sounds/` and go through the same output stream as speech. Convert new sounds with ffmpeg (see `convert_sounds.py`).

## Tools

- Local tools live in `core/tools.py`. Add a `types.FunctionDeclaration` to `FUNCTION_DECLARATIONS`, map the name in `TOOL_IMPLEMENTATIONS`, and add usage rules to the system prompt in `core/llm.py`.
- Any tool name not in `TOOL_IMPLEMENTATIONS` is routed to MCP (`core/mcp.py`, servers in `core/mcp_config.json`, types `sse` or `stateless`). **Every** MCP tool is sent to every Live session, so keep the tool count in mind.
- Local tools run **synchronously inside the async receive loop** and block audio meanwhile. Wrap slow ones (`asyncio.to_thread`).
- Tool results go back as `{"result": ...}`. Keep them short; they cost tokens and latency.

## Constraints

- **Weak target hardware** (Pi Zero 2W, 512 MB RAM, Jarvis already uses ~60 %). No heavy dependencies on the Pi (no Mem0, ChromaDB, torch, LiteLLM); offload heavy work to MCP servers or other machines. Don't run VS Code Remote-SSH on the Pi.
- **Public repository.** Never commit secrets or personal data. `.env`, `data/` (core memory, conversation archive) and recordings are gitignored. Personal setup belongs in gitignored files (`.env`, `CLAUDE.local.md`, later a local config).
- **LF line endings** everywhere (`.gitattributes`): the code and shell scripts run on Linux.
- Log with `print(f"[Module] ...", flush=True)` so journald shows it immediately.

## Portability (goal)

The repo should work for other people with other hardware and infrastructure, not only for the maintainer's setup. For new or touched code:
- No hardcoded hosts, IPs, HA entity IDs, personal names, voice, model or thresholds in code. Use env vars or config files, commit an `*.example`, and gitignore the real file.
- Optional integrations (Home Assistant, each MCP server, LEDs, button) must degrade gracefully: missing config or hardware means the feature is off, not a crash.
- Hardware access stays behind small interfaces (`core/hardware.py`, `core/audio.py`) so other boards and sound cards can be added.
- Personal prompt content (persona, household rules) belongs in a user-editable file, not in `core/llm.py`.

The list of currently hardcoded items is in FEATURE_BACKLOG.md under "Portabilität".

## Testing

- **On the Pi:**
  - Restart the service with `sudo systemctl restart jarvis` and follow the logs with `journalctl -u jarvis -f`.
  - To test without speaking: stop the service, run `venv/bin/python main.py --test`, then from a second shell run `venv/bin/python scripts/mock_trigger.py "Mach das Licht an"`. The text is injected into a Live session and the answer is spoken.
- **Manual test scripts** live in `tests/manual/`. Run them **from the repo root as modules**, e.g. `python -m tests.manual.test_mcp`, so that `core.*` imports resolve.
- **Off the Pi:** HA, MCP and Gemini text tests work (`tests.manual.test_ha_llm`, `tests.manual.test_mcp`, `core/ha.py`, `core/memory.py`, `core/tools.py`). Anything that touches ALSA or the mic (`main.py`, `core/audio.py`) needs the Pi. `core/hardware.py` falls back to mock LEDs without `smbus2`.
- There is no automated test suite yet.
- Paths in scripts must be relative to the repo (`os.path.abspath(__file__)`), never `/home/pi/...`.
- After changing `requirements.txt`, run `venv/bin/pip install -r requirements.txt` on the Pi. It is slow, so batch dependency changes.

## Configuration

- `.env`: see `.env.example`.
- `core/mcp_config.json`: MCP servers.
- `deploy/asound.conf`: ALSA setup for the reference hardware (copy to `~/.asoundrc`).
- `deploy/jarvis.service`: systemd unit.

## Layout

- Runtime code: `main.py`, `core/`, `assets/sounds/`.
- Everything else is tooling:
  - `scripts/`, with `debug/` and the one-off `migration/` (v2 Mem0 → v3)
  - `tests/manual/`
  - `deploy/`
  - `docs/` (`v2-architecture.md` describes the old design and is useful when porting backlog features)
- Keep the repo root clean. New helpers go into these folders.

## Personal setup

The maintainer's machine-specific notes (sync, hosts, aliases) live in the gitignored file below. It may not exist in your clone.

@CLAUDE.local.md
