#!/bin/bash
MAIN_PID=$(pgrep -f 'python.*main.py' | head -n 1)
sudo gdb -p $MAIN_PID -batch -ex 'set pagination off' -ex 'thread apply all bt'
