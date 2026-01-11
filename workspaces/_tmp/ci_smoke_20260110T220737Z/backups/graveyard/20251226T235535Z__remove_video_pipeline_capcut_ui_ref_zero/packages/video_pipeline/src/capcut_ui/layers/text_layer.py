#!/usr/bin/env python3
"""
Text Layer Controller
テキストレイヤー（Layer 6,7,8）専用の制御システム
字幕、タイトル、フォント、色、位置、アニメーションなどを管理
"""
import json
from typing import Dict, Any, List, Optional, Tuple
from ..core.layer_controller import LayerController, Transform2D

class TextStyle:
    """テキストスタイル定義クラス"""

    def __init__(self):
        # フォント設定
        self.font_family = "Arial"
        self.font_size = 24.0
        self.font_weight = "normal"  # normal, bold, light
        self.font_style = "normal"   # normal, italic

        # 色設定 (RGBA 0.0-1.0)
        self.text_color = [1.0, 1.0, 1.0, 1.0]  # 白
        self.stroke_color = [0.0, 0.0, 0.0, 1.0]  # 黒
        self.background_color = [0.0, 0.0, 0.0, 0.0]  # 透明

        # エフェクト
        self.stroke_width = 2.0
        self.shadow_enabled = False
        self.shadow_offset = [2.0, 2.0]
        self.shadow_color = [0.0, 0.0, 0.0, 0.5]
        self.shadow_blur = 4.0

        # レイアウト
        self.alignment = "center"  # left, center, right
        self.line_spacing = 1.2
        self.letter_spacing = 0.0

        # アニメーション
        self.animation_type = "none"  # none, fade, slide, typewriter
        self.animation_duration = 0.5

    def to_capcut_format(self) -> Dict[str, Any]:
        """CapCut形式のテキストスタイルに変換"""
        return {
            "font": {
                "family": self.font_family,
                "size": self.font_size,
                "weight": self.font_weight,
                "style": self.font_style
            },
            "color": {
                "text": self.text_color,
                "stroke": self.stroke_color,
                "background": self.background_color
            },
            "effects": {
                "stroke_width": self.stroke_width,
                "shadow": {
                    "enabled": self.shadow_enabled,
                    "offset": self.shadow_offset,
                    "color": self.shadow_color,
                    "blur": self.shadow_blur
                }
            },
            "layout": {
                "alignment": self.alignment,
                "line_spacing": self.line_spacing,
                "letter_spacing": self.letter_spacing
            },
            "animation": {
                "type": self.animation_type,
                "duration": self.animation_duration
            }
        }

    @classmethod
    def from_capcut_format(cls, data: Dict[str, Any]) -> 'TextStyle':
        """CapCut形式データからテキストスタイルを作成"""
        style = cls()

        if 'font' in data:
            font = data['font']
            style.font_family = font.get('family', 'Arial')
            style.font_size = font.get('size', 24.0)
            style.font_weight = font.get('weight', 'normal')
            style.font_style = font.get('style', 'normal')

        if 'color' in data:
            color = data['color']
            style.text_color = color.get('text', [1.0, 1.0, 1.0, 1.0])
            style.stroke_color = color.get('stroke', [0.0, 0.0, 0.0, 1.0])
            style.background_color = color.get('background', [0.0, 0.0, 0.0, 0.0])

        if 'effects' in data:
            effects = data['effects']
            style.stroke_width = effects.get('stroke_width', 2.0)
            if 'shadow' in effects:
                shadow = effects['shadow']
                style.shadow_enabled = shadow.get('enabled', False)
                style.shadow_offset = shadow.get('offset', [2.0, 2.0])
                style.shadow_color = shadow.get('color', [0.0, 0.0, 0.0, 0.5])
                style.shadow_blur = shadow.get('blur', 4.0)

        return style


class TextLayerController(LayerController):
    """テキストレイヤー制御クラス"""

    def __init__(self, track_index: int, track_data: Dict, draft_manager):
        """
        テキストレイヤーコントローラーを初期化

        Args:
            track_index: トラックインデックス（6,7,8）
            track_data: テキストトラックデータ
            draft_manager: DraftManagerインスタンス
        """
        super().__init__(track_index, track_data, draft_manager)

        # テキストレイヤー固有の初期化
        self.text_segments = self._extract_text_segments()
        self.default_style = TextStyle()
        self.default_transform = Transform2D(x=0.0, y=0.8, scale_x=1.0, scale_y=1.0, rotation=0.0)

        # レイヤータイプ判定
        self.layer_role = self._determine_layer_role()

    def _extract_text_segments(self) -> List[Dict]:
        """テキストセグメントのみを抽出"""
        text_segments = []
        for segment in self.segments:
            material_type = segment.get('material_type', '')
            if 'text' in material_type.lower() or 'subtitle' in str(segment.get('material', {})).lower():
                text_segments.append(segment)
        return text_segments

    def _determine_layer_role(self) -> str:
        """レイヤーの役割を判定"""
        if self.track_index == 6:
            return "main_subtitles"  # メイン字幕
        elif self.track_index == 7:
            return "title_subtitle"  # タイトル・サブタイトル
        elif self.track_index == 8:
            return "additional_text"  # 追加テキスト
        else:
            return "unknown_text"

    def get_specific_properties(self) -> Dict[str, Any]:
        """
        テキストレイヤー固有プロパティを取得

        Returns:
            Dict: テキストレイヤー固有プロパティ
        """
        properties = {
            'layer_role': self.layer_role,
            'total_texts': len(self.text_segments),
            'text_data': [],
            'style_data': [],
            'transform_data': [],
            'animation_data': []
        }

        for i, segment in enumerate(self.segments):
            # テキスト内容
            text_content = self._get_segment_text(i)
            properties['text_data'].append(text_content)

            # スタイル情報
            style = self._get_segment_style(i)
            properties['style_data'].append(style)

            # 変換情報
            transform = self._get_segment_transform(i)
            properties['transform_data'].append(transform)

            # アニメーション情報
            animation = self._get_segment_animation(i)
            properties['animation_data'].append(animation)

        return properties

    def set_specific_properties(self, properties: Dict[str, Any]) -> bool:
        """
        テキストレイヤー固有プロパティを設定

        Args:
            properties: 設定するプロパティ

        Returns:
            bool: 設定成功時True
        """
        try:
            # テキスト内容設定
            if 'text_data' in properties:
                for i, text_data in enumerate(properties['text_data']):
                    if i < len(self.segments):
                        self._set_segment_text(i, text_data)

            # スタイル設定
            if 'style_data' in properties:
                for i, style_data in enumerate(properties['style_data']):
                    if i < len(self.segments):
                        self._set_segment_style(i, style_data)

            # 変換データ設定
            if 'transform_data' in properties:
                for i, transform_data in enumerate(properties['transform_data']):
                    if i < len(self.segments):
                        self._set_segment_transform(i, transform_data)

            # アニメーション設定
            if 'animation_data' in properties:
                for i, animation_data in enumerate(properties['animation_data']):
                    if i < len(self.segments):
                        self._set_segment_animation(i, animation_data)

            return True

        except Exception as e:
            self.logger.error(f"Failed to set text properties: {e}")
            return False

    def _get_segment_text(self, segment_index: int) -> str:
        """セグメントのテキスト内容を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return ""

        material = segment.get('material', {})
        return material.get('text_content', material.get('content', ''))

    def _set_segment_text(self, segment_index: int, text: str) -> bool:
        """セグメントのテキスト内容を設定"""
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
            if 'material' not in segment:
                segment['material'] = {}

            segment['material']['text_content'] = text
            segment['material']['content'] = text  # 互換性のため

            self.logger.info(f"Updated segment {segment_index} text: {text[:50]}...")
            return True

        except Exception as e:
            self.logger.error(f"Failed to set text for segment {segment_index}: {e}")
            return False

    def _get_segment_style(self, segment_index: int) -> Dict[str, Any]:
        """セグメントのスタイル情報を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return self.default_style.to_capcut_format()

        material = segment.get('material', {})
        style_data = material.get('style', {})

        if not style_data:
            return self.default_style.to_capcut_format()

        return style_data

    def _set_segment_style(self, segment_index: int, style_data: Dict[str, Any]) -> bool:
        """セグメントのスタイル情報を設定"""
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
            if 'material' not in segment:
                segment['material'] = {}
            if 'style' not in segment['material']:
                segment['material']['style'] = {}

            # スタイルデータを適用
            if isinstance(style_data, dict):
                segment['material']['style'].update(style_data)
            else:
                # TextStyleオブジェクトの場合
                segment['material']['style'].update(style_data.to_capcut_format())

            return True

        except Exception as e:
            self.logger.error(f"Failed to set style for segment {segment_index}: {e}")
            return False

    def _get_segment_transform(self, segment_index: int) -> Dict[str, float]:
        """セグメントの変換情報を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return self.default_transform.to_capcut_format()

        material = segment.get('material', {})
        transform = material.get('transform', {})

        return {
            'x': transform.get('x', 0.0),
            'y': transform.get('y', 0.8),  # テキストのデフォルトは下部
            'scale_x': transform.get('scale_x', 1.0),
            'scale_y': transform.get('scale_y', 1.0),
            'rotation': transform.get('rotation', 0.0)
        }

    def _set_segment_transform(self, segment_index: int, transform_data: Dict[str, float]) -> bool:
        """セグメントの変換情報を設定"""
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
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

    def _get_segment_animation(self, segment_index: int) -> Dict[str, Any]:
        """セグメントのアニメーション情報を取得"""
        segment = self.get_segment(segment_index)
        if not segment:
            return {'type': 'none', 'duration': 0.5}

        material = segment.get('material', {})
        return material.get('animation', {'type': 'none', 'duration': 0.5})

    def _set_segment_animation(self, segment_index: int, animation_data: Dict[str, Any]) -> bool:
        """セグメントのアニメーション情報を設定"""
        segment = self.get_segment(segment_index)
        if not segment:
            return False

        try:
            if 'material' not in segment:
                segment['material'] = {}

            segment['material']['animation'] = animation_data
            return True

        except Exception as e:
            self.logger.error(f"Failed to set animation for segment {segment_index}: {e}")
            return False

    def set_text_content(self, segment_index: int, text: str) -> bool:
        """
        テキスト内容を設定

        Args:
            segment_index: セグメントインデックス
            text: 設定するテキスト

        Returns:
            bool: 設定成功時True
        """
        return self._set_segment_text(segment_index, text)

    def set_text_position(self, segment_index: int, x: float, y: float) -> bool:
        """
        テキストの位置を設定

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

    def set_font_style(self, segment_index: int, font_family: str = None,
                      font_size: float = None, font_weight: str = None,
                      font_style: str = None) -> bool:
        """
        フォントスタイルを設定

        Args:
            segment_index: セグメントインデックス
            font_family: フォントファミリー
            font_size: フォントサイズ
            font_weight: フォントウェイト (normal, bold, light)
            font_style: フォントスタイル (normal, italic)

        Returns:
            bool: 設定成功時True
        """
        current_style = self._get_segment_style(segment_index)

        # フォント設定更新
        if 'font' not in current_style:
            current_style['font'] = {}

        if font_family is not None:
            current_style['font']['family'] = font_family
        if font_size is not None:
            current_style['font']['size'] = font_size
        if font_weight is not None:
            current_style['font']['weight'] = font_weight
        if font_style is not None:
            current_style['font']['style'] = font_style

        return self._set_segment_style(segment_index, current_style)

    def set_text_color(self, segment_index: int, text_color: List[float] = None,
                      stroke_color: List[float] = None, background_color: List[float] = None) -> bool:
        """
        テキスト色を設定

        Args:
            segment_index: セグメントインデックス
            text_color: テキスト色 [R, G, B, A] (0.0-1.0)
            stroke_color: 縁取り色 [R, G, B, A] (0.0-1.0)
            background_color: 背景色 [R, G, B, A] (0.0-1.0)

        Returns:
            bool: 設定成功時True
        """
        current_style = self._get_segment_style(segment_index)

        # 色設定更新
        if 'color' not in current_style:
            current_style['color'] = {}

        if text_color is not None:
            current_style['color']['text'] = text_color
        if stroke_color is not None:
            current_style['color']['stroke'] = stroke_color
        if background_color is not None:
            current_style['color']['background'] = background_color

        return self._set_segment_style(segment_index, current_style)

    def set_text_alignment(self, segment_index: int, alignment: str) -> bool:
        """
        テキストの配置を設定

        Args:
            segment_index: セグメントインデックス
            alignment: 配置 (left, center, right)

        Returns:
            bool: 設定成功時True
        """
        current_style = self._get_segment_style(segment_index)

        if 'layout' not in current_style:
            current_style['layout'] = {}

        current_style['layout']['alignment'] = alignment
        return self._set_segment_style(segment_index, current_style)

    def apply_style_preset(self, preset_name: str, segment_indices: List[int] = None) -> bool:
        """
        スタイルプリセットを適用

        Args:
            preset_name: プリセット名
            segment_indices: 適用するセグメントのインデックス（None時は全セグメント）

        Returns:
            bool: 適用成功時True
        """
        presets = self._get_style_presets()

        if preset_name not in presets:
            self.logger.warning(f"Unknown preset: {preset_name}")
            return False

        preset_style = presets[preset_name]

        if segment_indices is None:
            segment_indices = list(range(len(self.segments)))

        success_count = 0
        for segment_index in segment_indices:
            if segment_index < len(self.segments):
                if self._set_segment_style(segment_index, preset_style):
                    success_count += 1

        self.logger.info(f"Applied preset '{preset_name}' to {success_count} segments")
        return success_count > 0

    def _get_style_presets(self) -> Dict[str, Dict]:
        """スタイルプリセット定義"""
        return {
            "default": {
                "font": {"family": "Arial", "size": 24.0, "weight": "normal", "style": "normal"},
                "color": {"text": [1.0, 1.0, 1.0, 1.0], "stroke": [0.0, 0.0, 0.0, 1.0], "background": [0.0, 0.0, 0.0, 0.0]},
                "effects": {"stroke_width": 2.0},
                "layout": {"alignment": "center"}
            },
            "title_large": {
                "font": {"family": "Arial", "size": 36.0, "weight": "bold", "style": "normal"},
                "color": {"text": [1.0, 1.0, 1.0, 1.0], "stroke": [0.0, 0.0, 0.0, 1.0], "background": [0.0, 0.0, 0.0, 0.3]},
                "effects": {"stroke_width": 3.0},
                "layout": {"alignment": "center"}
            },
            "subtitle_medium": {
                "font": {"family": "Arial", "size": 20.0, "weight": "normal", "style": "normal"},
                "color": {"text": [0.9, 0.9, 0.9, 1.0], "stroke": [0.0, 0.0, 0.0, 0.8], "background": [0.0, 0.0, 0.0, 0.0]},
                "effects": {"stroke_width": 1.5},
                "layout": {"alignment": "center"}
            },
            "elegant_gold": {
                "font": {"family": "Times New Roman", "size": 28.0, "weight": "bold", "style": "normal"},
                "color": {"text": [1.0, 0.84, 0.0, 1.0], "stroke": [0.4, 0.2, 0.0, 1.0], "background": [0.0, 0.0, 0.0, 0.4]},
                "effects": {"stroke_width": 2.5, "shadow": {"enabled": True, "offset": [2.0, 2.0], "color": [0.0, 0.0, 0.0, 0.7], "blur": 4.0}},
                "layout": {"alignment": "center"}
            }
        }

    def update_all_subtitles_from_srt(self, srt_content: str) -> bool:
        """
        SRTファイルの内容から全字幕を更新

        Args:
            srt_content: SRTファイルの内容

        Returns:
            bool: 更新成功時True
        """
        try:
            # SRT解析（簡易版）
            srt_entries = self._parse_srt_content(srt_content)

            success_count = 0
            for i, entry in enumerate(srt_entries):
                if i < len(self.segments):
                    # テキスト内容更新
                    if self._set_segment_text(i, entry['text']):
                        # タイミング更新
                        start_sec = entry['start_seconds']
                        end_sec = entry['end_seconds']
                        duration_sec = end_sec - start_sec

                        if self.set_segment_timing(i, start_sec, duration_sec):
                            success_count += 1

            self.logger.info(f"Updated {success_count} subtitles from SRT")
            return success_count > 0

        except Exception as e:
            self.logger.error(f"Failed to update from SRT: {e}")
            return False

    def _parse_srt_content(self, srt_content: str) -> List[Dict]:
        """SRT内容を解析（簡易版）"""
        entries = []
        lines = srt_content.strip().split('\n')

        i = 0
        while i < len(lines):
            # セグメント番号をスキップ
            if lines[i].strip().isdigit():
                i += 1
                continue

            # タイミング行を解析
            if ' --> ' in lines[i]:
                timing_line = lines[i].strip()
                start_str, end_str = timing_line.split(' --> ')

                start_seconds = self._time_str_to_seconds(start_str)
                end_seconds = self._time_str_to_seconds(end_str)

                # テキスト行を収集
                text_lines = []
                i += 1
                while i < len(lines) and lines[i].strip() != '':
                    text_lines.append(lines[i].strip())
                    i += 1

                entries.append({
                    'start_seconds': start_seconds,
                    'end_seconds': end_seconds,
                    'text': '\n'.join(text_lines)
                })

            i += 1

        return entries

    def _time_str_to_seconds(self, time_str: str) -> float:
        """時間文字列を秒に変換"""
        try:
            # "00:00:20,123" -> 20.123秒
            time_part, ms_part = time_str.split(',')
            h, m, s = map(int, time_part.split(':'))
            ms = int(ms_part)

            return h * 3600 + m * 60 + s + ms / 1000.0
        except:
            return 0.0

    def get_text_analysis(self) -> Dict[str, Any]:
        """
        テキストレイヤーの分析結果を取得

        Returns:
            Dict: 分析結果
        """
        total_duration = self._calculate_total_duration()
        avg_duration = total_duration / len(self.segments) if self.segments else 0

        # テキスト統計
        text_lengths = []
        unique_fonts = set()
        for i in range(len(self.segments)):
            text = self._get_segment_text(i)
            text_lengths.append(len(text))

            style = self._get_segment_style(i)
            font_family = style.get('font', {}).get('family', 'Unknown')
            unique_fonts.add(font_family)

        return {
            'layer_role': self.layer_role,
            'total_segments': len(self.segments),
            'text_segments': len(self.text_segments),
            'total_duration': total_duration,
            'average_duration': avg_duration,
            'text_statistics': {
                'total_characters': sum(text_lengths),
                'average_characters': sum(text_lengths) / len(text_lengths) if text_lengths else 0,
                'max_characters': max(text_lengths) if text_lengths else 0,
                'min_characters': min(text_lengths) if text_lengths else 0,
                'unique_fonts': list(unique_fonts)
            }
        }


# テスト用関数
def test_text_layer_controller():
    """TextLayerControllerのテスト関数"""
    print("📝 TextLayerController ready for use!")
    print("✅ Font management, positioning, coloring, and animation control available")

if __name__ == "__main__":
    test_text_layer_controller()