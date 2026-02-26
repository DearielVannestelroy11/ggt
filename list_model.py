from google import genai
import os
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# List semua model yang tersedia
models = client.models.list_models()

for m in models:
    print(m)
