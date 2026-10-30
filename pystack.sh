#!/bin/bash
/home/pi/jarvis_v3/venv/bin/python3 -m pip install pystack-debugger
MAIN_PID=$(pgrep -f 'python.*main.py' | head -n 1)
sudo /home/pi/jarvis_v3/venv/bin/pystack $MAIN_PID
