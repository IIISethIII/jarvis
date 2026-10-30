# Jarvis v3

Voice assistant for a Raspberry Pi Zero 2W with Google AIY Voice Bonnet V2 and PS3 Eye microphone,
built on the Gemini Live API (native voice-in/voice-out).

Predecessor: the v2 hybrid architecture (tag `v2-final`).
Open features to port from v2: [FEATURE_BACKLOG.md](FEATURE_BACKLOG.md).

## Components

- `main.py`: boot, mic watchdog, wake word loop (`--test` = inject text via `trigger.tmp`)
- `core/llm.py`: `GeminiLiveProvider` (Live session, system prompt with HA context, tool calls, API key rotation)
- `core/tools.py`: local tools (`call_ha_service`, `set_speaker_volume`, core memory, archive search, ...)
- `core/mcp.py` + `core/mcp_config.json`: MCP servers (gateway, Basic Memory, HiFiBerry)
- `core/audio.py`, `core/wake.py`, `core/sfx.py`, `core/hardware.py`: ALSA, openWakeWord, PCM sounds, LEDs
- `core/ha.py`: Home Assistant REST wrapper
- `core/memory.py`: `data/core_memory.md` + `data/archive.jsonl` (not in git)

## Setup on the Pi

```bash
cd /home/pi/jarvis_v3
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp asound.conf ~/.asoundrc
```

`.env`:

```bash
GEMINI_KEYS="key1,key2"
GEMINI_LIVE_MODEL="gemini-3.1-flash-live-preview"
HA_URL="http://homeassistant.local:8123"
HA_TOKEN="..."
```

systemd unit `/etc/systemd/system/jarvis.service`:

```ini
[Unit]
Description=Jarvis V3 AI Voice Assistant
After=network.target sound.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/jarvis_v3
ExecStartPre=/bin/sleep 2
ExecStart=/home/pi/jarvis_v3/venv/bin/python /home/pi/jarvis_v3/main.py
TimeoutStopSec=5
KillMode=control-group
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```
