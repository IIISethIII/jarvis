import os
from google import genai
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))

for m in client.models.list():
    if 'bidiGenerateContent' in m.supported_actions:
        print(f"Supported Live Model: {m.name}")
