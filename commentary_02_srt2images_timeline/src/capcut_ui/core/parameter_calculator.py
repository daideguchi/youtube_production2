#!/usr/bin/env python3
"""
Parameter Calculator
自動配置・適正値算出システム
黄金比配置、三分割法、視覚的バランス計算などを提供
"""
import math
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass
from enum import Enum

class LayoutType(Enum):
    """レイアウトタイプ定義"""
    GOLDEN_RATIO = "golden_ratio"          # 黄金比配置
    RULE_OF_THIRDS = "rule_of_thirds"      # 三分割法
    CENTER_BALANCED = "center_balanced"     # 中央バランス
    DYNAMIC_BALANCE = "dynamic_balance"     # 動的バランス
    GRID_ALIGNED = "grid_aligned"          # グリッド整列
    CUSTOM = "custom"                      # カスタム配置

@dataclass
class PositionResult:
    """配置結果を格納するデータクラス"""
    x: float              # X座標 (-1.0 ～ 1.0)
    y: float              # Y座標 (-1.0 ～ 1.0)
    scale: float          # スケール (0.1 ～ 5.0)
    rotation: float       # 回転角度 (0 ～ 360度)
    confidence: float     # 配置の信頼度 (0.0 ～ 1.0)
    layout_type: LayoutType
    reason: str           # 配置理由の説明

@dataclass
class ContentInfo:
    """コンテンツ情報を格納するデータクラス"""
    width: float          # コンテンツ幅
    height: float         # コンテンツ高さ
    aspect_ratio: float   # アスペクト比
    content_type: str     # コンテンツタイプ (image, text, etc.)
    importance: float     # 重要度 (0.0 ～ 1.0)
    semantic_weight: float # 意味的重み

class ParameterCalculator:
    """パラメータ自動計算のメインクラス"""

    # 定数定義
    GOLDEN_RATIO = 1.618
    CANVAS_ASPECT = 16 / 9  # CapCutの16:9アスペクト比
    SAFE_AREA_MARGIN = 0.1  # セーフエリアマージン

    def __init__(self):
        """パラメータ計算機を初期化"""
        self.canvas_width = 1.0
        self.canvas_height = 1.0 / self.CANVAS_ASPECT

        # 既存要素の位置記録（重複回避用）
        self.occupied_areas: List[Tuple[float, float, float, float]] = []

    def clear_occupied_areas(self):
        """既存要素の位置記録をクリア"""
        self.occupied_areas = []

    def add_occupied_area(self, x: float, y: float, width: float, height: float):
        """
        占有エリアを追加

        Args:
            x, y: 中心座標
            width, height: サイズ
        """
        self.occupied_areas.append((x - width/2, y - height/2, x + width/2, y + height/2))

    def calculate_golden_ratio_positions(self) -> List[PositionResult]:
        """
        黄金比に基づく推奨配置を計算

        Returns:
            List[PositionResult]: 黄金比配置リスト
        """
        positions = []
        phi = self.GOLDEN_RATIO

        # 黄金比分割点
        golden_points_x = [
            -1.0 + 2.0 / phi,      # 左の黄金比点
            1.0 - 2.0 / phi,       # 右の黄金比点
        ]

        golden_points_y = [
            -self.canvas_height + 2.0 * self.canvas_height / phi,  # 上の黄金比点
            self.canvas_height - 2.0 * self.canvas_height / phi,   # 下の黄金比点
        ]

        # 各組み合わせで配置を生成
        for i, x in enumerate(golden_points_x):
            for j, y in enumerate(golden_points_y):
                confidence = 0.9 - (i + j) * 0.1  # 左上ほど高い信頼度

                positions.append(PositionResult(
                    x=x, y=y, scale=0.8, rotation=0.0,
                    confidence=confidence,
                    layout_type=LayoutType.GOLDEN_RATIO,
                    reason=f"黄金比配置 ({['左', '右'][i]}{['上', '下'][j]})"
                ))

        return positions

    def calculate_rule_of_thirds_positions(self) -> List[PositionResult]:
        """
        三分割法に基づく推奨配置を計算

        Returns:
            List[PositionResult]: 三分割法配置リスト
        """
        positions = []

        # 三分割点
        thirds_x = [-2/3, 0, 2/3]
        thirds_y = [-self.canvas_height * 2/3, 0, self.canvas_height * 2/3]

        # 交点での配置（中央以外）
        for i, x in enumerate(thirds_x):
            for j, y in enumerate(thirds_y):
                if i == 1 and j == 1:  # 中央はスキップ
                    continue

                # 角の点ほど高い信頼度
                distance_from_center = math.sqrt(x*x + y*y)
                confidence = min(0.85, 0.5 + distance_from_center * 0.3)

                positions.append(PositionResult(
                    x=x, y=y, scale=0.75, rotation=0.0,
                    confidence=confidence,
                    layout_type=LayoutType.RULE_OF_THIRDS,
                    reason=f"三分割法配置 (交点{i+1}-{j+1})"
                ))

        return positions

    def calculate_dynamic_balance(self, content_list: List[ContentInfo]) -> List[PositionResult]:
        """
        動的バランスに基づく配置を計算

        Args:
            content_list: コンテンツ情報リスト

        Returns:
            List[PositionResult]: 動的バランス配置リスト
        """
        positions = []

        if not content_list:
            return positions

        # 重要度に基づいた配置戦略
        sorted_content = sorted(content_list, key=lambda c: c.importance, reverse=True)

        for i, content in enumerate(sorted_content):
            if i == 0:
                # 最も重要な要素は中央やや上に配置
                pos = PositionResult(
                    x=0.0, y=-0.2, scale=1.0, rotation=0.0,
                    confidence=0.95,
                    layout_type=LayoutType.DYNAMIC_BALANCE,
                    reason="最重要要素 - 中央やや上配置"
                )
            elif i == 1:
                # 2番目は左下に配置
                pos = PositionResult(
                    x=-0.5, y=0.3, scale=0.7, rotation=0.0,
                    confidence=0.8,
                    layout_type=LayoutType.DYNAMIC_BALANCE,
                    reason="2番目要素 - 左下バランス配置"
                )
            else:
                # その他は空いているスペースに配置
                angle = (i - 2) * (2 * math.pi / max(len(content_list) - 2, 1))
                radius = 0.6
                x = radius * math.cos(angle)
                y = radius * math.sin(angle)

                pos = PositionResult(
                    x=x, y=y, scale=0.5, rotation=0.0,
                    confidence=0.6,
                    layout_type=LayoutType.DYNAMIC_BALANCE,
                    reason=f"補助要素 - 円形配置 ({i+1}番目)"
                )

            positions.append(pos)

        return positions

    def calculate_optimal_scale(self, content: ContentInfo, target_position: Tuple[float, float]) -> float:
        """
        最適スケールを計算

        Args:
            content: コンテンツ情報
            target_position: 配置予定位置

        Returns:
            float: 最適スケール値
        """
        x, y = target_position

        # セーフエリア内に収まるスケールを計算
        safe_x = abs(x)
        safe_y = abs(y)

        max_scale_x = (1.0 - safe_x - self.SAFE_AREA_MARGIN) * 2 / content.width
        max_scale_y = (self.canvas_height - safe_y - self.SAFE_AREA_MARGIN) * 2 / content.height

        # 重要度に応じたスケール調整
        importance_factor = 0.5 + content.importance * 0.5
        optimal_scale = min(max_scale_x, max_scale_y) * importance_factor

        # スケール範囲を制限
        return max(0.1, min(2.0, optimal_scale))

    def check_collision(self, x: float, y: float, width: float, height: float) -> bool:
        """
        他の要素との衝突チェック

        Args:
            x, y: 中心座標
            width, height: サイズ

        Returns:
            bool: 衝突する場合True
        """
        left = x - width / 2
        right = x + width / 2
        top = y - height / 2
        bottom = y + height / 2

        for occupied in self.occupied_areas:
            occ_left, occ_top, occ_right, occ_bottom = occupied

            # AABB衝突判定
            if (left < occ_right and right > occ_left and
                top < occ_bottom and bottom > occ_top):
                return True

        return False

    def find_collision_free_position(self, preferred_x: float, preferred_y: float,
                                   width: float, height: float, max_attempts: int = 20) -> Tuple[float, float, float]:
        """
        衝突のない配置を探索

        Args:
            preferred_x, preferred_y: 希望座標
            width, height: サイズ
            max_attempts: 最大試行回数

        Returns:
            Tuple[float, float, float]: (x, y, confidence)
        """
        if not self.check_collision(preferred_x, preferred_y, width, height):
            return preferred_x, preferred_y, 1.0

        # スパイラル探索
        for attempt in range(max_attempts):
            angle = attempt * 0.5
            radius = (attempt + 1) * 0.1

            test_x = preferred_x + radius * math.cos(angle)
            test_y = preferred_y + radius * math.sin(angle)

            # 画面内チェック
            if (abs(test_x) <= 1.0 - width/2 - self.SAFE_AREA_MARGIN and
                abs(test_y) <= self.canvas_height - height/2 - self.SAFE_AREA_MARGIN):

                if not self.check_collision(test_x, test_y, width, height):
                    confidence = max(0.3, 1.0 - attempt * 0.05)
                    return test_x, test_y, confidence

        # 見つからない場合は希望位置を返す（低信頼度）
        return preferred_x, preferred_y, 0.1

    def calculate_text_positioning(self, text_content: str, font_size: float,
                                 image_positions: List[Tuple[float, float]]) -> PositionResult:
        """
        テキストの最適配置を計算

        Args:
            text_content: テキスト内容
            font_size: フォントサイズ
            image_positions: 既存画像の位置リスト

        Returns:
            PositionResult: テキスト配置結果
        """
        # テキスト長に基づくサイズ推定
        estimated_width = len(text_content) * font_size * 0.6
        estimated_height = font_size * 1.2

        # 画像との重複を避けて配置
        preferred_positions = [
            (0.0, -0.8),    # 上部中央
            (0.0, 0.8),     # 下部中央
            (-0.7, 0.0),    # 左中央
            (0.7, 0.0),     # 右中央
        ]

        for x, y in preferred_positions:
            final_x, final_y, confidence = self.find_collision_free_position(
                x, y, estimated_width, estimated_height
            )

            if confidence > 0.5:
                return PositionResult(
                    x=final_x, y=final_y, scale=1.0, rotation=0.0,
                    confidence=confidence,
                    layout_type=LayoutType.DYNAMIC_BALANCE,
                    reason="テキスト専用配置 - 画像との重複回避"
                )

        # フォールバック: 下部配置
        return PositionResult(
            x=0.0, y=0.6, scale=0.8, rotation=0.0,
            confidence=0.4,
            layout_type=LayoutType.CENTER_BALANCED,
            reason="テキスト配置 - フォールバック下部"
        )

    def get_preset_layout(self, layout_name: str) -> Dict[str, PositionResult]:
        """
        プリセットレイアウトを取得

        Args:
            layout_name: レイアウト名

        Returns:
            Dict[str, PositionResult]: レイヤー名: 配置情報のマップ
        """
        presets = {
            "classic_center": {
                "main_image": PositionResult(0.0, 0.0, 0.8, 0.0, 0.9, LayoutType.CENTER_BALANCED, "クラシック中央配置"),
                "title": PositionResult(0.0, -0.7, 1.0, 0.0, 0.8, LayoutType.CENTER_BALANCED, "上部タイトル"),
                "subtitle": PositionResult(0.0, 0.7, 0.8, 0.0, 0.7, LayoutType.CENTER_BALANCED, "下部サブタイトル")
            },
            "golden_magazine": {
                "main_image": PositionResult(-0.3, -0.2, 1.0, 0.0, 0.95, LayoutType.GOLDEN_RATIO, "黄金比メイン"),
                "title": PositionResult(0.4, -0.4, 1.2, 0.0, 0.9, LayoutType.GOLDEN_RATIO, "右上タイトル"),
                "accent": PositionResult(0.5, 0.3, 0.6, 0.0, 0.8, LayoutType.GOLDEN_RATIO, "右下アクセント")
            },
            "dynamic_three": {
                "primary": PositionResult(0.0, -0.3, 1.0, 0.0, 0.95, LayoutType.DYNAMIC_BALANCE, "プライマリ"),
                "secondary": PositionResult(-0.5, 0.4, 0.7, 0.0, 0.8, LayoutType.DYNAMIC_BALANCE, "セカンダリ"),
                "tertiary": PositionResult(0.5, 0.4, 0.7, 0.0, 0.8, LayoutType.DYNAMIC_BALANCE, "ターシャリ")
            }
        }

        return presets.get(layout_name, {})

# テスト用関数
def test_parameter_calculator():
    """ParameterCalculatorのテスト関数"""
    calc = ParameterCalculator()

    print("🧮 ParameterCalculator Test")
    print("=" * 40)

    # 黄金比配置テスト
    golden_positions = calc.calculate_golden_ratio_positions()
    print(f"✅ Golden ratio positions: {len(golden_positions)}")
    for pos in golden_positions[:2]:  # 最初の2つのみ表示
        print(f"   {pos.reason}: ({pos.x:.2f}, {pos.y:.2f}) confidence: {pos.confidence:.2f}")

    # 三分割法テスト
    thirds_positions = calc.calculate_rule_of_thirds_positions()
    print(f"✅ Rule of thirds positions: {len(thirds_positions)}")

    # プリセットテスト
    preset = calc.get_preset_layout("golden_magazine")
    print(f"✅ Preset layout: {len(preset)} elements")

    print("🎯 ParameterCalculator ready for use!")

if __name__ == "__main__":
    test_parameter_calculator()