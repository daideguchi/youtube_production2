#!/usr/bin/env python3
"""
LLM API接続テストスクリプト
"""
import os
from audio_tts_v2.tts.llm_client import query_llm_json, get_model_conf

def test_llm_connection():
    print("=== LLM API 接続テスト ===")
    
    # モデル設定の確認
    model_conf = get_model_conf("tts_primary")
    print(f"モデル設定: {model_conf}")
    
    # 環境変数の確認
    azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION")
    
    print(f"AZURE_OPENAI_API_KEY: {'設定済み' if azure_api_key else '未設定'}")
    print(f"AZURE_OPENAI_ENDPOINT: {azure_endpoint}")
    print(f"AZURE_OPENAI_API_VERSION: {azure_api_version}")
    
    # LLMへの簡単なクエリを送信
    try:
        print("\n--- LLMへのテストクエリを送信 ---")
        response = query_llm_json(
            model="tts_primary",
            api_key=azure_api_key,
            user_prompt="こんにちは",
            system_prompt="挨拶に応答してください",
            temperature=0.0
        )
        print(f"API呼び出し成功: {response}")
    except Exception as e:
        print(f"API呼び出しエラー: {e}")

if __name__ == "__main__":
    test_llm_connection()