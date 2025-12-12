#!/usr/bin/env python3
"""
CapCut Draft Analyzer
=====================
「シニアの口コミ１_2」ドラフトファイルの完全構造解析

使用方法:
python tools/analysis/capcut_draft_analyzer.py --draft-info "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/<draft-name>/draft_info.json"

機能:
1. 全レイヤー構造とパラメータ抽出
2. 各セグメントの設定値解析（position, scale, rotation, opacity等）
3. テキストスタイル設定詳細
4. エフェクトパラメータ
5. オーディオ設定
6. 座標系とアンカー設定分析
"""

import json
import argparse
import pathlib
from pprint import pprint
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from collections import defaultdict

@dataclass
class Transform2D:
    """2D変換パラメータ（位置・スケール・回転）"""
    x: float = 0.0
    y: float = 0.0
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0
    alpha: float = 1.0

    @classmethod
    def from_clip_data(cls, clip_data: Dict) -> 'Transform2D':
        """CapCutのclipデータから変換パラメータを抽出"""
        if not clip_data:
            return cls()

        transform = clip_data.get('transform', {})
        scale = clip_data.get('scale', {})

        return cls(
            x=transform.get('x', 0.0),
            y=transform.get('y', 0.0),
            scale_x=scale.get('x', 1.0),
            scale_y=scale.get('y', 1.0),
            rotation=clip_data.get('rotation', 0.0),
            alpha=clip_data.get('alpha', 1.0)
        )

@dataclass
class TextStyleInfo:
    """テキストスタイル設定"""
    font_family: str = ""
    font_size: float = 0.0
    font_color: str = ""
    alignment: int = 0
    bold_width: float = 0.0
    italic: bool = False
    underline: bool = False
    background_color: str = ""
    background_alpha: float = 1.0
    border_color: str = ""
    border_width: float = 0.0

    @classmethod
    def from_text_material(cls, text_data: Dict) -> 'TextStyleInfo':
        """テキストマテリアルデータからスタイル情報を抽出"""
        return cls(
            font_family=text_data.get('font_family', ''),
            font_size=text_data.get('font_size', 0.0),
            font_color=text_data.get('font_color', ''),
            alignment=text_data.get('alignment', 0),
            bold_width=text_data.get('bold_width', 0.0),
            italic=text_data.get('italic', False),
            underline=text_data.get('underline', False),
            background_color=text_data.get('background_color', ''),
            background_alpha=text_data.get('background_alpha', 1.0),
            border_color=text_data.get('border_color', ''),
            border_width=text_data.get('border_width', 0.0)
        )

class CapCutDraftAnalyzer:
    """CapCutドラフトファイルの解析クラス"""

    def __init__(self, draft_path: str):
        self.draft_path = pathlib.Path(draft_path)
        self.data: Dict = {}
        self.material_lookup: Dict[str, List] = {}

    def load_draft(self) -> bool:
        """ドラフトファイルを読み込み"""
        try:
            with self.draft_path.open('r', encoding='utf-8') as f:
                self.data = json.load(f)
            self._build_material_lookup()
            return True
        except Exception as e:
            print(f"❌ ドラフト読み込みエラー: {e}")
            return False

    def _build_material_lookup(self):
        """マテリアルIDから情報を検索するための辞書を構築"""
        self.material_lookup = {}
        materials = self.data.get('materials', {})

        for category, items in materials.items():
            for item in items:
                item_id = item.get('id')
                if item_id:
                    if item_id not in self.material_lookup:
                        self.material_lookup[item_id] = []
                    self.material_lookup[item_id].append((category, item))

    def get_basic_info(self) -> Dict:
        """基本プロジェクト情報を取得"""
        return {
            'name': self.data.get('name', ''),
            'width': self.data.get('canvas_config', {}).get('width', 1920),
            'height': self.data.get('canvas_config', {}).get('height', 1080),
            'fps': self.data.get('fps', 30.0),
            'duration_seconds': self.data.get('duration', 0) / 1000000,
            'track_count': len(self.data.get('tracks', []))
        }

    def analyze_tracks(self) -> List[Dict]:
        """全トラック（レイヤー）を解析"""
        tracks_info = []

        for i, track in enumerate(self.data.get('tracks', [])):
            track_info = {
                'index': i,
                'type': track.get('type'),
                'name': track.get('name', ''),
                'segment_count': len(track.get('segments', [])),
                'segments': []
            }

            # 各セグメントを解析
            for j, segment in enumerate(track.get('segments', [])):
                segment_info = self._analyze_segment(segment, j)
                track_info['segments'].append(segment_info)

            tracks_info.append(track_info)

        return tracks_info

    def _analyze_segment(self, segment: Dict, index: int) -> Dict:
        """個別セグメントを詳細解析"""
        segment_info = {
            'index': index,
            'id': segment.get('id', ''),
            'material_id': segment.get('material_id', ''),
            'start_time': segment.get('target_timerange', {}).get('start', 0) / 1000000,
            'duration': segment.get('target_timerange', {}).get('duration', 0) / 1000000,
            'visible': segment.get('visible', True),
            'volume': segment.get('volume', 1.0),
            'transform': Transform2D.from_clip_data(segment.get('clip', {})),
            'material_refs': segment.get('extra_material_refs', []),
            'material_details': []
        }

        # マテリアル詳細情報を取得
        for ref in segment_info['material_refs']:
            if ref in self.material_lookup:
                segment_info['material_details'].extend(self.material_lookup[ref])

        return segment_info

    def analyze_text_materials(self) -> List[Dict]:
        """テキストマテリアルの詳細解析"""
        text_materials = []

        for text_data in self.data.get('materials', {}).get('texts', []):
            text_info = {
                'id': text_data.get('id'),
                'content': text_data.get('words', ''),
                'style': TextStyleInfo.from_text_material(text_data),
                'template_info': text_data.get('caption_template_info', {}),
                'raw_data': text_data  # 完全なデータ
            }
            text_materials.append(text_info)

        return text_materials

    def analyze_audio_materials(self) -> List[Dict]:
        """オーディオマテリアルの解析"""
        audio_materials = []

        for audio_data in self.data.get('materials', {}).get('audios', []):
            audio_info = {
                'id': audio_data.get('id'),
                'duration': audio_data.get('duration', 0) / 1000000,
                'path': audio_data.get('path', ''),
                'name': audio_data.get('name', ''),
                'category': audio_data.get('category_name', ''),
                'volume': audio_data.get('volume', 1.0),
                'raw_data': audio_data
            }
            audio_materials.append(audio_info)

        return audio_materials

    def get_coordinate_system_info(self) -> Dict:
        """座標系の詳細情報を取得"""
        canvas = self.data.get('canvas_config', {})

        # サンプルセグメントから座標範囲を分析
        coordinate_samples = []
        for track in self.data.get('tracks', []):
            for segment in track.get('segments', []):
                clip = segment.get('clip', {})
                if clip:
                    transform = clip.get('transform', {})
                    coordinate_samples.append({
                        'x': transform.get('x', 0),
                        'y': transform.get('y', 0),
                        'track_type': track.get('type')
                    })

        return {
            'canvas_size': {
                'width': canvas.get('width', 1920),
                'height': canvas.get('height', 1080),
                'ratio': canvas.get('ratio', 'original')
            },
            'coordinate_system': {
                'description': 'CapCutは正規化座標系を使用（-1.0〜1.0）',
                'x_range': '左: -1.0, 中央: 0.0, 右: 1.0',
                'y_range': '上: -1.0, 中央: 0.0, 下: 1.0',
                'anchor_point': 'デフォルトは中央(0, 0)'
            },
            'sample_coordinates': coordinate_samples[:10]  # 最初の10個のサンプル
        }

    def generate_modification_guide(self, tracks_info: List[Dict]) -> Dict:
        """「ここをいじれば良い」ガイドを生成"""
        guide = {
            'position_control': {
                'description': '画像・テキストの位置調整',
                'location': 'tracks[i].segments[j].clip.transform.x/y',
                'values': '正規化座標（-1.0〜1.0）',
                'examples': {
                    '左上': {'x': -0.8, 'y': -0.8},
                    '中央': {'x': 0.0, 'y': 0.0},
                    '右下': {'x': 0.8, 'y': 0.8}
                }
            },
            'scale_control': {
                'description': 'サイズ調整',
                'location': 'tracks[i].segments[j].clip.scale.x/y',
                'values': '1.0が100%、2.0で200%',
                'examples': {
                    '縮小': {'x': 0.5, 'y': 0.5},
                    '標準': {'x': 1.0, 'y': 1.0},
                    '拡大': {'x': 1.5, 'y': 1.5}
                }
            },
            'rotation_control': {
                'description': '回転角度',
                'location': 'tracks[i].segments[j].clip.rotation',
                'values': '度数法（360度）',
                'examples': {
                    '時計回り90度': 90.0,
                    '反時計回り45度': -45.0
                }
            },
            'opacity_control': {
                'description': '透明度調整',
                'location': 'tracks[i].segments[j].clip.alpha',
                'values': '0.0（完全透明）〜1.0（不透明）',
                'examples': {
                    '半透明': 0.5,
                    '薄い透明': 0.8
                }
            },
            'timing_control': {
                'description': 'タイミング調整',
                'location': 'tracks[i].segments[j].target_timerange',
                'values': 'マイクロ秒単位（1秒=1000000）',
                'examples': {
                    '開始時刻': 'start: 5000000 (5秒後)',
                    '持続時間': 'duration: 3000000 (3秒間)'
                }
            },
            'text_style_control': {
                'description': 'テキストスタイル',
                'location': 'materials.texts[i]',
                'key_properties': [
                    'font_family: フォント名',
                    'font_size: フォントサイズ',
                    'font_color: 色（#RRGGBB形式）',
                    'alignment: 0=左, 1=中央, 2=右',
                    'bold_width: 太字の強度',
                    'background_color: 背景色'
                ]
            },
            'layer_order': {
                'description': 'レイヤー順序（上から下）',
                'explanation': 'tracks配列のインデックスが小さいほど上層',
                'examples': [
                    'tracks[0]: 最上層（最前面）',
                    'tracks[1]: 上から2番目',
                    'tracks[12]: 最下層（最背面）'
                ]
            }
        }

        return guide

def main():
    """メイン実行関数"""
    ap = argparse.ArgumentParser(description="CapCut draft_info.json analyzer")
    ap.add_argument("--draft-info", required=True, help="Path to draft_info.json")
    args = ap.parse_args()

    print("🔍 CapCut Draft Analyzer - 徹底解析開始")
    print("=" * 60)

    # ドラフトファイルパス
    draft_path = args.draft_info

    # 解析器初期化
    analyzer = CapCutDraftAnalyzer(draft_path)

    if not analyzer.load_draft():
        return

    # 基本情報
    basic_info = analyzer.get_basic_info()
    print("📊 基本プロジェクト情報")
    print("-" * 30)
    for key, value in basic_info.items():
        print(f"{key}: {value}")

    print("\n🎛️ レイヤー構造解析")
    print("-" * 30)
    tracks_info = analyzer.analyze_tracks()

    # 各トラックの概要
    for track in tracks_info:
        print(f"Track {track['index']:2d}: [{track['type']:5s}] {track['segment_count']:3d}セグメント")

        # 最初のセグメントの詳細（サンプル）
        if track['segments']:
            seg = track['segments'][0]
            transform = seg['transform']
            print(f"  └ サンプル: pos({transform.x:.3f}, {transform.y:.3f}) "
                  f"scale({transform.scale_x:.3f}, {transform.scale_y:.3f}) "
                  f"rot({transform.rotation:.1f}°) alpha({transform.alpha:.2f})")

    # テキスト解析
    print("\n📝 テキストマテリアル解析")
    print("-" * 30)
    text_materials = analyzer.analyze_text_materials()
    for i, text in enumerate(text_materials[:5]):  # 最初の5個
        content = str(text.get('content', ''))
        content_preview = content[:30] if len(content) > 30 else content
        font_size = text['style'].font_size
        print(f"Text {i}: '{content_preview}' font_size:{font_size}")

    # オーディオ解析
    print("\n🔊 オーディオマテリアル解析")
    print("-" * 30)
    audio_materials = analyzer.analyze_audio_materials()
    for audio in audio_materials:
        print(f"Audio: {audio['name']} ({audio['duration']:.1f}秒)")

    # 座標系情報
    print("\n📐 座標系・アンカー情報")
    print("-" * 30)
    coord_info = analyzer.get_coordinate_system_info()
    print("Canvas:", coord_info['canvas_size'])
    print("座標系:", coord_info['coordinate_system']['description'])

    # 修正ガイド
    print("\n🛠️ 修正ガイド - 「ここをいじれば良い」")
    print("-" * 30)
    guide = analyzer.generate_modification_guide(tracks_info)

    for section_name, section_info in guide.items():
        print(f"\n【{section_info['description']}】")
        if 'location' in section_info:
            print(f"場所: {section_info['location']}")
        if 'values' in section_info:
            print(f"値: {section_info['values']}")
        if 'examples' in section_info:
            print(f"例: {section_info['examples']}")
        if 'key_properties' in section_info:
            for prop in section_info['key_properties']:
                print(f"  • {prop}")

    print("\n✅ 解析完了")
    print(f"📂 解析対象: {draft_path}")

if __name__ == "__main__":
    main()
