#!/usr/bin/env python3
"""
統一パラメータ管理システム
全ての設定をYAML/JSONファイルから読み込み・検証・提供
"""
import yaml
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
from dataclasses import dataclass, field
from copy import deepcopy

logger = logging.getLogger(__name__)


@dataclass
class FontConfig:
    """フォント設定"""
    family: str
    size: float
    weight: str = "normal"
    style: str = "normal"
    line_height: float = 1.5
    letter_spacing: float = 0.0
    fallback: List[str] = field(default_factory=list)


@dataclass
class ColorConfig:
    """色設定 (RGBA)"""
    r: float
    g: float
    b: float
    a: float = 1.0

    @classmethod
    def from_list(cls, rgba: List[float]) -> 'ColorConfig':
        """リストから色設定を作成"""
        if len(rgba) == 3:
            return cls(rgba[0], rgba[1], rgba[2], 1.0)
        elif len(rgba) == 4:
            return cls(rgba[0], rgba[1], rgba[2], rgba[3])
        else:
            raise ValueError(f"Invalid RGBA list: {rgba}")

    def to_list(self) -> List[float]:
        """リスト形式に変換"""
        return [self.r, self.g, self.b, self.a]

    def to_dict(self) -> Dict[str, float]:
        """辞書形式に変換"""
        return {"r": self.r, "g": self.g, "b": self.b, "a": self.a}


@dataclass
class PositionConfig:
    """位置設定"""
    x: float
    y: float

    def to_dict(self) -> Dict[str, float]:
        return {"x": self.x, "y": self.y}


@dataclass
class AnimationConfig:
    """アニメーション設定"""
    type: str
    duration: float
    easing: str = "ease_in_out_cubic"
    loop: bool = False
    params: Dict[str, Any] = field(default_factory=dict)


class ParameterValidator:
    """パラメータバリデーター"""

    def __init__(self, validation_rules: Dict[str, Any]):
        self.rules = validation_rules

    def validate_font_size(self, size: float) -> float:
        """フォントサイズを検証"""
        rules = self.rules.get('fonts', {}).get('size', {})
        min_size = rules.get('min', 8.0)
        max_size = rules.get('max', 128.0)

        if size < min_size:
            logger.warning(f"Font size {size} too small, clamping to {min_size}")
            return min_size
        if size > max_size:
            logger.warning(f"Font size {size} too large, clamping to {max_size}")
            return max_size
        return size

    def validate_color_component(self, value: float) -> float:
        """色成分を検証 (0.0-1.0)"""
        rules = self.rules.get('colors', {}).get('rgba', {})
        min_val = rules.get('min', 0.0)
        max_val = rules.get('max', 1.0)

        return max(min_val, min(max_val, value))

    def validate_color(self, color: List[float]) -> List[float]:
        """RGBA色を検証"""
        if len(color) not in [3, 4]:
            raise ValueError(f"Color must have 3 or 4 components, got {len(color)}")

        validated = [self.validate_color_component(c) for c in color]

        # アルファ値がない場合は1.0を追加
        if len(validated) == 3:
            validated.append(1.0)

        return validated

    def validate_position(self, x: float, y: float) -> tuple[float, float]:
        """位置を検証"""
        x_rules = self.rules.get('positions', {}).get('x', {})
        y_rules = self.rules.get('positions', {}).get('y', {})

        x_min = x_rules.get('min', -1.0)
        x_max = x_rules.get('max', 1.0)
        y_min = y_rules.get('min', -1.0)
        y_max = y_rules.get('max', 1.0)

        validated_x = max(x_min, min(x_max, x))
        validated_y = max(y_min, min(y_max, y))

        if validated_x != x or validated_y != y:
            logger.warning(f"Position clamped from ({x}, {y}) to ({validated_x}, {validated_y})")

        return validated_x, validated_y

    def validate_scale(self, scale: float) -> float:
        """スケールを検証"""
        rules = self.rules.get('scale', {})
        min_scale = rules.get('min', 0.1)
        max_scale = rules.get('max', 5.0)

        return max(min_scale, min(max_scale, scale))

    def validate_rotation(self, rotation: float) -> float:
        """回転角度を検証 (正規化)"""
        # -360 ~ 360の範囲に正規化
        while rotation < -360:
            rotation += 360
        while rotation > 360:
            rotation -= 360
        return rotation

    def validate_opacity(self, opacity: float) -> float:
        """不透明度を検証"""
        rules = self.rules.get('opacity', {})
        min_opacity = rules.get('min', 0.0)
        max_opacity = rules.get('max', 1.0)

        return max(min_opacity, min(max_opacity, opacity))


class ParameterManager:
    """統一パラメータ管理クラス"""

    def __init__(self, config_path: Optional[Path] = None):
        """
        パラメータマネージャーを初期化

        Args:
            config_path: 設定ファイルパス（未指定時はデフォルト）
        """
        if config_path is None:
            # プロジェクトルートのconfigディレクトリを探す
            current_file = Path(__file__).resolve()
            project_root = current_file.parent.parent.parent
            config_path = project_root / "config" / "default_parameters.yaml"

        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        self.validator: Optional[ParameterValidator] = None

        # 設定ファイルを読み込み
        self.load_config()

    def load_config(self) -> None:
        """設定ファイルを読み込み"""
        if not self.config_path.exists():
            logger.error(f"Config file not found: {self.config_path}")
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        try:
            with open(self.config_path, 'r', encoding='utf-8') as f:
                if self.config_path.suffix in ['.yaml', '.yml']:
                    self.config = yaml.safe_load(f)
                elif self.config_path.suffix == '.json':
                    self.config = json.load(f)
                else:
                    raise ValueError(f"Unsupported config file format: {self.config_path.suffix}")

            # バリデーターを初期化
            validation_rules = self.config.get('validation', {})
            self.validator = ParameterValidator(validation_rules)

            logger.info(f"Loaded configuration from {self.config_path}")

        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            raise

    def reload_config(self) -> None:
        """設定ファイルを再読み込み"""
        self.load_config()
        logger.info("Configuration reloaded")

    # ===================================================================
    # フォント設定取得
    # ===================================================================

    def get_font(self, preset_name: str = "default") -> FontConfig:
        """フォント設定を取得"""
        fonts = self.config.get('fonts', {})

        # プリセット検索
        preset = fonts.get(preset_name)
        if preset:
            if isinstance(preset, dict):
                return FontConfig(
                    family=preset.get('family', 'Arial'),
                    size=self.validator.validate_font_size(preset.get('size', 24.0)),
                    weight=preset.get('weight', 'normal'),
                    style=preset.get('style', 'normal'),
                    line_height=preset.get('line_height', 1.5),
                    letter_spacing=preset.get('letter_spacing', 0.0),
                    fallback=preset.get('fallback', [])
                )

        # デフォルトフォント
        default = fonts.get('default', {})
        return FontConfig(
            family=default.get('family', 'Arial'),
            size=self.validator.validate_font_size(default.get('size', 24.0)),
            weight=default.get('weight', 'normal'),
            style=default.get('style', 'normal')
        )

    def get_font_size_preset(self, size_name: str) -> float:
        """フォントサイズプリセットを取得"""
        sizes = self.config.get('fonts', {}).get('sizes', {})
        size = sizes.get(size_name, 24.0)
        return self.validator.validate_font_size(size)

    # ===================================================================
    # 色設定取得
    # ===================================================================

    def get_color(self, color_name: str) -> ColorConfig:
        """色を取得"""
        palette = self.config.get('colors', {}).get('palette', {})
        color = palette.get(color_name)

        if color and isinstance(color, list):
            validated = self.validator.validate_color(color)
            return ColorConfig.from_list(validated)

        # デフォルトは白
        return ColorConfig(1.0, 1.0, 1.0, 1.0)

    def get_text_colors(self, preset_name: str = "default") -> Dict[str, ColorConfig]:
        """テキスト色プリセットを取得"""
        presets = self.config.get('colors', {}).get('text_presets', {})
        preset = presets.get(preset_name, {})

        result = {}

        if 'text' in preset:
            validated = self.validator.validate_color(preset['text'])
            result['text'] = ColorConfig.from_list(validated)

        if 'stroke' in preset:
            validated = self.validator.validate_color(preset['stroke'])
            result['stroke'] = ColorConfig.from_list(validated)

        if 'background' in preset:
            validated = self.validator.validate_color(preset['background'])
            result['background'] = ColorConfig.from_list(validated)

        return result

    def get_gradient(self, gradient_name: str) -> Dict[str, Any]:
        """グラデーション設定を取得"""
        gradients = self.config.get('colors', {}).get('gradients', {})
        return gradients.get(gradient_name, {})

    # ===================================================================
    # エフェクト設定取得
    # ===================================================================

    def get_effect_preset(self, preset_name: str) -> Dict[str, Any]:
        """エフェクトプリセットを取得"""
        presets = self.config.get('effect_presets', {})
        preset = presets.get(preset_name, {})

        return deepcopy(preset)

    def get_color_adjustment(self, preset_name: str) -> Dict[str, float]:
        """色調整設定を取得"""
        adjustments = self.config.get('effects', {}).get('color_adjustment', {})
        return adjustments.get(preset_name, {})

    def get_filter_settings(self, filter_name: str) -> Dict[str, Any]:
        """フィルター設定を取得"""
        filters = self.config.get('effects', {}).get('filters', {})
        return filters.get(filter_name, {})

    def get_blend_modes(self) -> List[str]:
        """利用可能なブレンドモード一覧を取得"""
        blend = self.config.get('effects', {}).get('blend_modes', {})
        return blend.get('available_modes', ['normal'])

    # ===================================================================
    # レイアウト設定取得
    # ===================================================================

    def get_position(self, position_name: str) -> PositionConfig:
        """位置プリセットを取得"""
        positions = self.config.get('layout', {}).get('positions', {})
        pos = positions.get(position_name, {'x': 0.0, 'y': 0.0})

        x, y = self.validator.validate_position(pos['x'], pos['y'])
        return PositionConfig(x, y)

    def get_scale_preset(self, scale_name: str) -> float:
        """スケールプリセットを取得"""
        scales = self.config.get('layout', {}).get('scale', {}).get('presets', {})
        scale = scales.get(scale_name, 1.0)
        return self.validator.validate_scale(scale)

    def get_rotation_preset(self, rotation_name: str) -> float:
        """回転プリセットを取得"""
        rotations = self.config.get('layout', {}).get('rotation', {}).get('presets', {})
        rotation = rotations.get(rotation_name, 0.0)
        return self.validator.validate_rotation(rotation)

    def get_golden_ratio_points(self) -> Dict[str, float]:
        """黄金比配置点を取得"""
        golden = self.config.get('layout', {}).get('golden_ratio', {})
        return {
            'phi': golden.get('phi', 1.618),
            'left_point': golden.get('left_point', -0.236),
            'right_point': golden.get('right_point', 0.236),
            'top_point': golden.get('top_point', -0.208),
            'bottom_point': golden.get('bottom_point', 0.208)
        }

    def get_rule_of_thirds(self) -> Dict[str, List[float]]:
        """三分割法グリッドを取得"""
        thirds = self.config.get('layout', {}).get('rule_of_thirds', {})
        return {
            'vertical_lines': thirds.get('vertical_lines', [-0.667, 0.0, 0.667]),
            'horizontal_lines': thirds.get('horizontal_lines', [-0.375, 0.0, 0.375])
        }

    def get_layout_preset(self, preset_name: str) -> Dict[str, Any]:
        """レイアウトプリセットを取得"""
        presets = self.config.get('layout_presets', {})
        return deepcopy(presets.get(preset_name, {}))

    # ===================================================================
    # アニメーション設定取得
    # ===================================================================

    def get_easing_types(self) -> List[str]:
        """利用可能なイージング関数一覧を取得"""
        easing = self.config.get('animations', {}).get('easing', {})
        return easing.get('types', ['linear'])

    def get_text_animation(self, animation_name: str) -> AnimationConfig:
        """テキストアニメーションを取得"""
        animations = self.config.get('animations', {}).get('text_animations', {})
        anim = animations.get(animation_name, {})

        return AnimationConfig(
            type=anim.get('type', 'fade'),
            duration=anim.get('duration', 0.5),
            easing=anim.get('easing', 'ease_in_out_cubic'),
            loop=anim.get('loop', False),
            params={k: v for k, v in anim.items() if k not in ['type', 'duration', 'easing', 'loop']}
        )

    def get_transition(self, transition_name: str) -> Dict[str, Any]:
        """トランジション設定を取得"""
        transitions = self.config.get('animations', {}).get('transitions', {})
        return transitions.get(transition_name, {})

    def get_keyframe_preset(self, preset_name: str) -> Dict[str, Any]:
        """キーフレームアニメーションプリセットを取得"""
        keyframes = self.config.get('animations', {}).get('keyframe_presets', {})
        return deepcopy(keyframes.get(preset_name, {}))

    # ===================================================================
    # 字幕プリセット取得
    # ===================================================================

    def get_subtitle_preset(self, preset_name: str = "default") -> Dict[str, Any]:
        """字幕プリセットを取得"""
        presets = self.config.get('subtitle_presets', {})
        return deepcopy(presets.get(preset_name, {}))

    def get_all_subtitle_presets(self) -> Dict[str, Dict[str, Any]]:
        """全字幕プリセットを取得"""
        return deepcopy(self.config.get('subtitle_presets', {}))

    # ===================================================================
    # プロジェクト設定取得
    # ===================================================================

    def get_project_defaults(self) -> Dict[str, Any]:
        """プロジェクトデフォルト設定を取得"""
        return self.config.get('project', {}).get('defaults', {})

    def get_capcut_settings(self) -> Dict[str, Any]:
        """CapCut統合設定を取得"""
        return self.config.get('project', {}).get('capcut', {})

    def get_image_generation_settings(self) -> Dict[str, Any]:
        """画像生成設定を取得"""
        return self.config.get('project', {}).get('image_generation', {})

    # ===================================================================
    # ユーティリティメソッド
    # ===================================================================

    def get_raw_config(self, path: str, default: Any = None) -> Any:
        """
        ドット記法で任意の設定を取得

        Args:
            path: 設定パス（例: "fonts.japanese.gothic.family"）
            default: デフォルト値

        Returns:
            設定値（見つからない場合はdefault）
        """
        keys = path.split('.')
        value = self.config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def save_config(self, output_path: Optional[Path] = None) -> None:
        """
        設定を保存

        Args:
            output_path: 出力パス（未指定時は元のファイルを上書き）
        """
        if output_path is None:
            output_path = self.config_path

        output_path = Path(output_path)

        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                if output_path.suffix in ['.yaml', '.yml']:
                    yaml.dump(self.config, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
                elif output_path.suffix == '.json':
                    json.dump(self.config, f, ensure_ascii=False, indent=2)
                else:
                    raise ValueError(f"Unsupported output format: {output_path.suffix}")

            logger.info(f"Configuration saved to {output_path}")

        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise

    def update_config(self, path: str, value: Any) -> None:
        """
        設定を更新

        Args:
            path: 設定パス（例: "fonts.default.size"）
            value: 新しい値
        """
        keys = path.split('.')
        config = self.config

        # 最後のキー以外を辿る
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]

        # 最後のキーに値を設定
        config[keys[-1]] = value

        logger.info(f"Updated config: {path} = {value}")


# グローバルインスタンス（シングルトンパターン）
_global_parameter_manager: Optional[ParameterManager] = None


def get_parameter_manager(config_path: Optional[Path] = None) -> ParameterManager:
    """
    グローバルパラメータマネージャーインスタンスを取得

    Args:
        config_path: 設定ファイルパス（初回のみ有効）

    Returns:
        ParameterManager: パラメータマネージャーインスタンス
    """
    global _global_parameter_manager

    if _global_parameter_manager is None:
        _global_parameter_manager = ParameterManager(config_path)

    return _global_parameter_manager


# テスト用関数
def test_parameter_manager():
    """ParameterManagerのテスト関数"""
    print("🔧 ParameterManager Test")
    print("=" * 60)

    # パラメータマネージャー初期化
    pm = get_parameter_manager()

    # フォント取得テスト
    print("\n📝 Font Configuration:")
    default_font = pm.get_font("default")
    print(f"  Default Font: {default_font.family}, {default_font.size}pt, {default_font.weight}")

    japanese_gothic = pm.get_font("japanese.gothic")
    print(f"  Japanese Gothic: {japanese_gothic.family}, {japanese_gothic.size}pt")

    # 色取得テスト
    print("\n🎨 Color Configuration:")
    white = pm.get_color("white")
    print(f"  White: RGBA({white.r}, {white.g}, {white.b}, {white.a})")

    text_colors = pm.get_text_colors("default")
    print(f"  Text Colors: {len(text_colors)} colors")

    # エフェクト取得テスト
    print("\n✨ Effect Presets:")
    vibrant = pm.get_effect_preset("vibrant")
    print(f"  Vibrant: {vibrant.get('name', 'N/A')}")

    # レイアウト取得テスト
    print("\n📐 Layout Configuration:")
    center_pos = pm.get_position("center")
    print(f"  Center Position: ({center_pos.x}, {center_pos.y})")

    golden = pm.get_golden_ratio_points()
    print(f"  Golden Ratio φ: {golden['phi']}")

    # アニメーション取得テスト
    print("\n🎬 Animation Configuration:")
    fade_in = pm.get_text_animation("fade_in")
    print(f"  Fade In: {fade_in.type}, {fade_in.duration}s, {fade_in.easing}")

    # 字幕プリセット取得テスト
    print("\n💬 Subtitle Presets:")
    large_clear = pm.get_subtitle_preset("large_clear")
    print(f"  Large Clear: Font {large_clear.get('font', {}).get('size', 'N/A')}pt")

    print("\n✅ ParameterManager test completed!")


if __name__ == "__main__":
    # ログ設定
    logging.basicConfig(level=logging.INFO)

    # テスト実行
    test_parameter_manager()
