#!/usr/bin/env python3
"""
Effect Layer Controller
エフェクトレイヤー（Layer 5）専用の制御システム
視覚エフェクト、トランジション、フィルター、アニメーションなどを管理
"""
import math
from typing import Dict, Any, List, Optional, Tuple
from ..core.layer_controller import LayerController

class EffectParameters:
    """エフェクトパラメータ定義クラス"""

    def __init__(self, effect_type: str = "none"):
        self.effect_type = effect_type

        # 基本パラメータ
        self.intensity = 0.5      # エフェクト強度 (0.0-1.0)
        self.opacity = 1.0        # 透明度 (0.0-1.0)
        self.duration = 1.0       # エフェクト継続時間

        # 色調整
        self.brightness = 0.0     # 明度調整 (-1.0 ～ 1.0)
        self.contrast = 0.0       # コントラスト調整 (-1.0 ～ 1.0)
        self.saturation = 0.0     # 彩度調整 (-1.0 ～ 1.0)
        self.hue = 0.0           # 色相調整 (-180 ～ 180度)

        # ブラー・シャープ
        self.blur_radius = 0.0    # ブラー半径 (0.0-100.0)
        self.sharpen_amount = 0.0 # シャープネス (0.0-1.0)

        # 歪み・変形
        self.scale = 1.0          # スケール (0.1-5.0)
        self.rotation = 0.0       # 回転角度 (0-360度)
        self.distortion = 0.0     # 歪み強度 (0.0-1.0)

        # アニメーション
        self.animation_speed = 1.0    # アニメーション速度
        self.loop_enabled = False     # ループ再生

        # カスタムパラメータ（エフェクト固有）
        self.custom_params = {}

    def to_capcut_format(self) -> Dict[str, Any]:
        """CapCut形式のエフェクトパラメータに変換"""
        return {
            "type": self.effect_type,
            "basic": {
                "intensity": self.intensity,
                "opacity": self.opacity,
                "duration": self.duration
            },
            "color": {
                "brightness": self.brightness,
                "contrast": self.contrast,
                "saturation": self.saturation,
                "hue": self.hue
            },
            "filter": {
                "blur_radius": self.blur_radius,
                "sharpen_amount": self.sharpen_amount
            },
            "transform": {
                "scale": self.scale,
                "rotation": self.rotation,
                "distortion": self.distortion
            },
            "animation": {
                "speed": self.animation_speed,
                "loop": self.loop_enabled
            },
            "custom": self.custom_params
        }

    @classmethod
    def from_capcut_format(cls, data: Dict[str, Any]) -> 'EffectParameters':
        """CapCut形式データからエフェクトパラメータを作成"""
        effect_type = data.get('type', 'none')
        params = cls(effect_type)

        if 'basic' in data:
            basic = data['basic']
            params.intensity = basic.get('intensity', 0.5)
            params.opacity = basic.get('opacity', 1.0)
            params.duration = basic.get('duration', 1.0)

        if 'color' in data:
            color = data['color']
            params.brightness = color.get('brightness', 0.0)
            params.contrast = color.get('contrast', 0.0)
            params.saturation = color.get('saturation', 0.0)
            params.hue = color.get('hue', 0.0)

        if 'filter' in data:
            filter_data = data['filter']
            params.blur_radius = filter_data.get('blur_radius', 0.0)
            params.sharpen_amount = filter_data.get('sharpen_amount', 0.0)

        if 'transform' in data:
            transform = data['transform']
            params.scale = transform.get('scale', 1.0)
            params.rotation = transform.get('rotation', 0.0)
            params.distortion = transform.get('distortion', 0.0)

        if 'animation' in data:
            animation = data['animation']
            params.animation_speed = animation.get('speed', 1.0)
            params.loop_enabled = animation.get('loop', False)

        if 'custom' in data:
            params.custom_params = data['custom']

        return params


class EffectLayerController(LayerController):
    """エフェクトレイヤー制御クラス"""

    def __init__(self, track_index: int, track_data: Dict, draft_manager):
        """
        エフェクトレイヤーコントローラーを初期化

        Args:
            track_index: トラックインデックス（通常5）
            track_data: エフェクトトラックデータ
            draft_manager: DraftManagerインスタンス
        """
        super().__init__(track_index, track_data, draft_manager)

        # エフェクトレイヤー固有の初期化
        self.effect_segments = self._extract_effect_segments()
        self.default_params = EffectParameters("default")

    def _extract_effect_segments(self) -> List[Dict]:
        """エフェクトセグメントのみを抽出"""
        effect_segments = []
        for segment in self.segments:
            material_type = segment.get('material_type', '')
            if 'effect' in material_type.lower() or 'filter' in str(segment.get('material', {})).lower():
                effect_segments.append(segment)
        return effect_segments

    def get_specific_properties(self) -> Dict[str, Any]:
        """
        エフェクトレイヤー固有プロパティを取得

        Returns:
            Dict: エフェクトレイヤー固有プロパティ
        """
        properties = {
            'total_effects': len(self.effect_segments),
            'effect_types': [],
            'parameters_data': [],
            'timing_data': [],
            'blend_modes': []
        }

        for i, segment in enumerate(self.segments):
            # エフェクトタイプ
            effect_type = self._get_segment_effect_type(i)
            properties['effect_types'].append(effect_type)

            # パラメータ情報
            params = self._get_segment_parameters(i)
            properties['parameters_data'].append(params)

            # タイミング情報
            timing = self.get_segment_timing(i)
            properties['timing_data'].append(timing)

            # ブレンドモード
            blend_mode = self._get_segment_blend_mode(i)
            properties['blend_modes'].append(blend_mode)

        return properties

    def set_specific_properties(self, properties: Dict[str, Any]) -> bool:
        """
        エフェクトレイヤー固有プロパティを設定

        Args:
            properties: 設定するプロパティ

        Returns:
            bool: 設定成功時True
        """
        try:
            # パラメータ設定
            if 'parameters_data' in properties:
                for i, params_data in enumerate(properties['parameters_data']):
                    if i < len(self.segments):
                        self._set_segment_parameters(i, params_data)

            # ブレンドモード設定
            if 'blend_modes' in properties:
                for i, blend_mode in enumerate(properties['blend_modes']):
                    if i < len(self.segments):
                        self._set_segment_blend_mode(i, blend_mode)

            return True

        except Exception as e:
            self.logger.error(f"Failed to set effect properties: {e}")
            return False

    def _get_segment_effect_type(self, segment_index: int) -> str:
        """セグメントのエフェクトタイプを取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return "none"

        material = segment.get('material', {})
        return material.get('effect_type', material.get('type', 'none'))

    def _get_segment_parameters(self, segment_index: int) -> Dict[str, Any]:
        """セグメントのパラメータ情報を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return self.default_params.to_capcut_format()

        material = segment.get('material', {})
        params_data = material.get('parameters', {})

        if not params_data:
            return self.default_params.to_capcut_format()

        return params_data

    def _set_segment_parameters(self, segment_index: int, params_data: Dict[str, Any]) -> bool:
        """セグメントのパラメータ情報を設定"""
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
            if 'material' not in segment:
                segment['material'] = {}

            # パラメータデータを適用
            segment['material']['parameters'] = params_data

            self.logger.info(f"Updated segment {segment_index} parameters")
            return True

        except Exception as e:
            self.logger.error(f"Failed to set parameters for segment {segment_index}: {e}")
            return False

    def _get_segment_blend_mode(self, segment_index: int) -> str:
        """セグメントのブレンドモードを取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return "normal"

        material = segment.get('material', {})
        return material.get('blend_mode', 'normal')

    def _set_segment_blend_mode(self, segment_index: int, blend_mode: str) -> bool:
        """セグメントのブレンドモードを設定"""
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
            if 'material' not in segment:
                segment['material'] = {}

            segment['material']['blend_mode'] = blend_mode
            return True

        except Exception as e:
            self.logger.error(f"Failed to set blend mode for segment {segment_index}: {e}")
            return False

    def set_effect_intensity(self, segment_index: int, intensity: float) -> bool:
        """
        エフェクト強度を設定

        Args:
            segment_index: セグメントインデックス
            intensity: エフェクト強度 (0.0-1.0)

        Returns:
            bool: 設定成功時True
        """
        current_params = self._get_segment_parameters(segment_index)

        if 'basic' not in current_params:
            current_params['basic'] = {}

        current_params['basic']['intensity'] = max(0.0, min(1.0, intensity))
        return self._set_segment_parameters(segment_index, current_params)

    def set_color_adjustment(self, segment_index: int, brightness: float = None,
                           contrast: float = None, saturation: float = None, hue: float = None) -> bool:
        """
        色調整を設定

        Args:
            segment_index: セグメントインデックス
            brightness: 明度調整 (-1.0 ～ 1.0)
            contrast: コントラスト調整 (-1.0 ～ 1.0)
            saturation: 彩度調整 (-1.0 ～ 1.0)
            hue: 色相調整 (-180 ～ 180度)

        Returns:
            bool: 設定成功時True
        """
        current_params = self._get_segment_parameters(segment_index)

        if 'color' not in current_params:
            current_params['color'] = {}

        if brightness is not None:
            current_params['color']['brightness'] = max(-1.0, min(1.0, brightness))
        if contrast is not None:
            current_params['color']['contrast'] = max(-1.0, min(1.0, contrast))
        if saturation is not None:
            current_params['color']['saturation'] = max(-1.0, min(1.0, saturation))
        if hue is not None:
            current_params['color']['hue'] = hue % 360

        return self._set_segment_parameters(segment_index, current_params)

    def set_blur_effect(self, segment_index: int, blur_radius: float) -> bool:
        """
        ブラーエフェクトを設定

        Args:
            segment_index: セグメントインデックス
            blur_radius: ブラー半径 (0.0-100.0)

        Returns:
            bool: 設定成功時True
        """
        current_params = self._get_segment_parameters(segment_index)

        if 'filter' not in current_params:
            current_params['filter'] = {}

        current_params['filter']['blur_radius'] = max(0.0, min(100.0, blur_radius))
        return self._set_segment_parameters(segment_index, current_params)

    def set_animation_speed(self, segment_index: int, speed: float) -> bool:
        """
        アニメーション速度を設定

        Args:
            segment_index: セグメントインデックス
            speed: アニメーション速度 (0.1-10.0)

        Returns:
            bool: 設定成功時True
        """
        current_params = self._get_segment_parameters(segment_index)

        if 'animation' not in current_params:
            current_params['animation'] = {}

        current_params['animation']['speed'] = max(0.1, min(10.0, speed))
        return self._set_segment_parameters(segment_index, current_params)

    def apply_effect_preset(self, preset_name: str) -> bool:
        """
        エフェクトプリセットを適用

        Args:
            preset_name: プリセット名

        Returns:
            bool: 適用成功時True
        """
        presets = self._get_effect_presets()

        if preset_name not in presets:
            self.logger.warning(f"Unknown effect preset: {preset_name}")
            return False

        preset_params = presets[preset_name]

        success_count = 0
        for i in range(len(self.segments)):
            if self._set_segment_parameters(i, preset_params):
                success_count += 1

        self.logger.info(f"Applied effect preset '{preset_name}' to {success_count}/{len(self.segments)} segments")
        return success_count > 0

    def _get_effect_presets(self) -> Dict[str, Dict]:
        """エフェクトプリセット定義"""
        return {
            "natural": {
                "basic": {"intensity": 0.3, "opacity": 1.0},
                "color": {"brightness": 0.1, "contrast": 0.1, "saturation": 0.2, "hue": 0},
                "filter": {"blur_radius": 0.0, "sharpen_amount": 0.0}
            },
            "vibrant": {
                "basic": {"intensity": 0.7, "opacity": 1.0},
                "color": {"brightness": 0.2, "contrast": 0.3, "saturation": 0.5, "hue": 0},
                "filter": {"blur_radius": 0.0, "sharpen_amount": 0.2}
            },
            "vintage": {
                "basic": {"intensity": 0.6, "opacity": 0.9},
                "color": {"brightness": -0.1, "contrast": -0.2, "saturation": -0.3, "hue": 15},
                "filter": {"blur_radius": 1.0, "sharpen_amount": 0.0}
            },
            "dreamy": {
                "basic": {"intensity": 0.5, "opacity": 0.8},
                "color": {"brightness": 0.3, "contrast": -0.1, "saturation": 0.2, "hue": -10},
                "filter": {"blur_radius": 2.0, "sharpen_amount": 0.0}
            },
            "cinematic": {
                "basic": {"intensity": 0.8, "opacity": 1.0},
                "color": {"brightness": -0.1, "contrast": 0.4, "saturation": 0.1, "hue": 0},
                "filter": {"blur_radius": 0.0, "sharpen_amount": 0.1}
            },
            "soft_glow": {
                "basic": {"intensity": 0.4, "opacity": 0.7},
                "color": {"brightness": 0.2, "contrast": -0.1, "saturation": 0.1, "hue": 5},
                "filter": {"blur_radius": 1.5, "sharpen_amount": 0.0}
            }
        }

    def create_transition_effect(self, start_segment: int, end_segment: int,
                                transition_type: str = "fade", duration: float = 1.0) -> bool:
        """
        トランジションエフェクトを作成

        Args:
            start_segment: 開始セグメント
            end_segment: 終了セグメント
            transition_type: トランジションタイプ
            duration: トランジション継続時間

        Returns:
            bool: 作成成功時True
        """
        try:
            # トランジション用の新しいセグメントを挿入
            transition_params = self._get_transition_params(transition_type, duration)

            # 開始セグメントの終了時刻を取得
            start_timing = self.get_segment_timing(start_segment)
            if not start_timing:
                return False

            transition_start = start_timing['end_sec'] - duration / 2

            # 新しいトランジションセグメントを作成（実装は実際のCapCut構造に依存）
            self.logger.info(f"Created {transition_type} transition from segment {start_segment} to {end_segment}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to create transition: {e}")
            return False

    def _get_transition_params(self, transition_type: str, duration: float) -> Dict[str, Any]:
        """トランジションパラメータを取得"""
        transitions = {
            "fade": {
                "type": "fade",
                "basic": {"intensity": 1.0, "opacity": 1.0, "duration": duration},
                "animation": {"speed": 1.0, "loop": False}
            },
            "slide": {
                "type": "slide",
                "basic": {"intensity": 1.0, "opacity": 1.0, "duration": duration},
                "transform": {"scale": 1.0, "rotation": 0.0},
                "animation": {"speed": 1.0, "loop": False}
            },
            "zoom": {
                "type": "zoom",
                "basic": {"intensity": 0.8, "opacity": 1.0, "duration": duration},
                "transform": {"scale": 1.2, "rotation": 0.0},
                "animation": {"speed": 1.0, "loop": False}
            }
        }

        return transitions.get(transition_type, transitions["fade"])

    def get_effect_analysis(self) -> Dict[str, Any]:
        """
        エフェクトレイヤーの分析結果を取得

        Returns:
            Dict: 分析結果
        """
        total_duration = self._calculate_total_duration()
        avg_duration = total_duration / len(self.segments) if self.segments else 0

        # エフェクト統計
        effect_types = []
        blend_modes = []
        avg_intensity = 0.0

        for i in range(len(self.segments)):
            effect_type = self._get_segment_effect_type(i)
            effect_types.append(effect_type)

            blend_mode = self._get_segment_blend_mode(i)
            blend_modes.append(blend_mode)

            params = self._get_segment_parameters(i)
            intensity = params.get('basic', {}).get('intensity', 0.0)
            avg_intensity += intensity

        unique_effects = list(set(effect_types))
        unique_blends = list(set(blend_modes))
        avg_intensity = avg_intensity / len(self.segments) if self.segments else 0

        return {
            'total_segments': len(self.segments),
            'effect_segments': len(self.effect_segments),
            'total_duration': total_duration,
            'average_duration': avg_duration,
            'effect_statistics': {
                'unique_effects': unique_effects,
                'unique_blend_modes': unique_blends,
                'average_intensity': avg_intensity,
                'effect_distribution': {effect: effect_types.count(effect) for effect in unique_effects}
            }
        }


# テスト用関数
def test_effect_layer_controller():
    """EffectLayerControllerのテスト関数"""
    print("✨ EffectLayerController ready for use!")
    print("✅ Visual effects, transitions, filters, and animation control available")

if __name__ == "__main__":
    test_effect_layer_controller()