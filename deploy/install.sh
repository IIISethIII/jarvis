#!/bin/bash
# Installs Jarvis on a Raspberry Pi (or another Debian-based Linux): system packages, venv, systemd units for
# Jarvis and the web interface, and the sudoers rule for its restart button. Then open http://<device>/ to set up.
#
# Run it from the repo as the user Jarvis should run as (not root; sudo is used where needed):
#   deploy/install.sh [--no-apt] [--no-start] [--alsa] [--port N] [--dry-run]
#     --no-apt     skip the apt packages
#     --no-start   install, but don't start the services
#     --alsa       copy deploy/asound.conf to ~/.asoundrc (only for the reference hardware: AIY Voice Bonnet + PS3 Eye)
#     --port N     port of the web interface (default 80)
#     --dry-run    only print what would be done
# Safe to run again (e.g. after git pull): an existing .env, ~/.asoundrc and data/ stay untouched.
set -euo pipefail

APT=1 START=1 ALSA=0 PORT=80 DRY=0
while [ $# -gt 0 ]; do
    case "$1" in
        --no-apt) APT=0 ;;
        --no-start) START=0 ;;
        --alsa) ALSA=1 ;;
        --port) PORT="${2:?--port needs a number}"; shift ;;
        --dry-run) DRY=1 ;;
        -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 1 ;;
    esac
    shift
done

cd "$(dirname "$0")/.."
REPO=$(pwd)
RUN_USER=$(id -un)
SYSTEMD=/etc/systemd/system

if [ "$(id -u)" = 0 ]; then
    echo "Run this as the user Jarvis should run as, not as root (it uses sudo where needed)." >&2
    exit 1
fi
case "$PORT" in ''|*[!0-9]*) echo "--port needs a number" >&2; exit 1 ;; esac
if [[ "$REPO" =~ [[:space:]] ]]; then
    echo "The repo path must not contain spaces: $REPO" >&2
    exit 1
fi

step() { echo; echo "==> $*"; }
run() {
    echo "+ $*"
    [ "$DRY" = 1 ] || "$@"
}
# install_file <target> <mode>: content from stdin, via sudo
install_file() {
    local target=$1 mode=$2 tmp
    tmp=$(mktemp)
    cat > "$tmp"
    if [ "$DRY" = 1 ]; then
        echo "+ write $target (mode $mode):"
        sed 's/^/    /' "$tmp"
    else
        sudo install -m "$mode" "$tmp" "$target"
        echo "+ wrote $target"
    fi
    rm -f "$tmp"
}
# a unit or rule from deploy/ with this install's user and path
adapt() {
    sed -e "s#/home/pi/jarvis_v3#$REPO#g" -e "s#^User=pi\$#User=$RUN_USER#" -e "s#^pi ALL=#$RUN_USER ALL=#" "$1"
}

if [ "$APT" = 1 ]; then
    step "System packages"
    run sudo apt-get update
    # pyaudio builds against portaudio, lgpio (button) against liblgpio with swig; alsa-utils for the mixer setup
    run sudo apt-get install -y python3-venv python3-dev build-essential portaudio19-dev libasound2-dev \
        swig liblgpio-dev alsa-utils
fi

step "Python environment (slow on a Pi Zero: allow 15-30 minutes the first time)"
[ -d venv ] || run python3 -m venv venv
run venv/bin/pip install --prefer-binary -r requirements.txt

step "Configuration"
if [ -f .env ]; then
    echo "  .env exists, kept."
else
    run cp .env.example .env
    run chmod 600 .env
fi
if [ "$ALSA" = 1 ]; then
    if [ -f "$HOME/.asoundrc" ]; then
        echo "  ~/.asoundrc exists, kept."
    else
        run cp deploy/asound.conf "$HOME/.asoundrc"
    fi
fi
groups=" $(id -nG) "
if [[ "$groups" != *" adm "* && "$groups" != *" systemd-journal "* ]]; then
    # the web interface shows the log with journalctl
    run sudo usermod -aG systemd-journal "$RUN_USER"
fi
for g in audio gpio i2c; do
    if getent group "$g" > /dev/null && [[ "$groups" != *" $g "* ]]; then
        run sudo usermod -aG "$g" "$RUN_USER"
    fi
done

step "systemd units"
adapt deploy/jarvis.service | install_file "$SYSTEMD/jarvis.service" 0644
adapt deploy/jarvis-web.service | install_file "$SYSTEMD/jarvis-web.service" 0644
adapt deploy/jarvis-web.socket | sed "s#^ListenStream=.*#ListenStream=$PORT#" | install_file "$SYSTEMD/jarvis-web.socket" 0644

step "Restart button of the web interface (sudoers rule)"
rule=$(mktemp)
adapt deploy/jarvis-web.sudoers | sed "s#/usr/bin/systemctl#$(command -v systemctl || echo /usr/bin/systemctl)#" > "$rule"
if [ "$DRY" = 1 ] || sudo visudo -cqf "$rule"; then
    install_file /etc/sudoers.d/jarvis-web 0440 < "$rule"
else
    echo "  The sudoers rule did not validate, skipped: the restart button won't work." >&2
fi
rm -f "$rule"

run sudo systemctl daemon-reload
if [ "$START" = 1 ]; then
    step "Start"
    run sudo systemctl enable --now jarvis-web.socket
    run sudo systemctl enable jarvis.service
    run sudo systemctl restart jarvis.service
fi

host=$(hostname)
ip=$(hostname -I 2>/dev/null | awk '{print $1}')
suffix=$([ "$PORT" = 80 ] && echo "" || echo ":$PORT")
echo
echo "Done. Open http://$host.local$suffix/${ip:+ or http://$ip$suffix/} in a browser on your network,"
echo "choose a password and follow the setup. Logs: journalctl -u jarvis -f"
