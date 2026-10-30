import os, requests
from dotenv import load_dotenv

load_dotenv('/home/pi/jarvis/.env')
key = os.getenv('GOOGLE_TTS_KEY')
print("Key exists:", bool(key))

r = requests.post(
    f'https://texttospeech.googleapis.com/v1/text:synthesize?key={key}', 
    json={
        'input': {'text': 'test'}, 
        'voice': {'languageCode': 'de-DE', 'name': 'de-DE-Journey-D'}, 
        'audioConfig': {'audioEncoding': 'LINEAR16'}
    }
)
print(r.status_code)
print(r.text)
