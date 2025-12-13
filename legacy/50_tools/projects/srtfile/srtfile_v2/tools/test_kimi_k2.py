#!/usr/bin/env python3
"""Kimi K2 への疎通テスト"""

import os
import sys
from openai import OpenAI

def test_kimi_k2():
    """Kimi K2 へテストメッセージを送信"""
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("Error: OPENROUTER_API_KEY is not set.", file=sys.stderr)
        return False

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    try:
        response = client.chat.completions.create(
            model="moonshotai/kimi-k2:free",
            messages=[
                {"role": "user", "content": "こんにちは、Kimi K2 さん。疎通テストです。"}
            ],
            max_tokens=100,
        )
        print("Kimi K2 Response:")
        print(response.choices[0].message.content)
        return True
    except Exception as e:
        print(f"Error connecting to Kimi K2: {e}", file=sys.stderr)
        return False

if __name__ == "__main__":
    success = test_kimi_k2()
    sys.exit(0 if success else 1)