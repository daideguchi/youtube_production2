#!/usr/bin/env python3
"""
Layer Controller Base Class
全レイヤータイプの共通機能を提供する基底クラス
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Tuple
import logging

class LayerController(ABC):
    """レイヤー制御の基底クラス"""

    def __init__(self, track_index: int, track_data: Dict, draft_manager):
        """
        レイヤーコントローラーを初期化

        Args:
            track_index: トラックインデックス
            track_data: トラックデータ
            draft_manager: DraftManagerインスタンス
        """
        self.track_index = track_index
        self.track_data = track_data
        self.draft_manager = draft_manager
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

        # レイヤー基本情報
        self.layer_type = track_data.get('type', 'unknown')
        self.attribute = track_data.get('attribute', 0)
        self.segments = track_data.get('segments', [])

    def get_layer_info(self) -> Dict[str, Any]:
        """
        レイヤー基本情報を取得

        Returns:
            Dict: レイヤー情報
        """
        return {
            'track_index': self.track_index,
            'layer_type': self.layer_type,
            'attribute': self.attribute,
            'segment_count': len(self.segments),
            'total_duration': self._calculate_total_duration()
        }

    def get_segments(self) -> List[Dict]:
        """セグメントリストを取得"""
        return self.segments

    def get_segment(self, segment_index: int) -> Optional[Dict]:
        """
        指定インデックスのセグメントを取得

        Args:
            segment_index: セグメントインデックス

        Returns:
            Dict: セグメント情報、存在しない場合None
        """
        if 0 <= segment_index < len(self.segments):
            return self.segments[segment_index]
        return None

    def _calculate_total_duration(self) -> float:
        """
        レイヤーの総継続時間を計算（秒）

        Returns:
            float: 総継続時間（秒）
        """
        max_end_time = 0
        for segment in self.segments:
            timerange = segment.get('target_timerange', {})
            start = timerange.get('start', 0)
            duration = timerange.get('duration', 0)
            end_time = (start + duration) / 1000000  # マイクロ秒 -> 秒
            max_end_time = max(max_end_time, end_time)
        return max_end_time

    def _microseconds_to_seconds(self, microseconds: int) -> float:
        """マイクロ秒を秒に変換"""
        return microseconds / 1000000

    def _seconds_to_microseconds(self, seconds: float) -> int:
        """秒をマイクロ秒に変換"""
        return int(seconds * 1000000)

    def get_segment_timing(self, segment_index: int) -> Optional[Dict]:
        """
        セグメントのタイミング情報を取得

        Args:
            segment_index: セグメントインデックス

        Returns:
            Dict: {start_sec, duration_sec, end_sec} または None
        """
        segment = self.get_segment(segment_index)
        if not segment:
            return None

        timerange = segment.get('target_timerange', {})
        start_micro = timerange.get('start', 0)
        duration_micro = timerange.get('duration', 0)

        start_sec = self._microseconds_to_seconds(start_micro)
        duration_sec = self._microseconds_to_seconds(duration_micro)
        end_sec = start_sec + duration_sec

        return {
            'start_sec': start_sec,
            'duration_sec': duration_sec,
            'end_sec': end_sec,
            'start_micro': start_micro,
            'duration_micro': duration_micro
        }

    def set_segment_timing(self, segment_index: int, start_sec: float, duration_sec: float) -> bool:
        """
        セグメントのタイミングを設定

        Args:
            segment_index: セグメントインデックス
            start_sec: 開始時間（秒）
            duration_sec: 継続時間（秒）

        Returns:
            bool: 設定成功時True
        """
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
            start_micro = self._seconds_to_microseconds(start_sec)
            duration_micro = self._seconds_to_microseconds(duration_sec)

            if 'target_timerange' not in segment:
                segment['target_timerange'] = {}

            segment['target_timerange']['start'] = start_micro
            segment['target_timerange']['duration'] = duration_micro

            self.logger.info(f"Updated segment {segment_index} timing: {start_sec:.2f}s - {start_sec + duration_sec:.2f}s")
            return True

        except Exception as e:
            self.logger.error(f"Failed to set segment timing: {e}")
            return False

    def set_layer_visibility(self, visible: bool) -> bool:
        """
        レイヤー全体の可視性を設定

        Args:
            visible: 可視性フラグ

        Returns:
            bool: 設定成功時True
        """
        try:
            # CapCutでは、trackレベルでの可視性制御
            if 'flag' not in self.track_data:
                self.track_data['flag'] = 0

            # フラグビット操作（仮実装 - 実際のCapCut仕様に応じて調整が必要）
            if visible:
                self.track_data['flag'] &= ~1  # ビット0をクリア（可視）
            else:
                self.track_data['flag'] |= 1   # ビット0をセット（非可視）

            return True

        except Exception as e:
            self.logger.error(f"Failed to set layer visibility: {e}")
            return False

    @abstractmethod
    def get_specific_properties(self) -> Dict[str, Any]:
        """
        レイヤータイプ固有のプロパティを取得（各サブクラスで実装）

        Returns:
            Dict: レイヤー固有プロパティ
        """
        pass

    @abstractmethod
    def set_specific_properties(self, properties: Dict[str, Any]) -> bool:
        """
        レイヤータイプ固有のプロパティを設定（各サブクラスで実装）

        Args:
            properties: 設定するプロパティ

        Returns:
            bool: 設定成功時True
        """
        pass

    def get_all_properties(self) -> Dict[str, Any]:
        """
        レイヤーの全プロパティを取得

        Returns:
            Dict: 基本プロパティ + 固有プロパティ
        """
        base_properties = {
            'layer_info': self.get_layer_info(),
            'timing_info': [self.get_segment_timing(i) for i in range(len(self.segments))]
        }

        specific_properties = self.get_specific_properties()

        return {**base_properties, **specific_properties}

    def export_layer_config(self) -> Dict[str, Any]:
        """
        レイヤー設定をエクスポート（設定保存用）

        Returns:
            Dict: エクスポート可能な設定データ
        """
        return {
            'track_index': self.track_index,
            'layer_type': self.layer_type,
            'properties': self.get_all_properties(),
            'export_timestamp': self._get_current_timestamp()
        }

    def _get_current_timestamp(self) -> str:
        """現在のタイムスタンプを取得"""
        from datetime import datetime
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def validate_layer_data(self) -> Dict[str, Any]:
        """
        レイヤーデータの整合性チェック

        Returns:
            Dict: バリデーション結果
        """
        try:
            # 基本構造チェック
            if not isinstance(self.segments, list):
                return {'valid': False, 'error': 'Segments is not a list'}

            # 各セグメントのチェック
            for i, segment in enumerate(self.segments):
                if not isinstance(segment, dict):
                    return {'valid': False, 'error': f'Segment {i} is not a dictionary'}

                # タイミング情報チェック
                if 'target_timerange' in segment:
                    timerange = segment['target_timerange']
                    if not isinstance(timerange.get('start'), int) or not isinstance(timerange.get('duration'), int):
                        return {'valid': False, 'error': f'Segment {i} has invalid timing data'}

            return {
                'valid': True,
                'segment_count': len(self.segments),
                'total_duration': self._calculate_total_duration()
            }

        except Exception as e:
            return {'valid': False, 'error': str(e)}


class Transform2D:
    """2D変換行列を扱うヘルパークラス"""

    def __init__(self, x: float = 0.0, y: float = 0.0, scale_x: float = 1.0, scale_y: float = 1.0, rotation: float = 0.0):
        """
        2D変換を初期化

        Args:
            x: X座標オフセット
            y: Y座標オフセット
            scale_x: X軸スケール
            scale_y: Y軸スケール
            rotation: 回転角度（度）
        """
        self.x = x
        self.y = y
        self.scale_x = scale_x
        self.scale_y = scale_y
        self.rotation = rotation

    def to_capcut_format(self) -> Dict[str, Any]:
        """CapCut形式の変換データに変換"""
        import math
        rad = math.radians(self.rotation)
        cos_r = math.cos(rad)
        sin_r = math.sin(rad)

        return {
            'x': self.x,
            'y': self.y,
            'scale_x': self.scale_x,
            'scale_y': self.scale_y,
            'rotation': self.rotation,
            'matrix': [
                cos_r * self.scale_x, -sin_r * self.scale_x,
                sin_r * self.scale_y, cos_r * self.scale_y,
                self.x, self.y
            ]
        }

    @classmethod
    def from_capcut_format(cls, data: Dict[str, Any]) -> 'Transform2D':
        """CapCut形式データから変換オブジェクトを作成"""
        return cls(
            x=data.get('x', 0.0),
            y=data.get('y', 0.0),
            scale_x=data.get('scale_x', 1.0),
            scale_y=data.get('scale_y', 1.0),
            rotation=data.get('rotation', 0.0)
        )


# テスト用関数
def test_layer_controller():
    """LayerControllerのテスト関数"""
    print("LayerController base class defined successfully")
    print("✅ Ready for specific layer implementations")


if __name__ == "__main__":
    test_layer_controller()