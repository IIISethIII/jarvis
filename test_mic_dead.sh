#!/bin/bash
# test_mic_dead.sh

echo "========================================"
echo " Jarvis Mic Deadlock Detector"
echo "========================================"

# Find the main Jarvis process
MAIN_PID=$(pgrep -f "python.*main.py" | head -n 1)

if [ -z "$MAIN_PID" ]; then
    echo "Jarvis is not running."
    exit 1
fi

echo "Main Jarvis PID: $MAIN_PID"

# Find the audio worker process
AUDIO_PID=$(pgrep -f "python.*main.py" | tail -n 1)

echo "Measuring CPU usage over 2 seconds..."
CPU_USAGE=$(top -b -n 2 -p $MAIN_PID -d 1 | tail -1 | awk '{print $9}')
AUDIO_CPU=$(top -b -n 2 -p $AUDIO_PID -d 1 | tail -1 | awk '{print $9}')

echo "Main Process CPU: $CPU_USAGE%"
echo "Audio Worker CPU: $AUDIO_CPU%"

# The threshold for queue backup deadlock is Main > 95% and Audio < 10%
IS_DEADLOCK=$(awk -v m="$CPU_USAGE" -v a="$AUDIO_CPU" 'BEGIN { if (m > 95.0 && a < 10.0) print "1"; else print "0" }')

if [ "$IS_DEADLOCK" = "1" ]; then
    echo ""
    echo "🚨 DEADLOCK DETECTED! 🚨"
    echo "The main loop is spinning at 100% CPU trying to process an infinitely backed-up audio queue!"
    echo "This happens because openwakeword processes chunks slower than real-time when its buffers are full."
    echo "The Rapid-Fire Watchdog didn't catch this because the chunks are processed slowly (taking 40ms each), so they don't look like an ALSA crash, but they permanently delay the microphone input."
    echo ""
    echo "Running pystack to prove the thread is stuck in openwakeword:"
    sudo /home/pi/jarvis_v3/venv/bin/pystack $MAIN_PID | grep -A 5 "_streaming_melspectrogram"
    exit 0
else
    echo "Mic is healthy and real-time. Queue is not backed up."
    exit 1
fi
