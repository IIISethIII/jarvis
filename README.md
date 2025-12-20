# Jarvis - AIY Voice Assistant

A custom voice assistant built for the Google AIY Voice Kit (Raspberry Pi), integrated with Gemini LLM, Home Assistant, and Picovoice.

## Features
* **Wake Word Detection:** Uses `pvporcupine` for "Jarvis" wake word.
* **LLM Backend:** Google Gemini (via REST API) for natural conversation.
* **Smart Home:** Controls lights and media via Home Assistant API.
* **TTS:** Google Cloud Text-to-Speech (Journey voice) with caching/streaming optimization.
* **Privacy:** Custom VAD (Voice Activity Detection) using `pvcobra`.

## Setup

1.  **Hardware:** Raspberry Pi with Google AIY Voice Hat.
2.  **Environment Variables:**
    Create a `.env` file in the root directory:
    ```bash
    PICOVOICE_KEY="your_picovoice_key"
    GEMINI_KEY="your_gemini_api_key"
    GOOGLE_TTS_KEY="your_google_cloud_key"
    HA_TOKEN="your_home_assistant_token"
    HA_URL="[http://homeassistant.local:8123](http://homeassistant.local:8123)"
    ```
3.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Usage
Run the main module:
```bash
python3 -m jarvis.main
```

This repository includes the Materia Sound Theme by nana-4, which uses audio assets from Google licensed under CC-BY 4.0.