#!/bin/bash
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
$REPO/venv/bin/python3 -m pip install pystack-debugger
MAIN_PID=$(pgrep -f 'python.*main.py' | head -n 1)
sudo $REPO/venv/bin/pystack $MAIN_PID
