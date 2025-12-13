#!/usr/bin/env python3
"""
Google Sheetsリトライ機能テストスクリプト
"""

import sys
import os
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

# 環境変数設定
os.environ['GOOGLE_SHEETS_ID'] = '1kuIX-pG7c8wBjtsIbnD0KCH-V0DIfznbfV9vP00aJew'
os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'] = '/Users/dd/projects/srtfile/srtfile-468804-826a8fecbe3c.json'

from app.sheets_io import SheetsClient, SheetsIOError

def test_sheets_connection():
    """Sheets接続・リトライ機能テスト"""
    print("=" * 60)
    print("🧪 Google Sheetsリトライ機能テスト")
    print("=" * 60)

    try:
        # クライアント作成
        print("\n1️⃣ SheetsClient初期化中...")
        client = SheetsClient(timeout=60)
        print("✅ クライアント初期化成功")

        # 接続テスト
        print("\n2️⃣ Google Sheets接続テスト中...")
        row_data = client.fetch_row(sheet_name='隠れ書庫アカシック', key='3')
        print(f"✅ 接続成功！台本3取得完了")
        print(f"   タイトル: {row_data.get('タイトル', 'N/A')}")
        print(f"   行番号: {row_data.get('_row_number', 'N/A')}")

        # 進捗更新テスト
        print("\n3️⃣ 進捗更新テスト中...")
        test_progress = "TEST@リトライ機能確認テスト"
        client.update_progress(sheet_name='隠れ書庫アカシック', key='3', progress_text=test_progress)
        print(f"✅ 進捗更新成功: '{test_progress}'")

        # 再度取得して確認
        print("\n4️⃣ 更新確認中...")
        row_data = client.fetch_row(sheet_name='隠れ書庫アカシック', key='3')
        actual_progress = row_data.get('進捗', '')
        print(f"✅ 更新確認成功")
        print(f"   進捗: {actual_progress}")

        print("\n" + "=" * 60)
        print("🎉 全テスト成功！リトライ機能が正常に動作しています")
        print("=" * 60)
        return True

    except SheetsIOError as e:
        print(f"\n❌ Sheetsエラー: {e}")
        return False
    except Exception as e:
        print(f"\n❌ 予期しないエラー: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = test_sheets_connection()
    sys.exit(0 if success else 1)
