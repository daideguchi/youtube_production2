#!/usr/bin/env python3
"""
Audio Layer Controller
オーディオレイヤー（Layer 1,2）専用の制御システム
音量、フェード、イコライザー、空間音響、タイミングなどを管理
"""
import math
from typing import Dict, Any, List, Optional, Tuple
from ..core.layer_controller import LayerController

class AudioParameters:
    """オーディオパラメータ定義クラス"""

    def __init__(self):
        # 基本音響設定
        self.volume = 1.0           # 音量 (0.0-2.0)
        self.mute = False           # ミュート状態
        self.pan = 0.0              # パン (-1.0=左 ～ 1.0=右)

        # フェード設定
        self.fade_in_duration = 0.0    # フェードイン時間（秒）
        self.fade_out_duration = 0.0   # フェードアウト時間（秒）
        self.fade_in_curve = "linear"  # フェードカーブ (linear, exponential, logarithmic)
        self.fade_out_curve = "linear"

        # イコライザー設定（周波数別ゲイン）
        self.eq_low = 0.0          # 低音 (-12dB ～ 12dB)
        self.eq_mid = 0.0          # 中音 (-12dB ～ 12dB)
        self.eq_high = 0.0         # 高音 (-12dB ～ 12dB)

        # エフェクト設定
        self.reverb_amount = 0.0    # リバーブ量 (0.0-1.0)
        self.reverb_room_size = 0.5 # ルームサイズ (0.0-1.0)
        self.echo_enabled = False   # エコー有効
        self.echo_delay = 0.2      # エコー遅延（秒）
        self.echo_decay = 0.3      # エコー減衰率

        # 音質調整
        self.pitch = 0.0           # ピッチシフト（半音、-12 ～ 12）
        self.speed = 1.0           # 再生速度 (0.5-2.0)
        self.noise_reduction = 0.0  # ノイズリダクション (0.0-1.0)

        # 空間音響
        self.spatial_enabled = False    # 空間音響有効
        self.distance_factor = 1.0     # 距離減衰係数
        self.doppler_effect = 0.0      # ドップラー効果強度

    def to_capcut_format(self) -> Dict[str, Any]:
        """CapCut形式のオーディオパラメータに変換"""
        return {
            "basic": {
                "volume": self.volume,
                "mute": self.mute,
                "pan": self.pan
            },
            "fade": {
                "fade_in": {
                    "duration": self.fade_in_duration,
                    "curve": self.fade_in_curve
                },
                "fade_out": {
                    "duration": self.fade_out_duration,
                    "curve": self.fade_out_curve
                }
            },
            "eq": {
                "low": self.eq_low,
                "mid": self.eq_mid,
                "high": self.eq_high
            },
            "effects": {
                "reverb": {
                    "amount": self.reverb_amount,
                    "room_size": self.reverb_room_size
                },
                "echo": {
                    "enabled": self.echo_enabled,
                    "delay": self.echo_delay,
                    "decay": self.echo_decay
                }
            },
            "quality": {
                "pitch": self.pitch,
                "speed": self.speed,
                "noise_reduction": self.noise_reduction
            },
            "spatial": {
                "enabled": self.spatial_enabled,
                "distance_factor": self.distance_factor,
                "doppler_effect": self.doppler_effect
            }
        }

    @classmethod
    def from_capcut_format(cls, data: Dict[str, Any]) -> 'AudioParameters':
        """CapCut形式データからオーディオパラメータを作成"""
        params = cls()

        if 'basic' in data:
            basic = data['basic']
            params.volume = basic.get('volume', 1.0)
            params.mute = basic.get('mute', False)
            params.pan = basic.get('pan', 0.0)

        if 'fade' in data:
            fade = data['fade']
            if 'fade_in' in fade:
                params.fade_in_duration = fade['fade_in'].get('duration', 0.0)
                params.fade_in_curve = fade['fade_in'].get('curve', 'linear')
            if 'fade_out' in fade:
                params.fade_out_duration = fade['fade_out'].get('duration', 0.0)
                params.fade_out_curve = fade['fade_out'].get('curve', 'linear')

        if 'eq' in data:
            eq = data['eq']
            params.eq_low = eq.get('low', 0.0)
            params.eq_mid = eq.get('mid', 0.0)
            params.eq_high = eq.get('high', 0.0)

        if 'effects' in data:
            effects = data['effects']
            if 'reverb' in effects:
                reverb = effects['reverb']
                params.reverb_amount = reverb.get('amount', 0.0)
                params.reverb_room_size = reverb.get('room_size', 0.5)
            if 'echo' in effects:
                echo = effects['echo']
                params.echo_enabled = echo.get('enabled', False)
                params.echo_delay = echo.get('delay', 0.2)
                params.echo_decay = echo.get('decay', 0.3)

        return params


class AudioLayerController(LayerController):
    """オーディオレイヤー制御クラス"""

    def __init__(self, track_index: int, track_data: Dict, draft_manager):
        """
        オーディオレイヤーコントローラーを初期化

        Args:
            track_index: トラックインデックス（1,2）
            track_data: オーディオトラックデータ
            draft_manager: DraftManagerインスタンス
        """
        super().__init__(track_index, track_data, draft_manager)

        # オーディオレイヤー固有の初期化
        self.audio_segments = self._extract_audio_segments()
        self.default_params = AudioParameters()

        # レイヤータイプ判定
        self.layer_role = self._determine_layer_role()

    def _extract_audio_segments(self) -> List[Dict]:
        """オーディオセグメントのみを抽出"""
        audio_segments = []
        for segment in self.segments:
            material_type = segment.get('material_type', '')
            if 'audio' in material_type.lower() or 'sound' in str(segment.get('material', {})).lower():
                audio_segments.append(segment)
        return audio_segments

    def _determine_layer_role(self) -> str:
        """レイヤーの役割を判定"""
        if self.track_index == 1:
            return "main_audio"      # メインオーディオ・ナレーション
        elif self.track_index == 2:
            return "background_music" # BGM
        else:
            return "unknown_audio"

    def get_specific_properties(self) -> Dict[str, Any]:
        """
        オーディオレイヤー固有プロパティを取得

        Returns:
            Dict: オーディオレイヤー固有プロパティ
        """
        properties = {
            'layer_role': self.layer_role,
            'total_audio_segments': len(self.audio_segments),
            'audio_parameters': [],
            'volume_levels': [],
            'fade_settings': [],
            'eq_settings': []
        }

        for i, segment in enumerate(self.segments):
            # オーディオパラメータ
            params = self._get_segment_parameters(i)
            properties['audio_parameters'].append(params)

            # 音量レベル
            volume = self._get_segment_volume(i)
            properties['volume_levels'].append(volume)

            # フェード設定
            fade = self._get_segment_fade(i)
            properties['fade_settings'].append(fade)

            # EQ設定
            eq = self._get_segment_eq(i)
            properties['eq_settings'].append(eq)

        return properties

    def set_specific_properties(self, properties: Dict[str, Any]) -> bool:
        """
        オーディオレイヤー固有プロパティを設定

        Args:
            properties: 設定するプロパティ

        Returns:
            bool: 設定成功時True
        """
        try:
            # オーディオパラメータ設定
            if 'audio_parameters' in properties:
                for i, params_data in enumerate(properties['audio_parameters']):
                    if i < len(self.segments):
                        self._set_segment_parameters(i, params_data)

            # 音量レベル設定
            if 'volume_levels' in properties:
                for i, volume in enumerate(properties['volume_levels']):
                    if i < len(self.segments):
                        self._set_segment_volume(i, volume)

            return True

        except Exception as e:
            self.logger.error(f"Failed to set audio properties: {e}")
            return False

    def _get_segment_parameters(self, segment_index: int) -> Dict[str, Any]:
        """セグメントのオーディオパラメータを取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return self.default_params.to_capcut_format()

        material = segment.get('material', {})
        params = material.get('audio_params', {})

        if not params:
            return self.default_params.to_capcut_format()

        return params

    def _set_segment_parameters(self, segment_index: int, params_data: Dict[str, Any]) -> bool:
        """セグメントのオーディオパラメータを設定"""
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
            if 'material' not in segment:
                segment['material'] = {}

            segment['material']['audio_params'] = params_data

            self.logger.info(f"Updated segment {segment_index} audio parameters")
            return True

        except Exception as e:
            self.logger.error(f"Failed to set audio parameters for segment {segment_index}: {e}")
            return False

    def _get_segment_volume(self, segment_index: int) -> float:
        """セグメントの音量を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return 1.0

        material = segment.get('material', {})
        params = material.get('audio_params', {})
        return params.get('basic', {}).get('volume', 1.0)

    def _set_segment_volume(self, segment_index: int, volume: float) -> bool:
        """セグメントの音量を設定"""
        current_params = self._get_segment_parameters(segment_index)

        if 'basic' not in current_params:
            current_params['basic'] = {}

        current_params['basic']['volume'] = max(0.0, min(2.0, volume))
        return self._set_segment_parameters(segment_index, current_params)

    def _get_segment_fade(self, segment_index: int) -> Dict[str, Any]:
        """セグメントのフェード設定を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return {'fade_in': 0.0, 'fade_out': 0.0}

        material = segment.get('material', {})
        params = material.get('audio_params', {})
        fade = params.get('fade', {})

        return {
            'fade_in': fade.get('fade_in', {}).get('duration', 0.0),
            'fade_out': fade.get('fade_out', {}).get('duration', 0.0)
        }

    def _get_segment_eq(self, segment_index: int) -> Dict[str, float]:
        """セグメントのEQ設定を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return {'low': 0.0, 'mid': 0.0, 'high': 0.0}

        material = segment.get('material', {})
        params = material.get('audio_params', {})
        return params.get('eq', {'low': 0.0, 'mid': 0.0, 'high': 0.0})

    def set_volume(self, segment_index: int, volume: float) -> bool:
        """
        音量を設定

        Args:
            segment_index: セグメントインデックス
            volume: 音量 (0.0-2.0)

        Returns:
            bool: 設定成功時True
        """
        return self._set_segment_volume(segment_index, volume)

    def set_fade(self, segment_index: int, fade_in: float = None, fade_out: float = None) -> bool:
        """
        フェードを設定

        Args:
            segment_index: セグメントインデックス
            fade_in: フェードイン時間（秒）
            fade_out: フェードアウト時間（秒）

        Returns:
            bool: 設定成功時True
        """
        current_params = self._get_segment_parameters(segment_index)

        if 'fade' not in current_params:
            current_params['fade'] = {}

        if fade_in is not None:
            if 'fade_in' not in current_params['fade']:
                current_params['fade']['fade_in'] = {}
            current_params['fade']['fade_in']['duration'] = max(0.0, fade_in)

        if fade_out is not None:
            if 'fade_out' not in current_params['fade']:
                current_params['fade']['fade_out'] = {}
            current_params['fade']['fade_out']['duration'] = max(0.0, fade_out)

        return self._set_segment_parameters(segment_index, current_params)

    def set_pan(self, segment_index: int, pan: float) -> bool:
        """
        パンを設定

        Args:
            segment_index: セグメントインデックス
            pan: パン位置 (-1.0=左 ～ 1.0=右)

        Returns:
            bool: 設定成功時True
        """
        current_params = self._get_segment_parameters(segment_index)

        if 'basic' not in current_params:
            current_params['basic'] = {}

        current_params['basic']['pan'] = max(-1.0, min(1.0, pan))
        return self._set_segment_parameters(segment_index, current_params)

    def set_eq(self, segment_index: int, low: float = None, mid: float = None, high: float = None) -> bool:
        """
        イコライザーを設定

        Args:
            segment_index: セグメントインデックス
            low: 低音ゲイン (-12dB ～ 12dB)
            mid: 中音ゲイン (-12dB ～ 12dB)
            high: 高音ゲイン (-12dB ～ 12dB)

        Returns:
            bool: 設定成功時True
        """
        current_params = self._get_segment_parameters(segment_index)

        if 'eq' not in current_params:
            current_params['eq'] = {}

        if low is not None:
            current_params['eq']['low'] = max(-12.0, min(12.0, low))
        if mid is not None:
            current_params['eq']['mid'] = max(-12.0, min(12.0, mid))
        if high is not None:
            current_params['eq']['high'] = max(-12.0, min(12.0, high))

        return self._set_segment_parameters(segment_index, current_params)

    def mute_segment(self, segment_index: int, muted: bool = True) -> bool:
        """
        セグメントをミュート

        Args:
            segment_index: セグメントインデックス
            muted: ミュート状態

        Returns:
            bool: 設定成功時True
        """
        current_params = self._get_segment_parameters(segment_index)

        if 'basic' not in current_params:
            current_params['basic'] = {}

        current_params['basic']['mute'] = muted
        return self._set_segment_parameters(segment_index, current_params)

    def apply_audio_preset(self, preset_name: str) -> bool:
        """
        オーディオプリセットを適用

        Args:
            preset_name: プリセット名

        Returns:
            bool: 適用成功時True
        """
        presets = self._get_audio_presets()

        if preset_name not in presets:
            self.logger.warning(f"Unknown audio preset: {preset_name}")
            return False

        preset_params = presets[preset_name]

        success_count = 0
        for i in range(len(self.segments)):
            if self._set_segment_parameters(i, preset_params):
                success_count += 1

        self.logger.info(f"Applied audio preset '{preset_name}' to {success_count}/{len(self.segments)} segments")
        return success_count > 0

    def _get_audio_presets(self) -> Dict[str, Dict]:
        """オーディオプリセット定義"""
        return {
            "clear_voice": {
                "basic": {"volume": 1.2, "pan": 0.0},
                "eq": {"low": -2.0, "mid": 3.0, "high": 2.0},
                "effects": {"noise_reduction": 0.3},
                "fade": {"fade_in": {"duration": 0.1}, "fade_out": {"duration": 0.1}}
            },
            "warm_voice": {
                "basic": {"volume": 1.0, "pan": 0.0},
                "eq": {"low": 2.0, "mid": 1.0, "high": -1.0},
                "effects": {"reverb": {"amount": 0.1, "room_size": 0.3}},
                "fade": {"fade_in": {"duration": 0.2}, "fade_out": {"duration": 0.2}}
            },
            "background_music": {
                "basic": {"volume": 0.6, "pan": 0.0},
                "eq": {"low": 0.0, "mid": -1.0, "high": -2.0},
                "effects": {"reverb": {"amount": 0.2, "room_size": 0.6}},
                "fade": {"fade_in": {"duration": 2.0}, "fade_out": {"duration": 2.0}}
            },
            "dramatic": {
                "basic": {"volume": 1.3, "pan": 0.0},
                "eq": {"low": 3.0, "mid": 1.0, "high": 0.0},
                "effects": {"reverb": {"amount": 0.3, "room_size": 0.8}},
                "fade": {"fade_in": {"duration": 0.5}, "fade_out": {"duration": 1.0}}
            },
            "soft_ambient": {
                "basic": {"volume": 0.4, "pan": 0.0},
                "eq": {"low": -1.0, "mid": 0.0, "high": -3.0},
                "effects": {"reverb": {"amount": 0.4, "room_size": 0.9}},
                "fade": {"fade_in": {"duration": 3.0}, "fade_out": {"duration": 3.0}}
            }
        }

    def analyze_audio_levels(self) -> Dict[str, Any]:
        """
        オーディオレベルの分析

        Returns:
            Dict: 音量分析結果
        """
        volumes = []
        fade_ins = []
        fade_outs = []
        eq_settings = []

        for i in range(len(self.segments)):
            volume = self._get_segment_volume(i)
            volumes.append(volume)

            fade = self._get_segment_fade(i)
            fade_ins.append(fade['fade_in'])
            fade_outs.append(fade['fade_out'])

            eq = self._get_segment_eq(i)
            eq_settings.append(eq)

        return {
            'volume_analysis': {
                'average': sum(volumes) / len(volumes) if volumes else 0,
                'max': max(volumes) if volumes else 0,
                'min': min(volumes) if volumes else 0,
                'segments_over_1_0': len([v for v in volumes if v > 1.0]),
                'segments_muted': len([v for v in volumes if v == 0.0])
            },
            'fade_analysis': {
                'avg_fade_in': sum(fade_ins) / len(fade_ins) if fade_ins else 0,
                'avg_fade_out': sum(fade_outs) / len(fade_outs) if fade_outs else 0,
                'segments_with_fade': len([f for f in fade_ins if f > 0]) + len([f for f in fade_outs if f > 0])
            },
            'eq_usage': {
                'segments_with_eq': len([eq for eq in eq_settings if any(abs(v) > 0.1 for v in eq.values())]),
                'common_eq_patterns': self._analyze_eq_patterns(eq_settings)
            }
        }

    def _analyze_eq_patterns(self, eq_settings: List[Dict]) -> Dict[str, int]:
        """EQパターンを分析"""
        patterns = {
            'voice_enhancement': 0,  # mid > 0, high > 0
            'bass_boost': 0,         # low > 2
            'clarity': 0,            # high > 3
            'warmth': 0,             # low > 0, mid > 0, high < 0
            'neutral': 0             # all close to 0
        }

        for eq in eq_settings:
            low, mid, high = eq['low'], eq['mid'], eq['high']

            if mid > 1.0 and high > 1.0:
                patterns['voice_enhancement'] += 1
            elif low > 2.0:
                patterns['bass_boost'] += 1
            elif high > 3.0:
                patterns['clarity'] += 1
            elif low > 0 and mid > 0 and high < 0:
                patterns['warmth'] += 1
            elif all(abs(v) < 0.5 for v in [low, mid, high]):
                patterns['neutral'] += 1

        return patterns

    def get_audio_analysis(self) -> Dict[str, Any]:
        """
        オーディオレイヤーの分析結果を取得

        Returns:
            Dict: 分析結果
        """
        total_duration = self._calculate_total_duration()
        avg_duration = total_duration / len(self.segments) if self.segments else 0

        level_analysis = self.analyze_audio_levels()

        return {
            'layer_role': self.layer_role,
            'total_segments': len(self.segments),
            'audio_segments': len(self.audio_segments),
            'total_duration': total_duration,
            'average_duration': avg_duration,
            **level_analysis
        }


# テスト用関数
def test_audio_layer_controller():
    """AudioLayerControllerのテスト関数"""
    print("🔊 AudioLayerController ready for use!")
    print("✅ Volume, fade, EQ, pan, and audio effects control available")

if __name__ == "__main__":
    test_audio_layer_controller()