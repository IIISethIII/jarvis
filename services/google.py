# jarvis/services/google.py
import json
import base64
import subprocess
import time
from aiy.leds import Leds
from jarvis.config import GOOGLE_TTS_KEY, DIM_BLUE, GEMINI_URL
from jarvis.utils import session

def speak_text_gemini(leds, text, mood="normal"):
    if not text or not text.strip():
        return

    if len(text) < 20:
        speak_text(leds, text)
        return
    
    style_instruction = """
    Sprich wie ein entspannter 24-jähriger Münchner. Locker und casual, 
    wie ein Mitbewohner der dir hilft. Nicht steif oder roboterhaft - 
    einfach natürlich und menschlich.
    """
        
    print(f"   Jarvis (Gemini-TTS): {text}")
    leds.update(Leds.rgb_on(DIM_BLUE))
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:streamGenerateContent?key={GOOGLE_TTS_KEY}&alt=sse"
    
    prompt = f"{style_instruction}: {text}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": "Umbriel"
                    }
                }
            }
        }
    }
    
    player_process = None
    
    try:
        start_req = time.time()
        # 1. REQUEST: stream=True
        # chunk_size=None hilft requests, Daten schneller durchzureichen
        response = session.post(url, json=payload, stream=True, timeout=60)
        
        if response.status_code == 200:
            # 2. PLAYER STARTEN
            # bufsize=0: WICHTIG! Deaktiviert den Python-Buffer für die Pipe.
            # aplay Parameter für weniger Latenz:
            # --buffer-time=50000 (50ms Buffer statt 500ms Standard)
            player_cmd = ["aplay", "-t", "raw", "-f", "S16_LE", "-r", "24000", "-c", "1", "-q", "--buffer-time=50000"]
            player_process = subprocess.Popen(player_cmd, stdin=subprocess.PIPE, bufsize=0)

            first_chunk_received = False

            # 3. STREAM VERARBEITEN
            # Wir nutzen iter_lines() da SSE textbasiert ist (zeilenweise)
            for line in response.iter_lines():
                if line:
                    decoded_line = line.decode('utf-8').strip()
                    
                    if decoded_line.startswith("data:"):
                        try:
                            json_str = decoded_line[5:].strip()
                            if not json_str: continue
                            
                            data = json.loads(json_str)
                            
                            candidates = data.get('candidates', [])
                            if candidates:
                                parts = candidates[0].get('content', {}).get('parts', [])
                                if parts:
                                    inline_data = parts[0].get('inlineData', {})
                                    b64_audio = inline_data.get('data')
                                    
                                    if b64_audio:
                                        # Debugging: Wann kam das erste Byte?
                                        if not first_chunk_received:
                                            lat = time.time() - start_req
                                            print(f" [DEBUG] Erste Audio-Daten nach: {lat:.2f}s")
                                            first_chunk_received = True

                                        audio_bytes = base64.b64decode(b64_audio)
                                        
                                        # Direktes Schreiben ohne Puffer
                                        player_process.stdin.write(audio_bytes)
                                        # flush() ist bei bufsize=0 technisch nicht nötig, schadet aber nicht
                                        player_process.stdin.flush() 
                                        
                        except Exception as parse_error:
                            print(f" [Stream Parse Error] {parse_error}")

        else:
            print(f" [TTS Error] {response.status_code}: {response.text}")

    except Exception as e:
        print(f" [TTS Exception] {e}")
        
    finally:
        if player_process:
            try:
                player_process.stdin.close()
                player_process.wait(timeout=2)
            except:
                pass

def speak_text(leds, text, stream=None):
    if not text or not text.strip(): return
    print("   Jarvis: " + text)
    
    leds.update(Leds.rgb_on(DIM_BLUE))
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_KEY}"
    
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": "de-DE", "name": "de-DE-Journey-D"},
        # ÄNDERUNG 1: LINEAR16 statt MP3 für aplay Kompatibilität
        # sampleRateHertz ist optional, aber 24000 sichert hohe Qualität
        "audioConfig": {"audioEncoding": "LINEAR16", "speakingRate": 1.1, "sampleRateHertz": 24000}
    }
    
    try:
        response = session.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            audio_content = response.json().get('audioContent')
            if audio_content:
                audio_binary = base64.b64decode(audio_content)
                try:
                    # ÄNDERUNG 2: aplay statt mpg123
                    # Wir übergeben keine Parameter (-r, -f), da die API einen WAV-Header mitschickt.
                    # aplay liest diesen Header und stellt sich automatisch richtig ein.
                    # -q: Quiet mode (weniger Konsolen-Output)
                    # -: Input von Stdin
                    p = subprocess.Popen(["aplay", "-q", "-"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
                    p.communicate(input=audio_binary)
                except Exception as e:
                    print(f" [Audio Playback Error] {e}")
        else:
            print(f" [TTS Error] {response.status_code}")
    except Exception as e:
        print(f" [TTS Exception] {e}")

def perform_google_search_internal(query):
    """Performs search via a separate Gemini call."""
    print(f"  [Internal] Searching: {query}")
    payload = {
        "contents": [{"parts": [{"text": query}]}],
        "tools": [{"googleSearch": {}}]
    }
    try:
        response = session.post(GEMINI_URL, json=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            try:
                return result['candidates'][0]['content']['parts'][0]['text']
            except:
                return "Ich konnte online keine Informationen finden."
        return f"Fehler bei der Suche: {response.status_code}"
    except Exception as e:
        return f"Verbindungsfehler: {e}"