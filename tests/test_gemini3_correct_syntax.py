
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

def test_gemini3_image_v1alpha():
    api_key = config.GEMINI_API_KEY
    if not api_key:
        print("Error: GEMINI_API_KEY not found in config.")
        return

    model_name = "gemini-3-pro-image-preview"
    print(f"Testing image generation with model: {model_name} (v1alpha / ImageConfig)")

    # v1alpha を明示的に指定
    client = genai.Client(api_key=api_key, http_options={'api_version': 'v1alpha'})
    
    prompt = "A cinematic shot of a futuristic city with glowing neon lights, digital art style."
    
    try:
        # 新しいドキュメントに沿ったリクエスト形式
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                image_config=types.ImageConfig(
                    aspect_ratio="16:9",
                    image_size="2K" # 4Kだと重そうなので一旦2Kでテスト
                ),
                # response_modalities=["IMAGE"] # ドキュメントの例にはないが、必要なら追加
            )
        )
        
        # レスポンス解析（ドキュメントの例に準拠）
        image_parts = [part for part in response.parts if part.inline_data]

        if image_parts:
            print(f"Success! Generated {len(image_parts)} images.")
            # 保存テストは省略（データが取れればOK）
            return
        
        print("Response received but no image parts found.")
        print(response)

    except Exception as e:
        print(f"Error during generation: {e}")

if __name__ == "__main__":
    test_gemini3_image_v1alpha()
