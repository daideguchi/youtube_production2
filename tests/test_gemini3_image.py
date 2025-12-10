
import sys
import os
from pathlib import Path
import logging

# プロジェクトルートをパスに追加
sys.path.append(str(Path("commentary_02_srt2images_timeline").resolve()))

try:
    from src.core.config import config
    from google import genai
    from google.genai import types
except ImportError as e:
    print(f"Error importing modules: {e}")
    sys.exit(1)

def test_gemini_flash_image():
    api_key = config.GEMINI_API_KEY
    if not api_key:
        print("Error: GEMINI_API_KEY not found in config.")
        return

    model_name = "gemini-2.5-flash-image"
    print(f"Testing image generation with model: {model_name}")

    client = genai.Client(api_key=api_key)
    
    prompt = "A cinematic shot of a futuristic city with glowing neon lights, digital art style."
    
    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"]
            )
        )
        
        if response.candidates:
             for cand in response.candidates:
                if cand.content and cand.content.parts:
                    for part in cand.content.parts:
                        if part.inline_data:
                            print("Success! Image data received.")
                            return
        
        print("Response received but no image data found.")
        print(response)

    except Exception as e:
        print(f"Error during generation: {e}")

if __name__ == "__main__":
    test_gemini_flash_image()
