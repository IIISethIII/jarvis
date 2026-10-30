#!/bin/bash
# Restart the jarvis service, wait until it listens for the wake word again and report errors from this boot.
# Runs on the Pi itself, or remotely when JARVIS_PI=user@host is set.
# Exit codes: 0 = ready, 1 = traceback/crash, 2 = timeout.
# Usage: [JARVIS_PI=pi@host] [TIMEOUT=180] scripts/restart_and_check.sh
set -u
TIMEOUT=${TIMEOUT:-180}

remote_script() {
    echo "TIMEOUT=$TIMEOUT"
    cat <<'EOF'
READY="Listening for 'Hey Jarvis'"
since=$(date '+%Y-%m-%d %H:%M:%S')
start=$(date +%s)
sudo systemctl restart jarvis
status=timeout
while :; do
    sleep 2
    elapsed=$(( $(date +%s) - start ))
    log=$(sudo journalctl -u jarvis --since "$since" --no-pager -o cat)
    if grep -q "Traceback" <<<"$log"; then status=error; break; fi
    if grep -qF "$READY" <<<"$log"; then status=ready; break; fi
    if [ "$elapsed" -gt 15 ] && ! systemctl is-active -q jarvis; then status=error; break; fi
    if [ "$elapsed" -ge "$TIMEOUT" ]; then break; fi
done
# Known harmless startup noise from PyAudio/ALSA/JACK probing and onnxruntime
NOISE='ALSA lib|[Jj]ack|JackShm|server socket|server request channel|CUDAExecutionProvider|warnings\.warn'
clean=$(grep -vE "$NOISE" <<<"$log")
echo "RESULT: $status after ${elapsed}s"
echo "--- warnings/errors since restart"
grep -iE "traceback|error|exception|failed|warn|❌" <<<"$clean" | tail -25 || echo "(none)"
echo "--- MCP"
grep -E "\[MCP\]|dynamic tools" <<<"$clean" | tail -6
echo "--- last log lines"
tail -12 <<<"$clean"
case $status in ready) exit 0 ;; error) exit 1 ;; *) exit 2 ;; esac
EOF
}

if [ -n "${JARVIS_PI:-}" ]; then
    remote_script | ssh "$JARVIS_PI" 'bash -s'
else
    remote_script | bash -s
fi
