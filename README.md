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
  tools.py           local tools (call_ha_service, set_speaker_volume, core memory, archive search, ...)
  mcp.py             MCP client; servers in mcp_config.json
  audio.py           mic process, playback, ALSA mixer setup
  wake.py            openWakeWord detector
  sfx.py             UI sounds (raw PCM)
  hardware.py        Voice Bonnet V2 RGB LED (mock fallback without smbus2)
  ha.py              Home Assistant REST wrapper
  memory.py          data/core_memory.md + data/archive.jsonl (not in git)
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

```bash
git clone https://github.com/IIISethIII/jarvis.git ~/jarvis_v3 && cd ~/jarvis_v3
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env                        # fill in keys
cp deploy/asound.conf ~/.asoundrc           # adjust device names to your hardware
sudo cp deploy/jarvis.service /etc/systemd/system/   # adjust user/paths
sudo systemctl enable --now jarvis
```

## Development

- Logs: `journalctl -u jarvis -f`, restart: `sudo systemctl restart jarvis`
- Restart and verify the boot (waits for the ready line, prints errors): `scripts/restart_and_check.sh`
  (from another machine: `JARVIS_PI=pi@<pi-ip> scripts/restart_and_check.sh`)
- Test without speaking:
  1. `sudo systemctl stop jarvis`
  2. `venv/bin/python main.py --test`
  3. in a second shell: `venv/bin/python scripts/mock_trigger.py "Mach das Licht an"`
- Manual tests run from the repo root as modules, e.g. `venv/bin/python -m tests.manual.test_mcp`.
