# Jarvis v3

Voice assistant for the Raspberry Pi built on the Gemini Live API (native voice-in/voice-out),
with Home Assistant control and MCP tools.

Reference hardware: Raspberry Pi Zero 2W, Google AIY Voice Bonnet V2, PS3 Eye USB microphone.

Predecessor: the v2 hybrid architecture (tag `v2-final`).
Open work: [FEATURE_BACKLOG.md](FEATURE_BACKLOG.md).

## Repository layout

```
main.py              boot, mic watchdog, wake word loop (--test: inject text via trigger.tmp)
core/
  llm.py             GeminiLiveProvider: Live session, system prompt with HA context, tool calls, key rotation
  tools.py           local tools (call_ha_service, set_speaker_volume, get_weather, core memory, archive search, ...)
  weather.py         weather for any place: Open-Meteo, in Germany DWD radar and warnings (Bright Sky), no API key
  mcp.py             MCP client; servers in mcp_config.json
  audio.py           mic process, playback, ALSA mixer setup
  wake.py            openWakeWord detector
  sfx.py             UI sounds (raw PCM)
  wakewords.py       finds wake word models: your own .onnx in data/wakewords/, then openWakeWord's
  hardware.py        Voice Bonnet V2 RGB LED (mock fallback without smbus2)
  ha.py              Home Assistant REST wrapper
  memory.py          data/core_memory.md, data/archive.jsonl + embeddings for semantic search (not in git)
assets/sounds/       UI sounds as 24 kHz mono s16le PCM
assets/sounds_src/   original .ogg sources (see scripts/convert_sounds.py)
deploy/              asound.conf (ALSA for the reference hardware), jarvis.service
scripts/             mock_trigger.py, convert_sounds.py, list_models.py
  debug/             gdb/pystack helpers for a hanging process
  migration/         one-off v2 (Mem0) -> v3 memory migration
tests/manual/        manual hardware/integration test scripts
docs/                v2 architecture and v3 rewrite plan
```

## Setup on the Pi

The quick way: clone, run the installer, finish in the browser.

```bash
git clone https://github.com/IIISethIII/jarvis.git ~/jarvis_v3 && cd ~/jarvis_v3
deploy/install.sh            # --help for options, e.g. --alsa for the reference hardware, --port 8080
```

It installs the system packages, the venv, both systemd units and the web interface, then prints the address
(e.g. `http://jarvis.local/`). There you choose a password, and the setup assistant asks for the Gemini key,
optional Home Assistant access and a few things about you. Until a key is set Jarvis waits without loading its
models. The microphone and speaker setup (`deploy/asound.conf`) is hardware-specific: adjust it to your sound card.

By hand:

```bash
git clone https://github.com/IIISethIII/jarvis.git ~/jarvis_v3 && cd ~/jarvis_v3
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env                        # fill in keys
cp core/mcp_config.example.json core/mcp_config.json   # optional: your MCP servers
cp deploy/asound.conf ~/.asoundrc           # adjust device names to your hardware
sudo cp deploy/jarvis.service /etc/systemd/system/   # adjust user/paths
sudo systemctl enable --now jarvis
```

## Web interface

A small settings page, made to set Jarvis up without a terminal. It needs only the Python standard library.

- **Status:** conversation, alarm, learner, MCP servers, timers, RAM; restart now or as soon as Jarvis is idle.
- **Settings:** every `.env` setting as a form with defaults, ranges and help texts, connection tests for
  Gemini keys, Home Assistant and OpenClaw, and a hint which changes still need a restart.
- **Workspace:** personality, house rules, what Jarvis knows about you, skills; with the change history and undo.
- **Tools:** MCP servers (add, test, hide tools).
- **Log:** the journal of the service, with secrets masked.

```bash
sudo cp deploy/jarvis-web.socket deploy/jarvis-web.service /etc/systemd/system/   # adjust user/paths in .service
sudo install -m 0440 deploy/jarvis-web.sudoers /etc/sudoers.d/jarvis-web           # restart button; adjust the user
sudo systemctl daemon-reload && sudo systemctl enable --now jarvis-web.socket
```

Open `http://<pi-address>/` (e.g. `http://jarvis.local/`) and choose a password; the first visitor sets it.
systemd listens on port 80 and starts the server on the first request. The server ends itself after
`JARVIS_WEB_IDLE_MIN` (10) minutes without requests, so it costs no RAM while nobody uses it (~16 MB while it runs).

- Plain HTTP for your LAN only: don't forward the port to the internet.
- Secrets (API keys, tokens) are write-only on the page: it shows whether they are set and their last characters.
- Forgot the password: `venv/bin/python -m web.server --set-password` on the Pi.
- Try it on any machine without touching your `.env`: `python3 -m web.server --port 8080 --env-file /tmp/test.env`.

## Development

- Logs: `journalctl -u jarvis -f`, restart: `sudo systemctl restart jarvis`
- Restart and verify the boot (waits for the ready line, prints errors): `scripts/restart_and_check.sh`
  (from another machine: `JARVIS_PI=pi@<pi-ip> scripts/restart_and_check.sh`)
- Test without speaking:
  1. `sudo systemctl stop jarvis`
  2. `venv/bin/python main.py --test`
  3. in a second shell: `venv/bin/python scripts/mock_trigger.py "Mach das Licht an"`
- Manual tests run from the repo root as modules, e.g. `venv/bin/python -m tests.manual.test_mcp`.
