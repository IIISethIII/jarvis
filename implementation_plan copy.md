# Implementation Plan: Jarvis V3 Rewrite Strategy

This plan outlines the exact logistics, workflow, and build order for rewriting Jarvis from the ground up using Antigravity, while minimizing downtime and hardware headaches.

## User Review Required

> [!IMPORTANT]  
> **Development Environment**: To build this efficiently, we must develop **directly on the Raspberry Pi**. I (Antigravity) cannot test ALSA audio configurations or AIY LED hardware from your local Windows machine. You must use **VS Code Remote-SSH** to open the Raspberry Pi workspace, allowing me to run terminal commands directly on the Pi.

## 1. Logistics & Setup Questions Answered

### Should I make a new repo?
**Yes.** Create a completely new folder/repository named `jarvis_v3`. Do not overwrite the `jarvis_v2` folder. Starting fresh ensures we don't accidentally break your working assistant, and it allows us to drop years of legacy code/files that you no longer need.

### Should I take the old Jarvis offline?
**No, leave it running for now!** 
We will build and test V3 in isolation using temporary test scripts. Only when V3 reaches Phase 3 (Conversational) will we need to `sudo systemctl stop jarvis` so that V3 can take exclusive control of the microphone. This means you won't lose your assistant while we spend days rewriting it.

### Should I make a new python environment?
**Yes.** Inside your new `jarvis_v3` folder, you will run `python3 -m venv venv`. We want a perfectly clean slate. We will only `pip install` the exact modern dependencies we need (like `google-genai`, `pyaudio`, and the local `/home/pi/aiyprojects`), completely stripping out old baggage like Picovoice.

### How to connect to the Pi?
1. Open VS Code on your Windows PC.
2. Install the **Remote - SSH** extension.
3. Connect to your Pi (e.g., `ssh pi@192.168.x.x`).
4. Open the `/home/pi/jarvis_v3` folder in VS Code.
5. Launch Antigravity inside that Remote workspace. 
*By doing this, whenever I write a file or run a terminal command, I am doing it directly on the Pi, which is mandatory for testing hardware.*

---

## 2. Proposed Build Order & Testing Strategy

The biggest mistake in a rewrite is trying to build the giant `main.py` loop all at once. We will build this in strictly isolated, testable layers.

### Phase 1: The Audio Foundation
**Goal**: Prove that Python can talk to the PS3 Eye and AIY Speaker without crashing.
- **Build**: We will write a tiny `core/audio.py` script that manages the `multiprocessing.Queue`.
- **Test**: I will write a `test_echo.py` script. You will run it, speak into the mic, and the Pi should immediately play your voice back to you. If this works, the ALSA `.asoundrc` is perfect.

### Phase 2: Wake Word & Hardware
**Goal**: Prove the Pi can hear its name and blink its lights.
- **Build**: We implement `openwakeword` and the `BonnetHardware` LED proxy.
- **Test**: I will write a `test_wakeword.py`. You run it. It sits silently until you say "Jarvis", at which point it prints "DETECTED" and the LEDs flash blue. 

### Phase 3: The LLM Wrapper (The Brain)
**Goal**: Have a fluid, low-latency conversation (No Smart Home yet).
- **Build**: We implement the `VoiceAssistantProvider` adapter and the `GeminiLiveProvider` wrapper we designed earlier. We connect Phase 1 (Audio) directly to the WebSockets.
- **Test**: *At this point, we stop the V2 service.* You run `test_conversation.py`. You say "Jarvis", the LEDs light up, and you have a normal, fluid voice conversation with Gemini using the Fast Brain.

### Phase 4: Tools & Home Assistant
**Goal**: Give Jarvis back his hands.
- **Build**: We port over the `ha.py` wrapper, but we clean it up drastically. We configure the Gemini Live API wrapper to intercept tool calls.
- **Test**: You run the main loop, say "Jarvis, turn on the living room light," and verify that the music volume dips, the light turns on, and the voice response is natural.

### Phase 5: Memory & Final Polish
**Goal**: Restore long-term context and background automation.
- **Build**: We re-integrate the `mcp.py` server connection and port over the `Mem0` vector database integration and `routine.py` nightly tracker.
- **Test**: We wait for the 4:00 AM cron job to trigger and verify that it successfully injects your daily habits into the core memory.

---

## 3. Your Next Steps
If you agree with this strategy, here is what you need to do manually right now:
1. SSH into your Raspberry Pi and run: `mkdir /home/pi/jarvis_v3`
2. Open that folder in VS Code using **Remote-SSH**.
3. Open a terminal in VS Code and run: `python3 -m venv venv`
4. Start a **new chat with Antigravity** inside that remote workspace, point me to the `migration_guide.md` we just created, and say: *"Let's begin Phase 1 of the rewrite!"*
