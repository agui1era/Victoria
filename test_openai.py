import os
import requests
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("OPENAI_API_KEY")
print(f"Key loaded: {api_key[:10]}...{api_key[-5:] if api_key else 'None'}")

if not api_key:
    print("❌ No API Key found.")
    exit(1)

try:
    r = requests.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=10
    )
    if r.status_code == 200:
        print("✅ API Key is VALID. Connected to OpenAI.")
    else:
        print(f"❌ API Key is INVALID. Status: {r.status_code}")
        print(f"Response: {r.text}")
except Exception as e:
    print(f"❌ Connection Error: {e}")
