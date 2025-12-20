import json
import base64
import time
import wave
from aiy.leds import Leds
from jarvis.config import GOOGLE_TTS_KEY, DIM_BLUE, GEMINI_URL
from jarvis.utils import session
from jarvis.services import sfx

def speak_text_gemini(leds, text, mood="normal"):
    if not text or not text.strip(): return
    if len(text) < 20:
        speak_text(leds, text)
        return
    
    style = "Sprich wie ein entspannter 24-jähriger Münchner. Locker, casual, natürlich."
    print(f"   Jarvis (Gemini): {text[:50]}...")
    leds.update(Leds.rgb_on(DIM_BLUE))
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-tts:streamGenerateContent?key={GOOGLE_TTS_KEY}&alt=sse"
    prompt = f"{style}: {text}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": "Umbriel"}}}
        }
    }
    
    all_audio_data = bytearray() # Wir sammeln erst alles

    try:
        response = session.post(url, json=payload, stream=True, timeout=60)
        if response.status_code == 200:
            for line in response.iter_lines():
                if line:
                    decoded = line.decode('utf-8').strip()
                    if decoded.startswith("data:"):
                        try:
                            json_str = decoded[5:].strip()
                            if not json_str: continue
                            data = json.loads(json_str)
                            cands = data.get('candidates', [])
                            if cands:
                                parts = cands[0].get('content', {}).get('parts', [])
                                if parts:
                                    b64 = parts[0].get('inlineData', {}).get('data')
                                    if b64:
                                        all_audio_data.extend(base64.b64decode(b64))
                        except: pass
            
            # Jetzt abspielen via sfx (Pygame)
            if all_audio_data:
                tmp_file = "/tmp/tts_gemini.wav"
                with wave.open(tmp_file, "wb") as f:
                    f.setnchannels(1)
                    f.setsampwidth(2) # 16 bit
                    f.setframerate(24000)
                    f.writeframes(all_audio_data)
                
                sfx.play_blocking(tmp_file)

        else: print(f" [TTS Error] {response.status_code}")
    except Exception as e:
        print(f" [TTS Exception] {e}")

def speak_text(leds, text, stream=None):
    if not text or not text.strip(): return
    print("   Jarvis: " + text)
    leds.update(Leds.rgb_on(DIM_BLUE))
    
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_KEY}"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": "de-DE", "name": "de-DE-Journey-D"},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000}
    }
    
    try:
        r = session.post(url, json=payload, timeout=10)
        if r.status_code == 200:
            content = r.json().get('audioContent')
            if content:
                audio_binary = base64.b64decode(content)
                # Speichern als WAV mit Header (Pygame mag raw PCM ohne Header oft nicht)
                tmp_file = "/tmp/tts_standard.wav"
                with wave.open(tmp_file, "wb") as f:
                    f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000)
                    f.writeframes(audio_binary)
                
                sfx.play_blocking(tmp_file)
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