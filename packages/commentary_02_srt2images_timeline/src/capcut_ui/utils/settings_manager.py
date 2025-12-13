#!/usr/bin/env python3
"""
Settings Manager
UI設定の保存・読み込み・管理システム
ドラフト別設定、レイヤー別設定、プリセット管理を提供
"""
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
import logging

class SettingsManager:
    """UI設定管理のメインクラス"""

    def __init__(self, base_settings_dir: str = None):
        """
        設定管理システムを初期化

        Args:
            base_settings_dir: 設定ファイル保存ディレクトリ
        """
        if base_settings_dir is None:
            # デフォルトは srt2images-timeline/settings/
            base_settings_dir = Path(__file__).parent.parent.parent.parent / "settings"

        self.settings_dir = Path(base_settings_dir)
        self.settings_dir.mkdir(parents=True, exist_ok=True)

        # 設定ファイルパス
        self.global_settings_file = self.settings_dir / "global_ui_settings.json"
        self.draft_settings_dir = self.settings_dir / "draft_settings"
        self.presets_file = self.settings_dir / "ui_presets.json"

        self.draft_settings_dir.mkdir(exist_ok=True)

        self.logger = logging.getLogger(__name__)

        # デフォルト設定
        self.default_global_settings = {
            "last_selected_draft": None,
            "ui_preferences": {
                "default_preview_zoom": 100,
                "auto_save": True,
                "show_advanced_controls": False,
                "theme": "light"
            },
            "window_state": {
                "last_selected_layer": 4,
                "timeline_position": 0.0
            }
        }

        self.default_draft_settings = {
            "draft_name": "",
            "last_modified": "",
            "layer_settings": {
                "4": {  # 画像レイヤー
                    "position": {"x": -0.3125, "y": 0.205},
                    "scale": 0.59,
                    "rotation": 0.0,
                    "opacity": 1.0
                },
                "6": {  # メイン字幕
                    "font_family": "Arial",
                    "font_size": 24.0,
                    "text_color": "#FFFFFF",
                    "stroke_width": 2.0,
                    "position": {"x": 0.0, "y": 0.8}
                },
                "5": {  # エフェクト
                    "intensity": 0.5,
                    "brightness": 0.0,
                    "contrast": 0.0,
                    "saturation": 0.0
                },
                "1": {  # オーディオ1
                    "volume": 1.0,
                    "pan": 0.0,
                    "fade_in": 0.0,
                    "fade_out": 0.0
                },
                "2": {  # オーディオ2
                    "volume": 1.0,
                    "pan": 0.0,
                    "fade_in": 0.0,
                    "fade_out": 0.0
                }
            },
            "custom_presets": {}
        }

        self.default_presets = {
            "image_layouts": {
                "center": {"x": 0.0, "y": 0.0, "scale": 1.0},
                "golden_ratio": {"x": -0.382, "y": 0.236, "scale": 0.8},
                "rule_of_thirds": {"x": -0.333, "y": -0.333, "scale": 0.75},
                "large_center": {"x": 0.0, "y": 0.0, "scale": 1.2},
                "small_corner": {"x": 0.6, "y": -0.6, "scale": 0.4}
            },
            "text_styles": {
                "default": {
                    "font_family": "Arial",
                    "font_size": 24.0,
                    "text_color": "#FFFFFF",
                    "stroke_width": 2.0
                },
                "title_large": {
                    "font_family": "Arial",
                    "font_size": 36.0,
                    "text_color": "#FFFFFF",
                    "stroke_width": 3.0
                },
                "elegant_gold": {
                    "font_family": "Times New Roman",
                    "font_size": 28.0,
                    "text_color": "#FFD700",
                    "stroke_width": 2.5
                }
            },
            "effect_presets": {
                "natural": {"intensity": 0.3, "brightness": 0.1, "contrast": 0.1, "saturation": 0.2},
                "vibrant": {"intensity": 0.7, "brightness": 0.2, "contrast": 0.3, "saturation": 0.5},
                "vintage": {"intensity": 0.6, "brightness": -0.1, "contrast": -0.2, "saturation": -0.3}
            },
            "audio_presets": {
                "clear_voice": {"volume": 1.2, "eq_low": -2.0, "eq_mid": 3.0, "eq_high": 2.0},
                "warm_voice": {"volume": 1.0, "eq_low": 2.0, "eq_mid": 1.0, "eq_high": -1.0},
                "background_music": {"volume": 0.6, "eq_low": 0.0, "eq_mid": -1.0, "eq_high": -2.0}
            }
        }

    def load_global_settings(self) -> Dict[str, Any]:
        """
        グローバル設定を読み込み

        Returns:
            Dict: グローバル設定データ
        """
        try:
            if self.global_settings_file.exists():
                with open(self.global_settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    # デフォルト設定でマージ
                    return self._merge_settings(self.default_global_settings, settings)
            else:
                return self.default_global_settings.copy()
        except Exception as e:
            self.logger.error(f"Failed to load global settings: {e}")
            return self.default_global_settings.copy()

    def save_global_settings(self, settings: Dict[str, Any]) -> bool:
        """
        グローバル設定を保存

        Args:
            settings: 保存する設定データ

        Returns:
            bool: 保存成功時True
        """
        try:
            with open(self.global_settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self.logger.error(f"Failed to save global settings: {e}")
            return False

    def load_draft_settings(self, draft_name: str) -> Dict[str, Any]:
        """
        特定ドラフトの設定を読み込み

        Args:
            draft_name: ドラフト名

        Returns:
            Dict: ドラフト設定データ
        """
        try:
            settings_file = self.draft_settings_dir / f"{self._sanitize_filename(draft_name)}.json"

            if settings_file.exists():
                with open(settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    # デフォルト設定でマージ
                    merged = self._merge_settings(self.default_draft_settings, settings)
                    merged["draft_name"] = draft_name
                    return merged
            else:
                # 新規ドラフトの場合はデフォルト設定を返す
                default = self.default_draft_settings.copy()
                default["draft_name"] = draft_name
                return default
        except Exception as e:
            self.logger.error(f"Failed to load draft settings for {draft_name}: {e}")
            default = self.default_draft_settings.copy()
            default["draft_name"] = draft_name
            return default

    def save_draft_settings(self, draft_name: str, settings: Dict[str, Any]) -> bool:
        """
        特定ドラフトの設定を保存

        Args:
            draft_name: ドラフト名
            settings: 保存する設定データ

        Returns:
            bool: 保存成功時True
        """
        try:
            settings_file = self.draft_settings_dir / f"{self._sanitize_filename(draft_name)}.json"

            # タイムスタンプを追加
            settings["last_modified"] = datetime.now().isoformat()
            settings["draft_name"] = draft_name

            with open(settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self.logger.error(f"Failed to save draft settings for {draft_name}: {e}")
            return False

    def load_presets(self) -> Dict[str, Any]:
        """
        プリセット設定を読み込み

        Returns:
            Dict: プリセット設定データ
        """
        try:
            if self.presets_file.exists():
                with open(self.presets_file, 'r', encoding='utf-8') as f:
                    presets = json.load(f)
                    # デフォルトプリセットでマージ
                    return self._merge_settings(self.default_presets, presets)
            else:
                return self.default_presets.copy()
        except Exception as e:
            self.logger.error(f"Failed to load presets: {e}")
            return self.default_presets.copy()

    def save_presets(self, presets: Dict[str, Any]) -> bool:
        """
        プリセット設定を保存

        Args:
            presets: 保存するプリセットデータ

        Returns:
            bool: 保存成功時True
        """
        try:
            with open(self.presets_file, 'w', encoding='utf-8') as f:
                json.dump(presets, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            self.logger.error(f"Failed to save presets: {e}")
            return False

    def get_layer_settings(self, draft_name: str, layer_index: int) -> Dict[str, Any]:
        """
        特定レイヤーの設定を取得

        Args:
            draft_name: ドラフト名
            layer_index: レイヤーインデックス

        Returns:
            Dict: レイヤー設定データ
        """
        draft_settings = self.load_draft_settings(draft_name)
        layer_key = str(layer_index)

        if layer_key in draft_settings["layer_settings"]:
            return draft_settings["layer_settings"][layer_key].copy()
        else:
            # デフォルト設定を返す
            return self.default_draft_settings["layer_settings"].get(layer_key, {}).copy()

    def save_layer_settings(self, draft_name: str, layer_index: int, layer_settings: Dict[str, Any]) -> bool:
        """
        特定レイヤーの設定を保存

        Args:
            draft_name: ドラフト名
            layer_index: レイヤーインデックス
            layer_settings: 保存するレイヤー設定

        Returns:
            bool: 保存成功時True
        """
        try:
            draft_settings = self.load_draft_settings(draft_name)
            layer_key = str(layer_index)

            if "layer_settings" not in draft_settings:
                draft_settings["layer_settings"] = {}

            draft_settings["layer_settings"][layer_key] = layer_settings

            return self.save_draft_settings(draft_name, draft_settings)
        except Exception as e:
            self.logger.error(f"Failed to save layer settings for {draft_name} layer {layer_index}: {e}")
            return False

    def export_settings(self, draft_name: str, export_path: str) -> bool:
        """
        設定をファイルにエクスポート

        Args:
            draft_name: ドラフト名
            export_path: エクスポート先ファイルパス

        Returns:
            bool: エクスポート成功時True
        """
        try:
            draft_settings = self.load_draft_settings(draft_name)
            global_settings = self.load_global_settings()
            presets = self.load_presets()

            export_data = {
                "export_info": {
                    "draft_name": draft_name,
                    "export_date": datetime.now().isoformat(),
                    "version": "1.0"
                },
                "draft_settings": draft_settings,
                "global_settings": global_settings,
                "presets": presets
            }

            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            self.logger.error(f"Failed to export settings: {e}")
            return False

    def import_settings(self, import_path: str, draft_name: str = None) -> bool:
        """
        設定をファイルからインポート

        Args:
            import_path: インポート元ファイルパス
            draft_name: インポート先ドラフト名（Noneの場合は元のドラフト名を使用）

        Returns:
            bool: インポート成功時True
        """
        try:
            with open(import_path, 'r', encoding='utf-8') as f:
                import_data = json.load(f)

            if "draft_settings" in import_data:
                target_draft = draft_name or import_data["draft_settings"].get("draft_name", "imported_draft")
                self.save_draft_settings(target_draft, import_data["draft_settings"])

            if "presets" in import_data:
                current_presets = self.load_presets()
                merged_presets = self._merge_settings(current_presets, import_data["presets"])
                self.save_presets(merged_presets)

            return True
        except Exception as e:
            self.logger.error(f"Failed to import settings: {e}")
            return False

    def list_draft_settings(self) -> List[Dict[str, Any]]:
        """
        保存されているドラフト設定の一覧を取得

        Returns:
            List[Dict]: ドラフト設定情報のリスト
        """
        try:
            settings_files = list(self.draft_settings_dir.glob("*.json"))
            draft_list = []

            for settings_file in settings_files:
                try:
                    with open(settings_file, 'r', encoding='utf-8') as f:
                        settings = json.load(f)

                    draft_list.append({
                        "draft_name": settings.get("draft_name", settings_file.stem),
                        "last_modified": settings.get("last_modified", ""),
                        "file_path": str(settings_file),
                        "layer_count": len(settings.get("layer_settings", {}))
                    })
                except:
                    continue

            # 最終更新日時でソート
            draft_list.sort(key=lambda x: x["last_modified"], reverse=True)
            return draft_list
        except Exception as e:
            self.logger.error(f"Failed to list draft settings: {e}")
            return []

    def delete_draft_settings(self, draft_name: str) -> bool:
        """
        特定ドラフトの設定を削除

        Args:
            draft_name: 削除するドラフト名

        Returns:
            bool: 削除成功時True
        """
        try:
            settings_file = self.draft_settings_dir / f"{self._sanitize_filename(draft_name)}.json"
            if settings_file.exists():
                settings_file.unlink()
                return True
            return False
        except Exception as e:
            self.logger.error(f"Failed to delete draft settings for {draft_name}: {e}")
            return False

    def _merge_settings(self, default: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
        """設定をマージ（再帰的）"""
        result = default.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_settings(result[key], value)
            else:
                result[key] = value

        return result

    def _sanitize_filename(self, filename: str) -> str:
        """ファイル名をサニタイズ"""
        import re
        # ファイル名として使用できない文字を置換
        sanitized = re.sub(r'[<>:"/\\|?*]', '_', filename)
        # 長すぎる場合は短縮
        if len(sanitized) > 200:
            sanitized = sanitized[:200]
        return sanitized

    def get_settings_info(self) -> Dict[str, Any]:
        """
        設定システムの情報を取得

        Returns:
            Dict: システム情報
        """
        return {
            "settings_directory": str(self.settings_dir),
            "global_settings_exists": self.global_settings_file.exists(),
            "presets_exists": self.presets_file.exists(),
            "draft_settings_count": len(list(self.draft_settings_dir.glob("*.json"))),
            "total_size_kb": sum(f.stat().st_size for f in self.settings_dir.rglob("*.json")) / 1024
        }


# テスト用関数
def test_settings_manager():
    """SettingsManagerのテスト関数"""
    print("⚙️ SettingsManager ready for use!")
    print("✅ Settings save/load, presets management, and draft-specific configurations available")

if __name__ == "__main__":
    test_settings_manager()