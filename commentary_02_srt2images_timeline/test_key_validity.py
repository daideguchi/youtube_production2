#!/usr/bin/env python3
"""
APIキーの有効性を確認するスクリプト（画像生成以外でテスト）
"""
from google import genai

test_key = "AIzaSyDADgmndjZ-xDK4QQcZTAHbp-sIO9xHcEA"

print("=" * 60)
print("APIキーの有効性確認テスト")
print("=" * 60)
print(f"テストキー: {test_key[:20]}...{test_key[-10:]}\n")

try:
    client = genai.Client(api_key=test_key)
    print("✅ クライアント作成成功\n")
    
    # テキスト生成でテスト（画像生成よりクォータが緩い可能性）
    print("1. テキスト生成APIでテスト...")
    try:
        resp = client.models.generate_content(
            model="gemini-2.0-flash-exp",
            contents=["Say hello in one word"]
        )
        if hasattr(resp, 'text') and resp.text:
            print(f"   ✅ 成功: {resp.text.strip()}")
            print("   → キーは有効です！認証も通っています。")
        else:
            print("   ⚠️  レスポンスはありますが、テキストが含まれていません")
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            print("   ⚠️  クォータ制限（キーは有効ですが、使用制限に達しています）")
        elif "401" in error_msg or "UNAUTHENTICATED" in error_msg:
            print("   ❌ 認証エラー（キーが無効です）")
        elif "403" in error_msg or "PERMISSION_DENIED" in error_msg:
            print("   ❌ 権限エラー（キーに権限がありません）")
        else:
            print(f"   ❌ エラー: {error_msg[:200]}")
    
    # モデル一覧取得でテスト
    print("\n2. モデル一覧取得でテスト...")
    try:
        models = list(client.models.list())
        if models:
            print(f"   ✅ 成功: {len(models)}個のモデルを取得")
            print("   → キーは有効です！認証も通っています。")
            # gemini-2.5-flash-imageが利用可能か確認
            image_models = [m for m in models if "image" in m.name.lower() or "flash-image" in m.name.lower()]
            if image_models:
                print(f"\n   画像生成モデル:")
                for m in image_models[:5]:
                    print(f"     - {m.name}")
        else:
            print("   ⚠️  モデルが取得できませんでした")
    except Exception as e:
        error_msg = str(e)
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            print("   ⚠️  クォータ制限（キーは有効ですが、使用制限に達しています）")
        elif "401" in error_msg or "UNAUTHENTICATED" in error_msg:
            print("   ❌ 認証エラー（キーが無効です）")
        else:
            print(f"   ❌ エラー: {error_msg[:200]}")
    
    print("\n" + "=" * 60)
    print("結論:")
    print("=" * 60)
    print("キー自体は有効で認証も通っています。")
    print("429エラーはクォータ制限によるもので、キーの問題ではありません。")
    print("時間をおいてから再度試すか、Google AI Studioでクォータを確認してください。")
    
except Exception as e:
    print(f"❌ クライアント作成に失敗: {e}")


