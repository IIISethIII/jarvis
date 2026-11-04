# Jarvis – project notes for Claude

Voice assistant built on the **Gemini Live API** (native voice-in/voice-out) that runs on a Raspberry Pi.
Reference hardware: Pi Zero 2W, Google AIY Voice Bonnet V2 (speaker, RGB LED, button), PS3 Eye USB mic.
Code is written on a separate dev machine and synced to the Pi; it does not fully run anywhere else (see "Testing").

Open work: [FEATURE_BACKLOG.md](FEATURE_BACKLOG.md) (features to port from v2, portability tasks).
Previous architecture (Live front-end + REST "slow brain" + Mem0) is tag `v2-final`; read old code with `git show v2-final:services/ha.py`.

## Runtime flow

1. `main.py` boots: LEDs (`core/hardware.py`), ALSA mixer levels (`core/audio.init_audio_settings`), MCP connections, the learner thread, `AudioManager`, `WakeWordDetector`.
2. Idle loop: mic chunks (16 kHz mono int16, 512 frames) come from a **separate process** via `multiprocessing.Queue`. That process applies a high-pass filter (`JARVIS_MIC_HIGHPASS`, 150 Hz) against mains hum, so the wake word and Gemini both get the filtered signal. Watchdogs restart the mic process on timeout, "rapid-fire" chunks, dead silence or a backed-up queue. Each chunk goes to openWakeWord (`hey_jarvis`, `JARVIS_WAKE_THRESHOLD` 0.5); scores just below the threshold are logged as `[WakeWord] Near miss`.
3. Wake word → `asyncio.run(GeminiLiveProvider.connect(...))` (`core/llm.py`). One Live session per wake word, with three tasks:
   - `_send_audio_loop`: mic → 4x digital gain → `send_realtime_input`
   - `_receive_audio_loop`: audio (24 kHz) → playback queue, transcripts → log, tool calls → execute → `send_tool_response`
   - `_inactivity_loop`: ends the session after `JARVIS_IDLE_TIMEOUT` (15 s) of silence, counted from the end of Jarvis' speech and paused while a tool runs (plus a deadlock watchdog in `connect`).
     - User speech only counts as activity once Gemini answers it. Unanswered speech (music, TV, people talking) gets a grace period that shrinks the longer Jarvis listens, capped at timeout + `JARVIS_SPEECH_GRACE`.
     - While Gemini's server VAD reports the user speaking (`voice_activity` START → END), the timer is paused, for at most `JARVIS_MAX_SPEECH` s so endless background audio can't hold the session.
     - While Jarvis speaks, `_send_audio_loop` sends silence instead of the dropped mic audio, so the server sees a continuous stream.
4. The session also ends after `end_conversation` or when the server sends GoAway. Then the sleep sound plays, LEDs go off, and the loop returns to idle.
   - `end_conversation` (tool result `"<END_CONVERSATION>"`) doesn't close right away. It sets `end_requested`, and `_inactivity_loop` closes once it has been quiet for `JARVIS_END_GRACE` (2 s). User speech before that (VAD start or a transcript) cancels the end: server VAD sometimes cuts a sentence ("Perfekt. Sonst noch was zu"), and Gemini then took the fragment for a goodbye.
   - The call gets no tool response unless the end is cancelled (`_cancel_end`): any response makes Gemini talk again, and it once spoke a literal "<no speech>".
   - It is the only tool call without sounds: no thinking loop, no success sound, and the thinking loop started by the user's words is stopped. Only the sleep sound plays when the session closes.
5. Timers and alarms (`core/timers.py`, tool `manage_timer`) run in their own thread and persist in `data/timers.json`. A firing timer cancels a running session and loops `assets/sounds/alarm.pcm` with an orange pulsing LED until the wake word, the button or `JARVIS_ALARM_TIMEOUT` (120 s) stops it. While it rings, the wake word only stops the alarm and does not start a session.

Important behaviour:
- **Half-duplex:** `AudioManager.get_audio_chunk` drops mic input while audio is playing, so the user cannot interrupt by voice.
- **The system prompt is built per session** by `core/prompt.py` (see "Prompt, workspace and learner" below). Everything is re-read at every wake word, so file edits need no restart.
- **API keys:** `GEMINI_KEYS` is a comma-separated pool. The key rotates after each session and on 429/quota errors.
- **SFX** are raw PCM (24 kHz, mono, s16le) in `assets/sounds/` and go through the same output stream as speech. Convert new sounds with ffmpeg (see `convert_sounds.py`).
  - `AudioManager.play_chunk` queues audio in 0.1 s pieces because the blocking stream write can't be interrupted. `stop_playback()` therefore cuts within 0.1 s. Don't write long buffers to the stream another way: a whole 3.8 s thinking loop used to delay every answer until it ended, with the mic open in that gap.

## Prompt, workspace and learner

- **Order** (`core/prompt.py`): `prompts/core.md` (fixed rules, English, `{assistant_name}`/`{language}`/`{time}` placeholders) → workspace `SOUL.md` (personality) and `AGENTS.md` (house rules) → tool guides from `prompts/guides/` → skill list → `USER.md`, `MEMORY.md` → recent changes → live context (recent conversations, timers, HA overview).
- **Guides** (`prompts/guides/*.md`) explain a tool. Their frontmatter `requires: a, b` names the tools that must all be loaded, otherwise the guide is left out. New tool rules go into a guide, not into `core.md`.
- **Workspace** (`core/workspace.py`, `data/workspace/`, gitignored, seeded from `prompts/templates/`): `SOUL`, `AGENTS`, `USER`, `MEMORY` with character budgets, plus `skills/<name>.md` (frontmatter `description`, optional `always: true`). Jarvis sees only the skill names and loads bodies with `load_skills`.
- **Jarvis never edits the workspace.** The tool `remember` queues a note in `.inbox.jsonl`.
- **The learner** (`core/learner.py`) is a background thread in the Jarvis process that only runs between sessions. It uses text models from `JARVIS_LEARNER_MODELS`, moving to the next model on 503 and the next key on 429. When all are busy it retries the whole chain after 20, 60 and 180 s. Don't add weak models like Flash-Lite as a fallback: they paraphrase entries and lose details. On the free tier these Flash models allow only **20 requests per day per project** (reset at midnight Pacific). On quota errors (429) the learner gives up at once and tries again 30 min later; notes stay queued meanwhile. Each conversation test session uses learner quota too. It runs:
  - right after a session with notes;
  - `JARVIS_LEARNER_DELAY_MIN` after ordinary sessions, skipping trivial ones. These runs may not change SOUL and AGENTS;
  - nightly (`JARVIS_REFLECT_HOUR`) as a reflection;
  - for `scripts/import_facts.py` and `scripts/onboard.py`.
- **Against drift:** the model returns single changes (add / replace / remove of one entry). `workspace.Run` validates them (unique `old_text`, duplicates, budgets, max changes per run) and writes changed files once. There is no whole-file rewrite on purpose.
  - Each run snapshots the files in `.history/<run>/` and logs every change with its reason in `CHANGES.md`.
  - Undo by voice (via `remember`) or with `python -m scripts.import_facts --undo`.
  - Keep it that way when extending the learner.
- The archive (`data/archive.jsonl`) stores each turn's tool calls (`tools`, results cut to 300 chars) so the learner can see what worked.

## Tools

- Local tools live in `core/tools.py`. Add a `types.FunctionDeclaration` to `FUNCTION_DECLARATIONS`, map the name in `TOOL_IMPLEMENTATIONS`, and put the usage rules in a guide under `prompts/guides/` with `requires:` set to the tool.
- Any tool name not in `TOOL_IMPLEMENTATIONS` is routed to MCP (`core/mcp.py`, servers in `core/mcp_config.json`, types `sse` or `stateless`). **Every** MCP tool is sent to every Live session, so keep the tool count in mind.
  - The tool list is fetched **once at boot**; new tools on a server need a Jarvis restart.
  - Hide tools per server with an optional blacklist of fnmatch patterns: `"exclude": ["library_delete_*"]`, or offer only some with an allowlist: `"include": ["bm_search", "bm_read"]`. Default: all tools.
  - Basic Memory (the user's knowledge base shared with other agents) is read-only for Jarvis: only `bm_search` and `bm_read`, with guide `prompts/guides/basic-memory.md` and project `JARVIS_BASIC_MEMORY_PROJECT` (default `main`). Jarvis' own facts stay in the workspace files and win on conflicts; writing goes through the learner, never to Basic Memory.
  - Free-form object parameters (no `properties`, e.g. a gateway's `call_tool` `arguments`) are offered to Gemini as JSON strings and decoded before the call.
- Optional delegation (`core/openclaw.py`, tool `ask_openclaw`): with `OPENCLAW_URL` and `OPENCLAW_TOKEN` set, Jarvis hands what it can't answer to an OpenClaw agent via the Gateway's `/v1/chat/completions`. The tool declaration and its prompt section only exist when configured. Answers take ~5–60 s. It is the one exception to the prompt's "never announce a tool" rule: Gemini says a short heads-up, then calls the tool, and the thinking SFX is queued behind that speech instead of cutting it off. The idle timer is paused meanwhile. Test off the Pi with `python3 -m core.openclaw "Frage"`.
- **Home Assistant context** (`core/ha_context.py`): the prompt only gets a compact overview, one line per entity with state, unit and short attributes, grouped by room if HA has areas, plus service names per domain. A trailing `+` marks more data. The tool `get_ha_details` returns the full attributes, the current state, a search over all entities, the parameters of a domain's services, and for `todo`/`weather`/`calendar` entities the service response (items, daily forecast, events). Above `JARVIS_HA_CONTEXT_CHARS` (30000) the overview drops attributes, then read-only entities. Don't put full attribute dumps back into the prompt: single sensors (forecasts, canteen menus) are 10–30k chars each.
- Local tools run **synchronously inside the async receive loop** and block audio meanwhile. Wrap slow ones (`asyncio.to_thread`).
- Tool results go back as `{"result": ...}`. Keep them short; they cost tokens and latency.

## Constraints

- **Weak target hardware** (Pi Zero 2W, 512 MB RAM, Jarvis already uses ~60 %). No heavy dependencies on the Pi (no Mem0, ChromaDB, torch, LiteLLM); offload heavy work to MCP servers or other machines. Don't run VS Code Remote-SSH on the Pi.
- **Public repository.** Never commit secrets or personal data. `.env`, `data/` (workspace, conversation archive) and recordings are gitignored. Personal setup belongs in gitignored files (`.env`, `CLAUDE.local.md`, later a local config).
- **LF line endings** everywhere (`.gitattributes`): the code and shell scripts run on Linux.
- Log with `print(f"[Module] ...", flush=True)` so journald shows it immediately.

## Portability (goal)

The repo should work for other people with other hardware and infrastructure, not only for the maintainer's setup. For new or touched code:
- No hardcoded hosts, IPs, HA entity IDs, personal names, voice, model or thresholds in code. Use env vars or config files, commit an `*.example`, and gitignore the real file.
- Optional integrations (Home Assistant, each MCP server, LEDs, button) must degrade gracefully: missing config or hardware means the feature is off, not a crash.
- Hardware access stays behind small interfaces (`core/hardware.py`, `core/audio.py`) so other boards and sound cards can be added.
- Personal prompt content (persona, household rules) belongs in the workspace, not in `prompts/` or code.

The list of currently hardcoded items is in FEATURE_BACKLOG.md under "Portabilität".

## Testing

- **On the Pi:**
  - Restart the service with `sudo systemctl restart jarvis` and follow the logs with `journalctl -u jarvis -f`.
  - To test without speaking: stop the service, run `venv/bin/python main.py --test`, then from a second shell run `venv/bin/python scripts/mock_trigger.py "Mach das Licht an"`. The text is injected into a Live session and the answer is spoken.
- **Manual test scripts** live in `tests/manual/`. Run them **from the repo root as modules**, e.g. `python -m tests.manual.test_mcp`, so that `core.*` imports resolve.
- **Off the Pi:** HA, MCP and Gemini text tests work (`tests.manual.test_ha_llm`, `tests.manual.test_mcp`, `core/ha.py`, `core/memory.py`, `core/tools.py`). Anything that touches ALSA or the mic (`main.py`, `core/audio.py`) needs the Pi. `core/hardware.py` falls back to mock LEDs without `smbus2`.
- **Conversation tests** (`tests/conversations/run.py`, run on Octopus with `venv/bin/python -m tests.conversations.run <files or folders>`):
  - They run text sessions against the real prompt and Live model in a throwaway data dir. The Pi stays silent.
  - Side-effect tools are stubbed. `remember`, `load_skills` and `search_archive` run for real, and the learner runs after every session.
  - Scenarios are JSON (format in the script docstring). Generic ones live in `examples/`, personal ones in `local/` (gitignored).
  - Each run uses the Live and learner quota of the shared key pool, so run them sparingly.
- **Unit tests** live in `tests/unit/` (standard `unittest`, no hardware or network): `python -m unittest discover tests/unit`. `test_prompt_fstrings.py` runs anywhere and catches unknown `{names}` in f-strings like the system prompt; `test_mcp_schema.py` needs the runtime dependencies, so run it on the Pi with `venv/bin/python`.
- Paths in scripts must be relative to the repo (`os.path.abspath(__file__)`), never `/home/pi/...`.
- After changing `requirements.txt`, run `venv/bin/pip install -r requirements.txt` on the Pi. It is slow, so batch dependency changes.

## Verifying a change (workflow)

Restarting Jarvis is expensive. On the reference Pi it takes **~45 s** until `[Idle] Listening for 'Hey Jarvis'...`. During that time the assistant is deaf, and a restart cuts off a running conversation. Verify cheaply first and restart rarely:

1. **Always (cheap):** `python3 -m py_compile <changed files>`. If the change touches HA, MCP or Gemini logic, also run the matching off-Pi test (`python -m tests.manual.test_mcp`, `test_ha_llm`, ...).
2. **Restart only for runtime changes.** That means `main.py`, `core/`, `assets/sounds/`, or new dependencies. `prompts/` and the workspace are read per session and need no restart. Changes to docs, `tests/` or `scripts/` need no restart.
   - Batch the work: **one restart at the end of a task**, not after every edit.
   - Make sure the code has reached the Pi before restarting.
3. **Restart and check** with `scripts/restart_and_check.sh` (set `JARVIS_PI=user@host` when running off the Pi). The script waits for the ready line, then prints errors, MCP status and the last log lines with the known ALSA/JACK/CUDA noise filtered out.
   - Exit `0` = ready.
   - Exit `1` = traceback or crash: read the log, fix, repeat.
   - Exit `2` = timeout.
   - Also check that all MCP servers came online and the dynamic tool count is plausible.
4. **Behaviour is not verified by a clean boot.** Whether a new tool or prompt rule actually works needs a voice test or the `--test` mode with `scripts/mock_trigger.py`. The `--test` mode stops the service and speaks out loud, so **ask the user before running it**. Otherwise tell them exactly what to say to Jarvis to test the change, and report "boots cleanly, behaviour untested" honestly.
5. If the user might be talking to Jarvis right now, ask before restarting.

## Configuration

- `.env`: see `.env.example`.
- `data/workspace/`: personality, house rules, user, memory, skills (see above). First setup: `python -m scripts.onboard`. Import old notes: `python -m scripts.import_facts <file>`. Edit by hand only while Jarvis is idle (the Pi side writes, Mutagen syncs both ways). The content goes into the prompt verbatim.
- `core/mcp_config.json`: MCP servers.
- `deploy/asound.conf`: ALSA setup for the reference hardware (copy to `~/.asoundrc`).
- `deploy/jarvis.service`: systemd unit.

## Layout

- Runtime code: `main.py`, `core/`, `prompts/`, `assets/sounds/`.
- Everything else is tooling:
  - `scripts/`, with `debug/` and the one-off `migration/` (v2 Mem0 → v3)
  - `tests/manual/`
  - `deploy/`
  - `docs/` (`v2-architecture.md` describes the old design and is useful when porting backlog features)
- Keep the repo root clean. New helpers go into these folders.

## Personal setup

The maintainer's machine-specific notes (sync, hosts, aliases) live in the gitignored file below. It may not exist in your clone.

@CLAUDE.local.md
