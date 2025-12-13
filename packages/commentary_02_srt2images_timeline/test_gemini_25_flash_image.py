#!/usr/bin/env python3
"""
Gemini 2.5 Flash Image モデルの動作確認テスト
"""
import sys
import os
from pathlib import Path
import tempfile
import logging

# プロジェクトルートをパスに追加
project_root = Path(__file__).resolve().parent
sys.path.insert(0, str(project_root / "src"))

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def test_gemini_25_flash_image():
    """Gemini 2.5 Flash Imageモデルが正常に動作するかテスト"""
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        logger.error("google-genai SDKがインストールされていません。")
        logger.error("インストール: pip install google-genai")
        return False
    
    # APIキーの読み込み
    try:
        from src.core.config import config
        api_key = config.GEMINI_API_KEY
        logger.info("APIキーを読み込みました")
    except Exception as e:
        logger.error(f"APIキーの読み込みに失敗: {e}")
        return False
    
    # モデル名
    model = "gemini-2.5-flash-image"
    logger.info(f"テスト対象モデル: {model}")
    
    # テスト用プロンプト
    test_prompt = "A beautiful sunset over a calm ocean, 16:9 aspect ratio"
    
    try:
        client = genai.Client(api_key=api_key)
        logger.info("Geminiクライアントを作成しました")
        
        # 画像生成設定
        config_kwargs = {"response_modalities": ["IMAGE", "TEXT"]}
        generate_config = types.GenerateContentConfig(**config_kwargs)
        
        logger.info(f"画像生成を開始します... (プロンプト: {test_prompt})")
        
        # API呼び出し
        resp = client.models.generate_content(
            model=model,
            contents=[test_prompt],
            config=generate_config
        )
        
        logger.info("API呼び出しが成功しました")
        
        # レスポンスの解析
        image_data = None
        if hasattr(resp, 'candidates') and resp.candidates:
            logger.info(f"レスポンスに{len(resp.candidates)}個の候補があります")
            for i, cand in enumerate(resp.candidates):
                content = getattr(cand, 'content', None)
                parts = getattr(content, 'parts', []) if content else []
                logger.info(f"候補 {i+1}: {len(parts)}個のパーツ")
                for j, part in enumerate(parts):
                    if hasattr(part, 'inline_data') and part.inline_data:
                        logger.info(f"  パーツ {j+1}: 画像データを検出")
                        data = getattr(part.inline_data, 'data', None)
                        if data:
                            import base64
                            if isinstance(data, (bytes, bytearray)):
                                b = bytes(data)
                                if len(b) >= 5 and all(32 <= x <= 122 for x in b[:16]) and not (b and (b[0] in (0x89, 0xFF))):
                                    image_data = base64.b64decode(b)
                                else:
                                    image_data = b
                            elif isinstance(data, str):
                                image_data = base64.b64decode(data)
                            if image_data:
                                logger.info(f"  画像データを取得しました (サイズ: {len(image_data)} bytes)")
                                break
                    elif hasattr(part, 'text'):
                        text = getattr(part, 'text', '')
                        if text:
                            logger.info(f"  パーツ {j+1}: テキストコンテンツ: {text[:100]}...")
                if image_data:
                    break
        
        if image_data:
            # 一時ファイルに保存して確認
            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp:
                tmp.write(image_data)
                tmp_path = tmp.name
            
            logger.info(f"✅ 成功: 画像を生成しました ({len(image_data)} bytes)")
            logger.info(f"   保存先: {tmp_path}")
            
            # ファイルサイズの確認
            file_size = os.path.getsize(tmp_path)
            logger.info(f"   ファイルサイズ: {file_size} bytes")
            
            # 画像の基本情報を確認
            try:
                from PIL import Image
                img = Image.open(tmp_path)
                logger.info(f"   画像サイズ: {img.size[0]}x{img.size[1]}")
                logger.info(f"   画像フォーマット: {img.format}")
            except Exception as e:
                logger.warning(f"   画像情報の取得に失敗: {e}")
            
            # クリーンアップ
            os.unlink(tmp_path)
            
            return True
        else:
            logger.error("❌ 失敗: レスポンスに画像データが含まれていません")
            # テキストレスポンスを確認
            text_content = getattr(resp, 'text', '') or "No text content"
            logger.info(f"   テキストレスポンス: {text_content[:500]}")
            return False
            
    except Exception as e:
        error_msg = str(e)
        logger.error(f"❌ エラーが発生しました: {error_msg}")
        
        # エラーの種類を判定
        if "429" in error_msg or "RESOURCE_EXHAUSTED" in error_msg:
            logger.error("   レート制限またはクォータ制限の可能性があります")
        elif "404" in error_msg or "NOT_FOUND" in error_msg:
            logger.error("   モデルが見つかりません。モデル名が正しいか確認してください")
        elif "401" in error_msg or "UNAUTHENTICATED" in error_msg:
            logger.error("   認証エラー。APIキーが正しいか確認してください")
        elif "500" in error_msg or "INTERNAL" in error_msg:
            logger.error("   サーバーエラー。しばらく待ってから再試行してください")
        
        return False

if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Gemini 2.5 Flash Image 動作確認テスト")
    logger.info("=" * 60)
    
    success = test_gemini_25_flash_image()
    
    logger.info("=" * 60)
    if success:
        logger.info("✅ テスト成功: Gemini 2.5 Flash Imageは正常に動作しています")
        sys.exit(0)
    else:
        logger.error("❌ テスト失敗: Gemini 2.5 Flash Imageの動作に問題があります")
        sys.exit(1)


