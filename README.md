# Jarvis - AIY Voice Assistant

A custom voice assistant built for the Google AIY Voice Kit (Raspberry Pi), integrated with Gemini LLM, Home Assistant, and Picovoice.

## Features
* **Wake Word Detection:** Uses `pvporcupine` for "Jarvis" wake word detection.
* **LLM Backend:** Google Gemini (via REST API) for natural conversation.
* **RAG Memory:** Local vector-based memory system to provide persistent context to the LLM.
* **Smart Home:** Controls lights and media via Home Assistant API, including automatic volume ducking when Jarvis is active.
* **TTS & Interruption:** Google Cloud Text-to-Speech with "Barge-in" support (stops speaking when the wake word is detected).
* **Multiprocessing:** Isolated audio worker process to ensure stability and low-latency detection.
* **Privacy:** Custom VAD (Voice Activity Detection) using `pvcobra`.

## Setup

1.  **Hardware:** Raspberry Pi with Google AIY Voice Hat.
2.  **Environment Variables:**
    Create a `.env` file in the root directory:
    ```bash
    PICOVOICE_KEY="your_picovoice_key"
    GEMINI_KEY="your_gemini_api_key"
    GEMINI_MODEL="gemini-2.5-flash" # Optional
    GOOGLE_TTS_KEY="your_google_cloud_key"
    HA_TOKEN="your_home_assistant_token"
    HA_URL="[http://homeassistant.local:8123](http://homeassistant.local:8123)"
    ```
3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    *Note: Ensure `libasound2-dev` and `portaudio19-dev` are installed on your system for `pyaudio`.*

## Usage
Run the main module:
```bash
python3 -m jarvis.main