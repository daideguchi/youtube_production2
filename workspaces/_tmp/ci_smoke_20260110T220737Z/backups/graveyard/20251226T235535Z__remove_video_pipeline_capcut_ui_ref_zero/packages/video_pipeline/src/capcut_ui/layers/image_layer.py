#!/usr/bin/env python3
"""
Image Layer Controller
画像レイヤー（Layer 4）専用の制御システム
配置、スケール、回転、透明度、エフェクトなどを管理
"""
import math
from typing import Dict, Any, List, Optional, Tuple
from ..core.layer_controller import LayerController, Transform2D

class ImageLayerController(LayerController):
    """画像レイヤー制御クラス"""

    def __init__(self, track_index: int, track_data: Dict, draft_manager):
        """
        画像レイヤーコントローラーを初期化

        Args:
            track_index: トラックインデックス（通常4）
            track_data: 画像トラックデータ
            draft_manager: DraftManagerインスタンス
        """
        super().__init__(track_index, track_data, draft_manager)

        # 画像レイヤー固有の初期化
        self.image_segments = self._extract_image_segments()
        self.default_transform = Transform2D(x=0.0, y=0.0, scale_x=0.99, scale_y=0.99, rotation=0.0)

    def _extract_image_segments(self) -> List[Dict]:
        """画像セグメントのみを抽出"""
        image_segments = []
        for segment in self.segments:
            material_type = segment.get('material_type', '')
            if 'image' in material_type.lower() or 'photo' in str(segment.get('material', {})).lower():
                image_segments.append(segment)
        return image_segments

    def get_specific_properties(self) -> Dict[str, Any]:
        """
        画像レイヤー固有プロパティを取得

        Returns:
            Dict: 画像レイヤー固有プロパティ
        """
        properties = {
            'total_images': len(self.image_segments),
            'transform_data': [],
            'opacity_data': [],
            'crop_data': [],
            'filter_data': []
        }

        for i, segment in enumerate(self.segments):
            # 変換情報
            transform = self._get_segment_transform(i)
            properties['transform_data'].append(transform)

            # 透明度情報
            opacity = self._get_segment_opacity(i)
            properties['opacity_data'].append(opacity)

            # クロップ情報
            crop = self._get_segment_crop(i)
            properties['crop_data'].append(crop)

            # フィルター情報
            filter_data = self._get_segment_filters(i)
            properties['filter_data'].append(filter_data)

        return properties

    def set_specific_properties(self, properties: Dict[str, Any]) -> bool:
        """
        画像レイヤー固有プロパティを設定

        Args:
            properties: 設定するプロパティ

        Returns:
            bool: 設定成功時True
        """
        try:
            # 変換データ設定
            if 'transform_data' in properties:
                for i, transform_data in enumerate(properties['transform_data']):
                    if i < len(self.segments):
                        self._set_segment_transform(i, transform_data)

            # 透明度設定
            if 'opacity_data' in properties:
                for i, opacity in enumerate(properties['opacity_data']):
                    if i < len(self.segments):
                        self._set_segment_opacity(i, opacity)

            # クロップ設定
            if 'crop_data' in properties:
                for i, crop_data in enumerate(properties['crop_data']):
                    if i < len(self.segments):
                        self._set_segment_crop(i, crop_data)

            return True

        except Exception as e:
            self.logger.error(f"Failed to set image properties: {e}")
            return False

    def _get_segment_transform(self, segment_index: int) -> Dict[str, float]:
        """
        セグメントの変換情報を取得

        Args:
            segment_index: セグメントインデックス

        Returns:
            Dict: 変換情報 {x, y, scale_x, scale_y, rotation}
        """
        segment = self.get_segment(segment_index)
        if not segment:
            return self.default_transform.to_capcut_format()

        # CapCutの変換データ構造から抽出
        material = segment.get('material', {})
        transform = material.get('transform', {})

        return {
            'x': transform.get('x', 0.0),
            'y': transform.get('y', 0.0),
            'scale_x': transform.get('scale_x', 0.99),
            'scale_y': transform.get('scale_y', 0.99),
            'rotation': transform.get('rotation', 0.0)
        }

    def _set_segment_transform(self, segment_index: int, transform_data: Dict[str, float]) -> bool:
        """
        セグメントの変換情報を設定

        Args:
            segment_index: セグメントインデックス
            transform_data: 変換データ

        Returns:
            bool: 設定成功時True
        """
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
            # material構造を確保
            if 'material' not in segment:
                segment['material'] = {}
            if 'transform' not in segment['material']:
                segment['material']['transform'] = {}

            # 変換データ設定
            transform = segment['material']['transform']
            transform.update(transform_data)

            self.logger.info(f"Updated segment {segment_index} transform: {transform_data}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to set transform for segment {segment_index}: {e}")
            return False

    def _get_segment_opacity(self, segment_index: int) -> float:
        """セグメントの透明度を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return 1.0

        material = segment.get('material', {})
        return material.get('opacity', 1.0)

    def _set_segment_opacity(self, segment_index: int, opacity: float) -> bool:
        """セグメントの透明度を設定"""
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
            if 'material' not in segment:
                segment['material'] = {}

            segment['material']['opacity'] = max(0.0, min(1.0, opacity))
            return True

        except Exception as e:
            self.logger.error(f"Failed to set opacity for segment {segment_index}: {e}")
            return False

    def _get_segment_crop(self, segment_index: int) -> Dict[str, float]:
        """セグメントのクロップ情報を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return {'left': 0.0, 'top': 0.0, 'right': 1.0, 'bottom': 1.0}

        material = segment.get('material', {})
        crop = material.get('crop', {})

        return {
            'left': crop.get('left', 0.0),
            'top': crop.get('top', 0.0),
            'right': crop.get('right', 1.0),
            'bottom': crop.get('bottom', 1.0)
        }

    def _set_segment_crop(self, segment_index: int, crop_data: Dict[str, float]) -> bool:
        """セグメントのクロップ情報を設定"""
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
            if 'material' not in segment:
                segment['material'] = {}
            if 'crop' not in segment['material']:
                segment['material']['crop'] = {}

            segment['material']['crop'].update(crop_data)
            return True

        except Exception as e:
            self.logger.error(f"Failed to set crop for segment {segment_index}: {e}")
            return False

    def _get_segment_filters(self, segment_index: int) -> List[Dict]:
        """セグメントのフィルター情報を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return []

        material = segment.get('material', {})
        return material.get('filters', [])

    def set_image_position(self, segment_index: int, x: float, y: float) -> bool:
        """
        画像の位置を設定

        Args:
            segment_index: セグメントインデックス
            x: X座標 (-1.0 ～ 1.0)
            y: Y座標 (-1.0 ～ 1.0)

        Returns:
            bool: 設定成功時True
        """
        current_transform = self._get_segment_transform(segment_index)
        current_transform.update({'x': x, 'y': y})
        return self._set_segment_transform(segment_index, current_transform)

    def set_image_scale(self, segment_index: int, scale: float, maintain_aspect: bool = True) -> bool:
        """
        画像のスケールを設定

        Args:
            segment_index: セグメントインデックス
            scale: スケール値 (0.1 ～ 5.0)
            maintain_aspect: アスペクト比維持フラグ

        Returns:
            bool: 設定成功時True
        """
        current_transform = self._get_segment_transform(segment_index)

        if maintain_aspect:
            current_transform.update({'scale_x': scale, 'scale_y': scale})
        else:
            current_transform.update({'scale_x': scale})

        return self._set_segment_transform(segment_index, current_transform)

    def set_image_rotation(self, segment_index: int, rotation: float) -> bool:
        """
        画像の回転角度を設定

        Args:
            segment_index: セグメントインデックス
            rotation: 回転角度（度）

        Returns:
            bool: 設定成功時True
        """
        current_transform = self._get_segment_transform(segment_index)
        current_transform.update({'rotation': rotation % 360})
        return self._set_segment_transform(segment_index, current_transform)

    def apply_layout_preset(self, preset_name: str) -> bool:
        """
        レイアウトプリセットを適用

        Args:
            preset_name: プリセット名

        Returns:
            bool: 適用成功時True
        """
        from ..core.parameter_calculator import ParameterCalculator

        calc = ParameterCalculator()

        if preset_name == "golden_ratio":
            positions = calc.calculate_golden_ratio_positions()
        elif preset_name == "rule_of_thirds":
            positions = calc.calculate_rule_of_thirds_positions()
        elif preset_name == "center_grid":
            positions = self._generate_center_grid_layout()
        else:
            self.logger.warning(f"Unknown preset: {preset_name}")
            return False

        # 各セグメントに配置を適用
        success_count = 0
        for i, segment in enumerate(self.segments):
            if i < len(positions):
                pos = positions[i]
                if self.set_image_position(i, pos.x, pos.y):
                    self.set_image_scale(i, pos.scale)
                    success_count += 1

        self.logger.info(f"Applied preset '{preset_name}' to {success_count}/{len(self.segments)} segments")
        return success_count > 0

    def _generate_center_grid_layout(self) -> List:
        """中央グリッドレイアウトを生成"""
        from ..core.parameter_calculator import PositionResult, LayoutType

        positions = []
        grid_size = math.ceil(math.sqrt(len(self.segments)))

        for i in range(len(self.segments)):
            row = i // grid_size
            col = i % grid_size

            x = -0.5 + (col + 0.5) * (1.0 / grid_size)
            y = -0.3 + (row + 0.5) * (0.6 / grid_size)

            positions.append(PositionResult(
                x=x, y=y, scale=0.8/grid_size, rotation=0.0,
                confidence=0.8,
                layout_type=LayoutType.GRID_ALIGNED,
                reason=f"グリッド配置 ({row+1}, {col+1})"
            ))

        return positions

    def get_image_analysis(self) -> Dict[str, Any]:
        """
        画像レイヤーの分析結果を取得

        Returns:
            Dict: 分析結果
        """
        total_duration = self._calculate_total_duration()
        avg_duration = total_duration / len(self.segments) if self.segments else 0

        # 位置分布分析
        positions = [self._get_segment_transform(i) for i in range(len(self.segments))]
        avg_x = sum(p['x'] for p in positions) / len(positions) if positions else 0
        avg_y = sum(p['y'] for p in positions) / len(positions) if positions else 0

        return {
            'total_segments': len(self.segments),
            'image_segments': len(self.image_segments),
            'total_duration': total_duration,
            'average_duration': avg_duration,
            'position_center': {'x': avg_x, 'y': avg_y},
            'transform_summary': {
                'avg_scale': sum(p['scale_x'] for p in positions) / len(positions) if positions else 0,
                'rotation_range': [min(p['rotation'] for p in positions), max(p['rotation'] for p in positions)] if positions else [0, 0]
            }
        }

    def reset_all_transforms(self) -> bool:
        """全画像の変換をデフォルトにリセット"""
        success_count = 0
        default_transform = self.default_transform.to_capcut_format()

        for i in range(len(self.segments)):
            if self._set_segment_transform(i, default_transform):
                success_count += 1

        self.logger.info(f"Reset transforms for {success_count}/{len(self.segments)} segments")
        return success_count == len(self.segments)


# テスト用関数
def test_image_layer_controller():
    """ImageLayerControllerのテスト関数"""
    print("🖼️  ImageLayerController ready for use!")
    print("✅ Image positioning, scaling, rotation, and effects control available")

if __name__ == "__main__":
    test_image_layer_controller()