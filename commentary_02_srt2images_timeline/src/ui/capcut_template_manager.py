#!/usr/bin/env python3
"""
CapCut Template Manager
CapCutプロジェクトから動的にテンプレート情報を取得・管理するシステム
"""
import json
import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

@dataclass
class CapCutTemplate:
    """CapCutテンプレート情報"""
    name: str
    description: str
    category: str
    target_audience: str
    tx: float = 0.0
    ty: float = 0.0
    scale: float = 1.0
    path: Optional[str] = None
    last_modified: Optional[float] = None
    layer_count: int = 0
    duration: float = 0.0

class CapCutTemplateManager:
    """CapCutテンプレート管理システム"""
    
    def __init__(self, capcut_projects_dir: str = None):
        """
        Args:
            capcut_projects_dir: CapCutプロジェクトディレクトリのパス
        """
        if capcut_projects_dir:
            self.capcut_projects_dir = Path(capcut_projects_dir).expanduser()
        else:
            env_root = os.getenv("CAPCUT_DRAFT_ROOT")
            self.capcut_projects_dir = (
                Path(env_root).expanduser()
                if env_root
                else Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"
            )
        self.templates_cache = {}
        self.predefined_templates = self._get_predefined_templates()
    
    def _get_predefined_templates(self) -> Dict[str, CapCutTemplate]:
        """プリセットテンプレート定義"""
        return {
            "シニア恋愛テンプレ": CapCutTemplate(
                name="シニア恋愛テンプレ",
                description="日本人向け恋愛コンテンツに最適化",
                category="恋愛・ライフスタイル",
                target_audience="シニア層",
                tx=-0.3125,
                ty=0.205,
                scale=0.59
            ),
            "000_シニアの朗読テンプレ": CapCutTemplate(
                name="000_シニアの朗読テンプレ",
                description="朗読・教育コンテンツ向け",
                category="教育・朗読",
                target_audience="シニア層",
                tx=-0.163,
                ty=0.201,
                scale=0.59
            ),
            "人生の道標_最新テンプレ": CapCutTemplate(
                name="人生の道標_最新テンプレ",
                description="人生観・哲学コンテンツ向け",
                category="哲学・スピリチュアル",
                target_audience="全年代",
                tx=0.0,
                ty=0.0,
                scale=0.99
            ),
            "5-コピー": CapCutTemplate(
                name="5-コピー",
                description="汎用テンプレート",
                category="汎用",
                target_audience="全年代",
                tx=-0.2,
                ty=0.15,
                scale=0.65
            )
        }
    
    def scan_capcut_projects(self) -> List[CapCutTemplate]:
        """CapCutプロジェクトディレクトリをスキャンしてテンプレートを検出"""
        templates = []
        
        if not self.capcut_projects_dir.exists():
            logger.warning(f"CapCutプロジェクトディレクトリが見つかりません: {self.capcut_projects_dir}")
            return list(self.predefined_templates.values())
        
        try:
            for project_dir in self.capcut_projects_dir.iterdir():
                if project_dir.is_dir():
                    template = self._analyze_project_directory(project_dir)
                    if template:
                        templates.append(template)
        except Exception as e:
            logger.error(f"プロジェクトスキャンエラー: {e}")
        
        # プリセットテンプレートと合わせる
        templates.extend(self.predefined_templates.values())
        
        # 重複除去（名前ベース）
        unique_templates = {}
        for template in templates:
            unique_templates[template.name] = template
        
        return list(unique_templates.values())
    
    def _analyze_project_directory(self, project_dir: Path) -> Optional[CapCutTemplate]:
        """個別プロジェクトディレクトリを解析"""
        try:
            # draft_info.jsonの存在確認
            draft_info_path = project_dir / "draft_info.json"
            if not draft_info_path.exists():
                return None
            
            # ドラフト情報読み込み
            with open(draft_info_path, 'r', encoding='utf-8') as f:
                draft_data = json.load(f)
            
            # 基本情報抽出
            name = project_dir.name
            duration = draft_data.get('duration', 0) / 1000000  # マイクロ秒から秒に変換
            tracks = draft_data.get('tracks', [])
            layer_count = len(tracks)
            
            # 画像レイヤーの座標情報抽出
            tx, ty, scale = self._extract_image_position(tracks)
            
            # カテゴリー推定
            category = self._estimate_category(name)
            
            # 対象ユーザー推定
            target_audience = self._estimate_target_audience(name)
            
            return CapCutTemplate(
                name=name,
                description=f"CapCutプロジェクト: {name}",
                category=category,
                target_audience=target_audience,
                tx=tx,
                ty=ty,
                scale=scale,
                path=str(project_dir),
                last_modified=draft_info_path.stat().st_mtime,
                layer_count=layer_count,
                duration=duration
            )
            
        except Exception as e:
            logger.warning(f"プロジェクト解析エラー {project_dir.name}: {e}")
            return None
    
    def _extract_image_position(self, tracks: List[Dict]) -> tuple[float, float, float]:
        """トラックから画像レイヤーの位置情報を抽出"""
        for track in tracks:
            if track.get('type') == 'video':
                segments = track.get('segments', [])
                for segment in segments:
                    clip = segment.get('clip', {})
                    transform = clip.get('transform', {})
                    scale_info = clip.get('scale', {})
                    
                    # 画像らしいセグメントの場合（アルファが1.0未満、または特定の座標）
                    if transform or scale_info:
                        tx = transform.get('x', 0.0)
                        ty = transform.get('y', 0.0)
                        scale = scale_info.get('x', 1.0)
                        
                        # デフォルト位置でない場合は有効な画像位置として採用
                        if abs(tx) > 0.1 or abs(ty) > 0.1 or abs(scale - 1.0) > 0.1:
                            return tx, ty, scale
        
        # デフォルト値
        return 0.0, 0.0, 1.0
    
    def _estimate_category(self, name: str) -> str:
        """プロジェクト名からカテゴリーを推定"""
        name_lower = name.lower()
        
        if any(keyword in name_lower for keyword in ['恋愛', '恋', 'love', 'シニア']):
            return "恋愛・ライフスタイル"
        elif any(keyword in name_lower for keyword in ['朗読', '教育', '学習', 'education']):
            return "教育・朗読"
        elif any(keyword in name_lower for keyword in ['人生', '道標', '哲学', 'philosophy', 'スピリチュアル']):
            return "哲学・スピリチュアル"
        elif any(keyword in name_lower for keyword in ['ファンタジー', 'fantasy', '魔法', 'magic']):
            return "ファンタジー"
        else:
            return "汎用"
    
    def _estimate_target_audience(self, name: str) -> str:
        """プロジェクト名から対象ユーザーを推定"""
        name_lower = name.lower()
        
        if any(keyword in name_lower for keyword in ['シニア', 'senior', '高齢']):
            return "シニア層"
        elif any(keyword in name_lower for keyword in ['若者', 'youth', '学生']):
            return "若年層"
        else:
            return "全年代"
    
    def get_template_by_name(self, name: str) -> Optional[CapCutTemplate]:
        """テンプレート名で検索"""
        templates = self.get_all_templates()
        
        # 完全一致
        for template in templates:
            if template.name == name:
                return template
        
        # 前方一致（「シニア恋愛テンプレ(1)」のような場合）
        for template in templates:
            if name.startswith(template.name):
                return template
        
        return None
    
    def get_all_templates(self) -> List[CapCutTemplate]:
        """全テンプレートを取得（キャッシュ付き）"""
        return self.scan_capcut_projects()
    
    def get_templates_by_category(self, category: str) -> List[CapCutTemplate]:
        """カテゴリー別テンプレート取得"""
        all_templates = self.get_all_templates()
        return [t for t in all_templates if t.category == category]
    
    def get_categories(self) -> List[str]:
        """利用可能なカテゴリー一覧"""
        all_templates = self.get_all_templates()
        categories = list(set(t.category for t in all_templates))
        return sorted(categories)
    
    def get_template_templates(self) -> List[CapCutTemplate]:
        """「テンプレ」を含む名前のテンプレートのみを取得"""
        all_templates = self.get_all_templates()
        template_templates = []
        
        for template in all_templates:
            if "テンプレ" in template.name:
                template_templates.append(template)
        
        # プリセットテンプレートも「テンプレ」を含む場合は追加
        for preset_name, preset_template in self.predefined_templates.items():
            if "テンプレ" in preset_name:
                # 重複チェック
                if not any(t.name == preset_name for t in template_templates):
                    template_templates.append(preset_template)
        
        return sorted(template_templates, key=lambda x: x.name)
    
    def refresh_cache(self):
        """キャッシュをリフレッシュ"""
        self.templates_cache.clear()

def test_template_manager():
    """テスト関数"""
    manager = CapCutTemplateManager()
    
    print("=== CapCut Template Manager Test ===")
    
    # 全テンプレート取得
    templates = manager.get_all_templates()
    print(f"検出されたテンプレート数: {len(templates)}")
    
    for template in templates[:5]:  # 最初の5個のみ表示
        print(f"名前: {template.name}")
        print(f"カテゴリー: {template.category}")
        print(f"座標: ({template.tx}, {template.ty}), スケール: {template.scale}")
        print(f"レイヤー数: {template.layer_count}, 時間: {template.duration:.1f}秒")
        print("---")
    
    # カテゴリー一覧
    categories = manager.get_categories()
    print(f"利用可能なカテゴリー: {categories}")
    
    # 特定テンプレート検索
    target_template = manager.get_template_by_name("シニア恋愛テンプレ")
    if target_template:
        print(f"検索結果: {target_template.name} - {target_template.description}")
    else:
        print("テンプレートが見つかりませんでした")

if __name__ == "__main__":
    test_template_manager()
