import subprocess
import json
import base64
import time
import wave
import threading
from aiy.leds import Leds
from jarvis.config import GOOGLE_TTS_KEY, DIM_BLUE, get_gemini_url, GEMINI_STT_URL, MY_LAT, MY_LNG
from jarvis.utils import session
from jarvis.services import sfx

def transcribe_audio(audio_bytes):
    """
    Nutzt Gemini Flash als schnellen Speech-to-Text (STT) Service.
    Ziel: Nur den Text extrahieren, keine Antwort generieren.
    """
    if not audio_bytes: return ""

    print("   [STT] Transkribiere Audio...")
    
    b64_data = base64.b64encode(audio_bytes).decode('utf-8')
    
    prompt = """
    Höre dir diese Audio-Datei an und transkribiere den gesprochenen Inhalt exakt in Text.
    - Ignoriere Hintergrundgeräusche.
    - Schreibe den vollen Satz aus.
    - Gib nur den reinen Text zurück, ohne Zeitstempel oder Einleitung.
    """

    payload = {
        "contents": [{
            "parts": [
                {"text": prompt},
                {"inline_data": {"mime_type": "audio/wav", "data": b64_data}}
            ]
        }],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 256
        }
    }

    try:
        # Wir nutzen hier dieselbe URL wie sonst auch (Flash ist schnell genug)
        response = session.post(GEMINI_STT_URL, json=payload, timeout=10)
        if response.status_code == 200:
            result = response.json()
            try:
                text = result['candidates'][0]['content']['parts'][0]['text'].strip()
                if "LEER" in text: return ""
                return text
            except: return ""
        else:
            print(f"   [STT Error] Status: {response.status_code}")
            return ""
    except Exception as e:
        print(f"   [STT Exception] {e}")
        return ""

def speak_text_gemini_old(leds, text, mood="normal", interrupt_check=None):
    if not text or not text.strip(): return
    if len(text) < 20:
        speak_text(leds, text, interrupt_check=interrupt_check)
        return
    
    style = "Sprich wie ein entspannter 24-jähriger. Locker, casual, natürlich."
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
                
                return sfx.play_blocking(tmp_file, interrupt_check=interrupt_check)

        else: print(f" [TTS Error] {response.status_code}")
    except Exception as e:
        print(f" [TTS Exception] {e}")

    return False

def speak_text_gemini(leds, text, mood="normal", interrupt_check=None):
    was_interrupted = False 

    if not text or not text.strip(): return False
    
    # Fallback für kurze Texte (optional)
    if len(text) < 20:
        return speak_text(leds, text, interrupt_check=interrupt_check)
    
    style = "Sprich wie ein entspannter 24-jähriger. Locker, casual, natürlich."
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
    
    player_command = ["aplay", "-r", "24000", "-f", "S16_LE", "-c", "1", "-t", "raw", "--buffer-time=500000"]
    player_process = None

    try:
        t_start = time.time() # Zeitmessung Start

        player_process = subprocess.Popen(player_command, stdin=subprocess.PIPE)
        
        response = session.post(url, json=payload, stream=True, timeout=60)
        
        if response.status_code == 200:
            for line in response.iter_lines():
                if interrupt_check and interrupt_check():
                    print("   [TTS] Unterbrochen.")
                    was_interrupted = True # Merken, dass wir unterbrochen wurden
                    break # Schleife verlassen
                
                if line:
                    decoded = line.decode('utf-8').strip()
                    if decoded.startswith("data:"):
                        try:
                            json_str = decoded[5:].strip()
                            if not json_str: continue
                            data = json.loads(json_str)
                            cands = data.get('candidates', [])
                            if cands:
                                cand = cands[0]
                                
                                # 1. Audio verarbeiten
                                parts = cand.get('content', {}).get('parts', [])
                                if parts:
                                    b64 = parts[0].get('inlineData', {}).get('data')
                                    if b64:
                                        if not first_chunk_received:
                                            # ... Latenz Logik ...
                                            first_chunk_received = True
                                        
                                        audio_chunk = base64.b64decode(b64)
                                        player_process.stdin.write(audio_chunk)
                                        player_process.stdin.flush()
                                
                                if cand.get('finishReason'):
                                    break
                        except Exception: pass
        else:
            print(f" [TTS Error] {response.status_code}")

    except Exception as e:
        print(f" [TTS Exception] {e}")
        
    finally:
        if player_process:
            try:
                player_process.stdin.close()
                # Wenn unterbrochen wurde, töten wir den Prozess sofort, damit der Ton stoppt
                if was_interrupted:
                    player_process.terminate()
                else:
                    player_process.wait(timeout=2)
            except:
                player_process.terminate()

    return was_interrupted

def speak_text_old(leds, text, interrupt_check=None):
    if not text or not text.strip(): return False # Rückgabe False (nicht unterbrochen)
    print("   Jarvis: " + text)
    leds.update(Leds.rgb_on(DIM_BLUE))
    
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_KEY}"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": "de-DE", "name": "de-DE-Journey-D"},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000}
    }
    
    try:
        r = session.post(url, json=payload, timeout=30)
        if r.status_code == 200:
            content = r.json().get('audioContent')
            if content:
                audio_binary = base64.b64decode(content)
                # Speichern als WAV mit Header (Pygame mag raw PCM ohne Header oft nicht)
                tmp_file = "/tmp/tts_standard.wav"
                with wave.open(tmp_file, "wb") as f:
                    f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000)
                    f.writeframes(audio_binary)
                
                return sfx.play_blocking(tmp_file, interrupt_check=interrupt_check)

        else:
            print(f" [TTS API Error] Status: {r.status_code} | Response: {r.text}")
    except Exception as e:
        print(f" [TTS Exception] {e}")

    return False

def speak_text(leds, text, interrupt_check=None):
    if not text or not text.strip(): return False
    
    print("   Jarvis: " + text)
    leds.update(Leds.rgb_on(DIM_BLUE))
    
    url = f"https://texttospeech.googleapis.com/v1/text:synthesize?key={GOOGLE_TTS_KEY}"
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": "de-DE", "name": "de-DE-Journey-D"}, # faster voice: "voice": {"languageCode": "de-DE", "name": "de-DE-Neural2-D", "ssmlGender": "MALE"},
        "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000}
    }
    
    was_interrupted = False
    player_process = None

    try:
        r = session.post(url, json=payload, timeout=10)
        
        if r.status_code == 200:
            data = r.json()
            content = data.get('audioContent')
            
            if content:
                audio_binary = base64.b64decode(content)
                
                cmd = ["aplay", "-t", "raw", "-f", "S16_LE", "-r", "24000", "-c", "1", "-q", "--buffer-time=500000"]
                player_process = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                
                def writer():
                    try:
                        player_process.stdin.write(audio_binary)
                    except Exception:
                        pass 
                    finally:
                        try:
                            player_process.stdin.close()
                        except: pass

                t = threading.Thread(target=writer)
                t.daemon = True # Thread stirbt, wenn Hauptprogramm endet
                t.start()

                # Jetzt können wir SOFORT prüfen, auch wenn write() noch beschäftigt ist
                while player_process.poll() is None:
                    if interrupt_check and interrupt_check():
                        player_process.terminate()
                        was_interrupted = True
                        print("   [TTS] Unterbrochen.")
                        break
                    time.sleep(0.05)

        else:
            print(f" [TTS API Error] Status: {r.status_code}")
            
    except Exception as e:
        print(f" [TTS Exception] {e}")

    finally:
        if player_process and player_process.poll() is None:
            player_process.terminate()
            
    return was_interrupted

def perform_google_search_internal(query):
    """Performs search via a separate Gemini call."""
    print(f"  [Internal] Searching: {query}")
    payload = {
        "contents": [{"parts": [{"text": query}]}],
        "tools": [{"googleSearch": {}}]
    }
    try:
        response = session.post(get_gemini_url(), json=payload, timeout=30)
        if response.status_code == 200:
            result = response.json()
            try:
                return result['candidates'][0]['content']['parts'][0]['text']
            except:
                return "Ich konnte online keine Informationen finden."
        return f"Fehler bei der Suche: {response.status_code}"
    except Exception as e:
        return f"Verbindungsfehler: {e}"
    
def perform_maps_search(query):
    """Führt eine Google Maps Suche mit explizitem Standort-Kontext aus."""
    print(f"  [Maps] Searching: {query}")

    payload = {
        "contents": [{"parts": [{"text": query}]}],
        "tools": [{"googleMaps": {}}],
        "toolConfig": {
            "retrievalConfig": {
                "latLng": {
                    "latitude": MY_LAT,
                    "longitude": MY_LNG
                }
            }
        }
    }
    
    try:
        response = session.post(get_gemini_url(), json=payload, timeout=30)
        
        if response.status_code == 200:
            result = response.json()
            try:
                return result['candidates'][0]['content']['parts'][0]['text']
            except (KeyError, IndexError):
                return "Ich konnte dazu nichts auf der Karte finden."
        
        print(f"  [Maps Error] {response.status_code}")
        return f"Fehler bei der Suche: {response.status_code}"
        
    except Exception as e:
        print(f"  [Maps Exception] {e}")
        return "Verbindungsfehler."