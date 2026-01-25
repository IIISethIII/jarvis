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

1.  **Hardware:** Raspberry Pi Zero W with Google AIY Voice Hat (v2) and PS3 Eye USB Microphone.

2.  **Hardware & Power Optimization (Crucial for Pi Zero):**
    Edit `/boot/cmdline.txt` and add these parameters to the end of the line (do not create a new line):
    ```text
    dwc_otg.fiq_enable=0 dwc_otg.fiq_fsm_enable=0 dwc_otg.speed=1 dwc_otg.nak_holdoff=1 usbcore.autosuspend=-1
    ```
    Edit `/boot/config.txt` and ensure the following settings are active to free up RAM and provide enough power to the USB mic:
    ```text
    [all]
    start_x=0             # Disable camera firmware
    gpu_mem=16            # Minimum GPU RAM, more for System/Python
    max_usb_current=1     # Unlock USB power limit for the PS3 Eye
    dtoverlay=spi0-1cs,cs0_pin=7
    ```

3.  **ALSA Configuration:**
    Use the `dsnoop` and `dmix` configuration in `/etc/asound.conf` or `~/.asoundrc` to handle buffering and multi-app audio access. Recommended `period_size` for PS3 Eye (48kHz) to match Porcupine (16kHz) is `1536`.

4.  **Environment Variables:**
    Create a `.env` file in the root directory:
    ```bash
    PICOVOICE_KEY="your_picovoice_key"
    GEMINI_KEY="your_gemini_api_key"
    GEMINI_MODEL="gemini-2.0-flash"
    GOOGLE_TTS_KEY="your_google_cloud_key"
    HA_TOKEN="your_home_assistant_token"
    HA_URL="[http://homeassistant.local:8123](http://homeassistant.local:8123)"
    ```

5.  **Install Dependencies:**
    ```bash
    pip install -r requirements.txt
    ```
    *Note: Ensure `libasound2-dev` and `portaudio19-dev` are installed for `pyaudio`.*

## Usage
Run the main module:
```bash
python3 -m jarvis.main