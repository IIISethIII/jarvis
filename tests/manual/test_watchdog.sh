#!/bin/bash
AUDIO_PID=$(pgrep -P $(pgrep -f 'python.*main.py' | head -n 1) -f 'python.*main.py')
echo "Audio Worker PID: $AUDIO_PID"
sudo kill -STOP $AUDIO_PID
sleep 6
sudo journalctl -u jarvis -n 20
