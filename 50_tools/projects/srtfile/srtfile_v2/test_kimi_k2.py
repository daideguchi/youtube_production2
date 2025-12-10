import os
from openai import OpenAI

# 環境変数からAPIキーを取得
api_key = os.environ.get('OPENROUTER_API_KEY')
if not api_key:
    raise ValueError("環境変数 OPENROUTER_API_KEY が設定されていません。")

# OpenRouterのクライアントを準備
client = OpenAI(
    base_url='https://openrouter.ai/api/v1',
    api_key=api_key,
)

# テストメッセージ
messages = [
    {'role': 'user', 'content': 'こんにちは、あなたはどんなことができますか？'}
]

# Kimi K2 APIを呼び出し
try:
    response = client.chat.completions.create(
        model='moonshotai/kimi-k2:free',
        messages=messages
    )
    
    # 結果を表示
    print("Kimi K2 API 呼び出し成功:")
    print(response.choices[0].message.content)
    
except Exception as e:
    print(f"Kimi K2 API 呼び出しエラー: {e}")
    raise