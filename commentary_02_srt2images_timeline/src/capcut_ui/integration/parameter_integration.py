#!/usr/bin/env python3
"""
パラメータ管理システムと既存コントローラーの統合
TextLayerController、EffectLayerControllerへのパラメータ管理統合
"""
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

# パス追加
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config.parameter_manager import get_parameter_manager, ParameterManager
from capcut_ui.layers.text_layer import TextLayerController
from capcut_ui.layers.effect_layer import EffectLayerController
from capcut_ui.core.parameter_calculator import ParameterCalculator


class ParameterIntegration:
    """パラメータ管理システム統合クラス"""

    def __init__(self, config_path: Optional[Path] = None):
        """
        統合システムを初期化

        Args:
            config_path: 設定ファイルパス（オプション）
        """
        self.param_manager = get_parameter_manager(config_path)

    # ===================================================================
    # TextLayerController統合
    # ===================================================================

    def apply_subtitle_preset_to_text_layer(
        self,
        text_controller: TextLayerController,
        preset_name: str = "default",
        segment_indices: Optional[List[int]] = None
    ) -> bool:
        """
        字幕プリセットをTextLayerControllerに適用

        Args:
            text_controller: テキストレイヤーコントローラー
            preset_name: プリセット名
            segment_indices: 適用するセグメントのインデックス（None時は全て）

        Returns:
            bool: 適用成功時True
        """
        # プリセット取得
        preset = self.param_manager.get_subtitle_preset(preset_name)

        if not preset:
            return False

        if segment_indices is None:
            segment_indices = list(range(len(text_controller.segments)))

        success_count = 0

        for idx in segment_indices:
            # フォント設定
            if 'font' in preset:
                font = preset['font']
                if text_controller.set_font_style(
                    idx,
                    font_family=font.get('family'),
                    font_size=font.get('size'),
                    font_weight=font.get('weight'),
                    font_style=font.get('style')
                ):
                    success_count += 1

            # 色設定
            if 'color' in preset:
                color = preset['color']
                if text_controller.set_text_color(
                    idx,
                    text_color=color.get('text'),
                    stroke_color=color.get('stroke'),
                    background_color=color.get('background')
                ):
                    success_count += 1

            # 位置設定
            if 'position' in preset:
                pos = preset['position']
                if text_controller.set_text_position(idx, pos['x'], pos['y']):
                    success_count += 1

        return success_count > 0

    def set_font_from_config(
        self,
        text_controller: TextLayerController,
        font_preset: str,
        segment_indices: Optional[List[int]] = None
    ) -> bool:
        """
        設定ファイルからフォントを適用

        Args:
            text_controller: テキストレイヤーコントローラー
            font_preset: フォントプリセット名（例: "japanese.gothic"）
            segment_indices: 適用するセグメント

        Returns:
            bool: 適用成功時True
        """
        # フォント設定取得
        font_config = self.param_manager.get_font(font_preset)

        if segment_indices is None:
            segment_indices = list(range(len(text_controller.segments)))

        success_count = 0

        for idx in segment_indices:
            if text_controller.set_font_style(
                idx,
                font_family=font_config.family,
                font_size=font_config.size,
                font_weight=font_config.weight,
                font_style=font_config.style
            ):
                success_count += 1

        return success_count > 0

    def set_text_color_from_config(
        self,
        text_controller: TextLayerController,
        color_preset: str,
        segment_indices: Optional[List[int]] = None
    ) -> bool:
        """
        設定ファイルからテキスト色を適用

        Args:
            text_controller: テキストレイヤーコントローラー
            color_preset: 色プリセット名（例: "default"、"golden"）
            segment_indices: 適用するセグメント

        Returns:
            bool: 適用成功時True
        """
        # 色プリセット取得
        colors = self.param_manager.get_text_colors(color_preset)

        if not colors:
            return False

        if segment_indices is None:
            segment_indices = list(range(len(text_controller.segments)))

        success_count = 0

        for idx in segment_indices:
            kwargs = {}

            if 'text' in colors:
                kwargs['text_color'] = colors['text'].to_list()
            if 'stroke' in colors:
                kwargs['stroke_color'] = colors['stroke'].to_list()
            if 'background' in colors:
                kwargs['background_color'] = colors['background'].to_list()

            if text_controller.set_text_color(idx, **kwargs):
                success_count += 1

        return success_count > 0

    def set_text_position_from_config(
        self,
        text_controller: TextLayerController,
        position_preset: str,
        segment_indices: Optional[List[int]] = None
    ) -> bool:
        """
        設定ファイルから位置を適用

        Args:
            text_controller: テキストレイヤーコントローラー
            position_preset: 位置プリセット名（例: "subtitle_default"）
            segment_indices: 適用するセグメント

        Returns:
            bool: 適用成功時True
        """
        # 位置プリセット取得
        position = self.param_manager.get_position(position_preset)

        if segment_indices is None:
            segment_indices = list(range(len(text_controller.segments)))

        success_count = 0

        for idx in segment_indices:
            if text_controller.set_text_position(idx, position.x, position.y):
                success_count += 1

        return success_count > 0

    # ===================================================================
    # EffectLayerController統合
    # ===================================================================

    def apply_effect_preset_to_effect_layer(
        self,
        effect_controller: EffectLayerController,
        preset_name: str = "natural"
    ) -> bool:
        """
        エフェクトプリセットをEffectLayerControllerに適用

        Args:
            effect_controller: エフェクトレイヤーコントローラー
            preset_name: プリセット名（例: "vibrant"、"cinematic"）

        Returns:
            bool: 適用成功時True
        """
        # プリセット取得
        preset = self.param_manager.get_effect_preset(preset_name)

        if not preset:
            return False

        success_count = 0

        # 色調整の適用
        if 'color_adjustment' in preset:
            color_adj = preset['color_adjustment']

            for i in range(len(effect_controller.segments)):
                if effect_controller.set_color_adjustment(
                    i,
                    brightness=color_adj.get('brightness'),
                    contrast=color_adj.get('contrast'),
                    saturation=color_adj.get('saturation'),
                    hue=color_adj.get('hue')
                ):
                    success_count += 1

        # フィルターの適用
        if 'filters' in preset:
            filters = preset['filters']

            # ブラーフィルター
            if 'blur' in filters:
                blur_settings = filters['blur']
                if 'gaussian_blur' in blur_settings:
                    radius = blur_settings['gaussian_blur'].get('radius', 0.0)
                    for i in range(len(effect_controller.segments)):
                        if effect_controller.set_blur_effect(i, radius):
                            success_count += 1

        # ブレンドモード設定
        if 'blend_mode' in preset:
            blend_mode = preset['blend_mode']
            for i in range(len(effect_controller.segments)):
                if effect_controller._set_segment_blend_mode(i, blend_mode):
                    success_count += 1

        return success_count > 0

    def apply_color_adjustment_from_config(
        self,
        effect_controller: EffectLayerController,
        adjustment_preset: str
    ) -> bool:
        """
        設定ファイルから色調整を適用

        Args:
            effect_controller: エフェクトレイヤーコントローラー
            adjustment_preset: 色調整プリセット名

        Returns:
            bool: 適用成功時True
        """
        # 色調整設定取得
        adjustment = self.param_manager.get_color_adjustment(adjustment_preset)

        if not adjustment:
            return False

        success_count = 0

        for i in range(len(effect_controller.segments)):
            if effect_controller.set_color_adjustment(
                i,
                brightness=adjustment.get('brightness'),
                contrast=adjustment.get('contrast'),
                saturation=adjustment.get('saturation'),
                hue=adjustment.get('hue')
            ):
                success_count += 1

        return success_count > 0

    # ===================================================================
    # ParameterCalculator統合
    # ===================================================================

    def configure_parameter_calculator(self, calculator: ParameterCalculator) -> None:
        """
        ParameterCalculatorに設定を適用

        Args:
            calculator: パラメータ計算機
        """
        # 黄金比設定
        golden = self.param_manager.get_golden_ratio_points()
        calculator.GOLDEN_RATIO = golden['phi']

        # セーフエリアマージン設定
        canvas = self.param_manager.get_raw_config('layout.canvas', {})
        if 'safe_area_margin' in canvas:
            calculator.SAFE_AREA_MARGIN = canvas['safe_area_margin']

    def get_layout_from_preset(self, preset_name: str) -> Dict[str, Dict[str, float]]:
        """
        レイアウトプリセットから配置情報を取得

        Args:
            preset_name: レイアウトプリセット名

        Returns:
            Dict: レイヤー名: 配置情報のマップ
        """
        preset = self.param_manager.get_layout_preset(preset_name)

        if not preset or 'layers' not in preset:
            return {}

        result = {}

        for layer_name, layer_config in preset['layers'].items():
            result[layer_name] = {
                'x': layer_config.get('position', {}).get('x', 0.0),
                'y': layer_config.get('position', {}).get('y', 0.0),
                'scale': layer_config.get('scale', 1.0),
                'rotation': layer_config.get('rotation', 0.0)
            }

        return result

    # ===================================================================
    # 一括適用ユーティリティ
    # ===================================================================

    def apply_complete_style(
        self,
        text_controller: TextLayerController,
        effect_controller: Optional[EffectLayerController] = None,
        style_name: str = "default"
    ) -> bool:
        """
        完全なスタイルを一括適用

        Args:
            text_controller: テキストレイヤーコントローラー
            effect_controller: エフェクトレイヤーコントローラー（オプション）
            style_name: スタイル名

        Returns:
            bool: 適用成功時True
        """
        # プロジェクトデフォルト設定取得
        defaults = self.param_manager.get_project_defaults()

        # 字幕プリセット適用
        subtitle_preset = defaults.get('subtitle_preset', style_name)
        subtitle_success = self.apply_subtitle_preset_to_text_layer(
            text_controller,
            subtitle_preset
        )

        # エフェクトプリセット適用（effect_controllerが指定されている場合）
        effect_success = True
        if effect_controller:
            effect_preset = defaults.get('effect_preset', 'natural')
            effect_success = self.apply_effect_preset_to_effect_layer(
                effect_controller,
                effect_preset
            )

        return subtitle_success and effect_success

    def batch_apply_from_config(
        self,
        text_controller: TextLayerController,
        config_dict: Dict[str, str]
    ) -> Dict[str, bool]:
        """
        設定辞書からバッチ適用

        Args:
            text_controller: テキストレイヤーコントローラー
            config_dict: 設定辞書
                例: {
                    'font': 'japanese.gothic',
                    'color': 'elegant_gold',
                    'position': 'subtitle_default'
                }

        Returns:
            Dict: 各設定の適用結果
        """
        results = {}

        if 'font' in config_dict:
            results['font'] = self.set_font_from_config(
                text_controller,
                config_dict['font']
            )

        if 'color' in config_dict:
            results['color'] = self.set_text_color_from_config(
                text_controller,
                config_dict['color']
            )

        if 'position' in config_dict:
            results['position'] = self.set_text_position_from_config(
                text_controller,
                config_dict['position']
            )

        return results


# グローバルインスタンス
_global_param_integration: Optional[ParameterIntegration] = None


def get_parameter_integration(config_path: Optional[Path] = None) -> ParameterIntegration:
    """
    グローバルパラメータ統合インスタンスを取得

    Args:
        config_path: 設定ファイルパス（初回のみ有効）

    Returns:
        ParameterIntegration: パラメータ統合インスタンス
    """
    global _global_param_integration

    if _global_param_integration is None:
        _global_param_integration = ParameterIntegration(config_path)

    return _global_param_integration


# テスト用関数
def test_parameter_integration():
    """ParameterIntegrationのテスト関数"""
    print("🔗 ParameterIntegration Test")
    print("=" * 60)

    # パラメータ統合初期化
    integration = get_parameter_integration()

    # 字幕プリセット一覧
    print("\n📝 Available Subtitle Presets:")
    presets = integration.param_manager.get_all_subtitle_presets()
    for name in presets.keys():
        print(f"  - {name}")

    # エフェクトプリセット一覧
    print("\n✨ Available Effect Presets:")
    effect_presets = ['natural', 'vibrant', 'cinematic', 'dreamy', 'vintage']
    for name in effect_presets:
        preset = integration.param_manager.get_effect_preset(name)
        print(f"  - {name}: {preset.get('description', 'N/A')}")

    # レイアウトプリセット一覧
    print("\n📐 Available Layout Presets:")
    layout_presets = ['classic_center', 'golden_magazine', 'dynamic_three', 'split_screen']
    for name in layout_presets:
        preset = integration.param_manager.get_layout_preset(name)
        print(f"  - {name}: {preset.get('description', 'N/A')}")

    print("\n✅ ParameterIntegration test completed!")


if __name__ == "__main__":
    # テスト実行
    test_parameter_integration()
