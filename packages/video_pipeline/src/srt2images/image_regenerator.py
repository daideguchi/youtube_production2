#!/usr/bin/env python3
"""
Individual Image Regenerator
個別画像の再生成と差し替え機能
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from PIL import Image
import tempfile
import shutil

from .nanobanana_client import _run_direct, _convert_to_16_9

logger = logging.getLogger(__name__)

_SHOT_BLOCK_RE = re.compile(r"Shot:.*?(?:\n\n|$)", re.DOTALL)


def _replace_shot_block(prompt: str, shot_block: str) -> str:
    """
    Replace the "Shot:" guidance block inside an existing prompt.

    Many runs store a fully-rendered `cue.prompt` that includes a "Shot:" paragraph.
    When `cue.diversity_note` (which also starts with "Shot:") is updated later, the
    stored `cue.prompt` can become stale. For regeneration tools, we prefer the latest
    `cue.diversity_note` to avoid accidentally producing face close-ups.
    """
    if not prompt or "Shot:" not in prompt:
        return prompt
    sb = shot_block.strip()

    # Some historical prompts accidentally contain duplicated Shot blocks.
    # Replace all Shot blocks, and keep a blank line separator when there is following text.
    def _repl(m: re.Match[str]) -> str:
        if m.end() < len(prompt):
            return sb + "\n\n"
        return sb

    return _SHOT_BLOCK_RE.sub(_repl, prompt)


class ImageRegenerator:
    """個別画像の再生成・差し替え機能を提供するクラス"""
    
    def __init__(self, output_dir: Path):
        """
        Args:
            output_dir: 生成済み画像があるoutputディレクトリ
        """
        self.output_dir = Path(output_dir)
        self.images_dir = self.output_dir / "images"
        self.cues_file = self.output_dir / "image_cues.json"
        self.cues_data = None
        self._load_cues()
    
    def _load_cues(self):
        """image_cues.jsonファイルを読み込み"""
        if self.cues_file.exists():
            with open(self.cues_file, 'r', encoding='utf-8') as f:
                self.cues_data = json.load(f)
        else:
            raise FileNotFoundError(f"image_cues.json not found: {self.cues_file}")
    
    def get_image_list(self) -> List[Dict]:
        """
        生成済み画像の一覧を取得
        
        Returns:
            画像情報のリスト。各要素には以下が含まれる:
            - index: 画像番号
            - file_path: 画像ファイルパス
            - exists: ファイル存在フラグ
            - file_size: ファイルサイズ (bytes)
            - dimensions: 画像サイズ (width, height)
            - cue_info: image_cues.jsonの該当セグメント情報
            - is_fallback: フォールバック画像判定
        """
        if not self.cues_data:
            return []
        
        image_list = []
        
        for cue in self.cues_data.get('cues', []):
            index = cue.get('index', 0)
            image_file = self.images_dir / f"{index:04d}.png"
            
            info = {
                'index': index,
                'file_path': image_file,
                'exists': image_file.exists(),
                'file_size': 0,
                'dimensions': (0, 0),
                'cue_info': cue,
                'is_fallback': False
            }
            
            if image_file.exists():
                try:
                    stat = image_file.stat()
                    info['file_size'] = stat.st_size
                    
                    with Image.open(image_file) as img:
                        info['dimensions'] = img.size
                        
                    # 厳格なフォールバック検出システム
                    info['is_fallback'] = self._is_failed_image(image_file, stat.st_size, info['dimensions'])
                        
                except Exception as e:
                    logger.warning(f"Failed to analyze image {image_file}: {e}")
            
            image_list.append(info)
        
        return sorted(image_list, key=lambda x: x['index'])
    
    def get_image_info(self, index: int) -> Optional[Dict]:
        """
        指定されたindex番号の画像情報を取得
        
        Args:
            index: 画像番号
            
        Returns:
            画像情報辞書またはNone
        """
        image_list = self.get_image_list()
        for img_info in image_list:
            if img_info['index'] == index:
                return img_info
        return None
    
    def regenerate_image(self, index: int, custom_prompt: Optional[str] = None, 
                        custom_style: Optional[str] = None) -> bool:
        """
        指定されたindex番号の画像を再生成
        
        Args:
            index: 画像番号
            custom_prompt: 追加の指示（Noneの場合は追加なし）
            custom_style: カスタムスタイル（Noneの場合はデフォルトスタイル）
            
        Returns:
            成功フラグ
        """
        img_info = self.get_image_info(index)
        if not img_info:
            logger.error(f"Image info not found for index {index}")
            return False
        
        cue_info = img_info['cue_info']
        image_file = img_info['file_path']
        
        # プロンプト決定（image_cues の prompt/summary をベースに、追加指示を追記）
        base_prompt = cue_info.get('prompt', cue_info.get('summary', ''))
        diversity_note = str(cue_info.get("diversity_note") or "").strip()
        if diversity_note.startswith("Shot:") and isinstance(base_prompt, str) and "Shot:" in base_prompt:
            base_prompt = _replace_shot_block(base_prompt, diversity_note)

        # If the cue-level shot hint is not a close-up, avoid leaving stale "close-up reaction" tokens
        # in template guidance (they can overpower the medium-shot instruction).
        shot_hint = str(cue_info.get("shot_hint") or "").strip().lower()
        if isinstance(base_prompt, str) and shot_hint and "closeup" not in shot_hint:
            base_prompt = base_prompt.replace("close-up reaction", "medium reaction")
            base_prompt = base_prompt.replace("wide/medium/close-up", "wide/medium")
        prompt_parts = [base_prompt] if base_prompt else []
        if custom_prompt:
            prompt_parts.append(custom_prompt)
        if custom_style:
            prompt_parts.append(custom_style)
        prompt = "\n\n".join([p for p in prompt_parts if p])
        
        logger.info(f"Regenerating image {index:04d}.png with prompt: {prompt[:100]}...")
        
        try:
            # 一時的なバックアップ作成
            backup_file = None
            if image_file.exists():
                backup_file = image_file.with_suffix('.png.backup')
                shutil.copy2(image_file, backup_file)
            
            # 画像再生成
            success = _run_direct(
                prompt=prompt,
                output_path=str(image_file),
                width=1920,
                height=1080,
                config_path=str(Path.home() / "nanobanana" / "config.json"),
                timeout_sec=300,
                input_images=None
            )
            
            if success:
                # 16:9変換
                _convert_to_16_9(str(image_file), 1920, 1080)
                
                # バックアップ削除
                if backup_file and backup_file.exists():
                    backup_file.unlink()
                
                logger.info(f"Successfully regenerated image {index:04d}.png")
                return True
            else:
                # 失敗時はバックアップから復元
                if backup_file and backup_file.exists():
                    shutil.move(backup_file, image_file)
                logger.error(f"Failed to regenerate image {index:04d}.png")
                return False
                
        except Exception as e:
            # エラー時もバックアップから復元
            if backup_file and backup_file.exists():
                shutil.move(backup_file, image_file)
            logger.error(f"Error regenerating image {index:04d}.png: {e}")
            return False
    
    def regenerate_multiple_images(self, indices: List[int], custom_prompt: Optional[str] = None,
                                  custom_style: Optional[str] = None) -> Dict[int, bool]:
        """
        複数の画像を一度に再生成
        
        Args:
            indices: 画像番号のリスト
            custom_prompt: カスタムプロンプト
            custom_style: カスタムスタイル
            
        Returns:
            {index: success_flag} の辞書
        """
        results = {}
        for index in indices:
            results[index] = self.regenerate_image(index, custom_prompt, custom_style)
        return results
    
    def get_fallback_images(self) -> List[Dict]:
        """
        フォールバック画像（50KB未満）のリストを取得
        
        Returns:
            フォールバック画像の情報リスト
        """
        all_images = self.get_image_list()
        return [img for img in all_images if img['is_fallback'] and img['exists']]
    
    def regenerate_all_fallbacks(self, custom_prompt: Optional[str] = None,
                                custom_style: Optional[str] = None) -> Dict[int, bool]:
        """
        全てのフォールバック画像を再生成
        
        Args:
            custom_prompt: カスタムプロンプト
            custom_style: カスタムスタイル
            
        Returns:
            {index: success_flag} の辞書
        """
        fallbacks = self.get_fallback_images()
        indices = [img['index'] for img in fallbacks]
        logger.info(f"Found {len(indices)} fallback images to regenerate")
        return self.regenerate_multiple_images(indices, custom_prompt, custom_style)
    
    def _is_failed_image(self, image_path: Path, file_size: int, dimensions: tuple) -> bool:
        """
        厳格な失敗画像検出システム
        
        Args:
            image_path: 画像ファイルパス
            file_size: ファイルサイズ（bytes）
            dimensions: 画像サイズ（width, height）
            
        Returns:
            bool: True if failed/fallback image
        """
        try:
            # 1. ファイルサイズチェック: 50KB未満は明らかにプレースホルダー
            if file_size < 50 * 1024:
                logger.debug(f"Failed image detected (size {file_size}B < 50KB): {image_path}")
                return True
            
            # 2. 異常に小さい画像サイズをチェック
            width, height = dimensions
            if width < 100 or height < 100:
                logger.debug(f"Failed image detected (dimensions {width}x{height}): {image_path}")
                return True
            
            # 3. 日本語文字検出（OCRを使用してテキストを抽出し、日本語文字をチェック）
            if self._contains_japanese_text(image_path):
                logger.debug(f"Failed image detected (contains Japanese text): {image_path}")
                return True
            
            # 4. プレースホルダー画像の特徴検出（単色背景 + テキストのみ）
            if self._is_placeholder_pattern(image_path):
                logger.debug(f"Failed image detected (placeholder pattern): {image_path}")
                return True
                
            return False
            
        except Exception as e:
            logger.warning(f"Error checking failed image {image_path}: {e}")
            return True  # エラーの場合は安全側に倒して失敗画像として扱う
    
    def _contains_japanese_text(self, image_path: Path) -> bool:
        """
        画像に日本語文字が含まれているかをチェック
        Gemini Flash Image Previewでは日本語文字は入れないため、日本語があれば失敗画像
        """
        try:
            import pytesseract
            from PIL import Image
            
            with Image.open(image_path) as img:
                # OCRでテキスト抽出
                text = pytesseract.image_to_string(img, lang='jpn+eng')
                
                # 日本語文字（ひらがな、カタカナ、漢字）をチェック
                import re
                japanese_pattern = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FAF]+')
                
                if japanese_pattern.search(text):
                    logger.debug(f"Japanese text found in image: {text[:100]}")
                    return True
                    
        except ImportError:
            logger.debug("pytesseract not available, skipping Japanese text detection")
        except Exception as e:
            logger.debug(f"Error detecting Japanese text: {e}")
            
        return False
    
    def _is_placeholder_pattern(self, image_path: Path) -> bool:
        """
        プレースホルダー画像のパターンを検出
        （単色背景 + 中央のテキスト配置など）
        """
        try:
            from PIL import Image
            import numpy as np
            
            with Image.open(image_path) as img:
                # RGB変換
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                
                img_array = np.array(img)
                
                # 1. 色の多様性をチェック - プレースホルダーは色数が非常に少ない
                unique_colors = len(np.unique(img_array.reshape(-1, 3), axis=0))
                if unique_colors < 10:
                    logger.debug(f"Low color diversity detected: {unique_colors} colors")
                    return True
                
                # 2. 背景色の単調性をチェック
                # 画像の四隅の色をサンプリング
                height, width = img_array.shape[:2]
                corners = [
                    img_array[0, 0],           # 左上
                    img_array[0, width-1],     # 右上
                    img_array[height-1, 0],    # 左下
                    img_array[height-1, width-1]  # 右下
                ]
                
                # 四隅の色が似ている（単色背景の可能性）
                corner_colors = np.array(corners)
                color_variance = np.var(corner_colors, axis=0).sum()
                if color_variance < 100:  # 非常に低い分散値
                    logger.debug(f"Uniform background detected: variance {color_variance}")
                    return True
                    
        except ImportError:
            logger.debug("numpy not available, skipping placeholder pattern detection")
        except Exception as e:
            logger.debug(f"Error detecting placeholder pattern: {e}")
            
        return False
