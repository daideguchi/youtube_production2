#!/usr/bin/env python3
"""
Image Template Manager
画像生成テンプレートの管理・作成・編集システム
"""
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, asdict
import logging
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(PROJECT_ROOT / "src"))
from config.template_registry import get_active_templates, resolve_template_path  # noqa: E402

logger = logging.getLogger(__name__)

@dataclass
class ImageTemplate:
    """画像デザインテンプレート情報"""
    name: str
    description: str
    category: str
    style: str
    file: str
    preview_keywords: List[str]
    created_at: Optional[str] = None
    modified_at: Optional[str] = None
    author: str = "user"
    version: str = "1.0"
    tags: List[str] = None
    
    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()
        if self.modified_at is None:
            self.modified_at = self.created_at

class ImageTemplateManager:
    """画像テンプレート管理システム"""
    
    def __init__(self, templates_dir: str = None, config_file: str = None):
        """
        Args:
            templates_dir: テンプレートファイルディレクトリ
            config_file: テンプレート設定ファイル
        """
        self.base_dir = PROJECT_ROOT
        self.templates_dir = Path(templates_dir or self.base_dir / "templates")
        self.config_file = Path(config_file or self.base_dir / "ui" / "image_templates_config.json")
        
        # ディレクトリ作成
        self.templates_dir.mkdir(exist_ok=True)
        self.config_file.parent.mkdir(exist_ok=True)
        
        self.predefined_templates = self._get_predefined_templates()
        self.custom_templates = self._load_custom_templates()
    
    def _get_predefined_templates(self) -> Dict[str, ImageTemplate]:
        """プリセットテンプレート定義"""
        presets: Dict[str, ImageTemplate] = {}
        for entry in get_active_templates():
            # scopeにmanual/legacy含むものだけをUIのプリセットとして扱う
            if any(tag in entry.scope for tag in ("manual", "legacy")) or len(entry.scope) == 0:
                presets_key = Path(entry.id).stem
                presets[presets_key] = ImageTemplate(
                    name=entry.label,
                    description=entry.label,
                    category="プリセット",
                    style="",
                    file=str(Path("templates") / entry.id),
                    preview_keywords=[],
                    author="system",
                    version="1.0",
                )
        return presets
    
    def _load_custom_templates(self) -> Dict[str, ImageTemplate]:
        """カスタムテンプレート設定を読み込み"""
        if not self.config_file.exists():
            return {}
        
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            templates = {}
            for key, template_data in data.items():
                templates[key] = ImageTemplate(**template_data)
            
            return templates
            
        except Exception as e:
            logger.error(f"カスタムテンプレート読み込みエラー: {e}")
            return {}
    
    def _save_custom_templates(self):
        """カスタムテンプレート設定を保存"""
        try:
            # dataclassをdictに変換
            data = {}
            for key, template in self.custom_templates.items():
                data[key] = asdict(template)
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            logger.error(f"カスタムテンプレート保存エラー: {e}")
            raise
    
    def get_all_templates(self) -> Dict[str, ImageTemplate]:
        """全テンプレートを取得"""
        all_templates = {}
        all_templates.update(self.predefined_templates)
        all_templates.update(self.custom_templates)
        return all_templates
    
    def get_template_by_name(self, name: str) -> Optional[ImageTemplate]:
        """名前でテンプレートを検索"""
        all_templates = self.get_all_templates()
        
        # キーで直接検索
        if name in all_templates:
            return all_templates[name]
        
        # テンプレート名で検索
        for template in all_templates.values():
            if template.name == name:
                return template
        
        return None
    
    def get_templates_by_category(self, category: str) -> List[ImageTemplate]:
        """カテゴリー別テンプレート取得"""
        all_templates = self.get_all_templates()
        return [t for t in all_templates.values() if t.category == category]
    
    def get_categories(self) -> List[str]:
        """利用可能なカテゴリー一覧"""
        all_templates = self.get_all_templates()
        categories = list(set(t.category for t in all_templates.values()))
        return sorted(categories)
    
    def create_template(self, name: str, description: str, category: str, 
                       style: str, prompt_content: str, preview_keywords: List[str],
                       tags: List[str] = None) -> str:
        """新規テンプレート作成"""
        try:
            # テンプレートキー生成
            template_key = self._generate_template_key(name)
            
            # テンプレートファイル作成
            template_filename = f"{template_key}.txt"
            template_path = self.templates_dir / template_filename
            
            with open(template_path, 'w', encoding='utf-8') as f:
                f.write(prompt_content)
            
            # テンプレート情報作成
            template = ImageTemplate(
                name=name,
                description=description,
                category=category,
                style=style,
                file=f"templates/{template_filename}",
                preview_keywords=preview_keywords,
                tags=tags or [],
                created_at=datetime.now().isoformat(),
                modified_at=datetime.now().isoformat(),
                author="user"
            )
            
            # カスタムテンプレートに追加
            self.custom_templates[template_key] = template
            self._save_custom_templates()
            
            logger.info(f"テンプレート作成完了: {name}")
            return template_key
            
        except Exception as e:
            logger.error(f"テンプレート作成エラー: {e}")
            raise
    
    def update_template(self, template_key: str, **kwargs) -> bool:
        """既存テンプレートの更新"""
        try:
            if template_key not in self.custom_templates:
                raise ValueError(f"テンプレートが見つかりません: {template_key}")
            
            template = self.custom_templates[template_key]
            
            # 更新可能フィールド
            updatable_fields = ['name', 'description', 'category', 'style', 'preview_keywords', 'tags']
            
            for field, value in kwargs.items():
                if field in updatable_fields:
                    setattr(template, field, value)
            
            # プロンプト内容更新
            if 'prompt_content' in kwargs:
                template_path = self.base_dir / template.file
                with open(template_path, 'w', encoding='utf-8') as f:
                    f.write(kwargs['prompt_content'])
            
            # 更新日時を設定
            template.modified_at = datetime.now().isoformat()
            
            # 保存
            self._save_custom_templates()
            
            logger.info(f"テンプレート更新完了: {template.name}")
            return True
            
        except Exception as e:
            logger.error(f"テンプレート更新エラー: {e}")
            return False
    
    def delete_template(self, template_key: str) -> bool:
        """テンプレート削除"""
        try:
            if template_key not in self.custom_templates:
                raise ValueError(f"テンプレートが見つかりません: {template_key}")
            
            template = self.custom_templates[template_key]
            
            # ファイル削除
            template_path = self.base_dir / template.file
            if template_path.exists():
                template_path.unlink()
            
            # テンプレート情報削除
            del self.custom_templates[template_key]
            self._save_custom_templates()
            
            logger.info(f"テンプレート削除完了: {template.name}")
            return True
            
        except Exception as e:
            logger.error(f"テンプレート削除エラー: {e}")
            return False
    
    def get_template_content(self, template: Union[ImageTemplate, str]) -> str:
        """テンプレートファイルの内容を取得"""
        try:
            if isinstance(template, str):
                template = self.get_template_by_name(template)
                if not template:
                    raise ValueError(f"テンプレートが見つかりません: {template}")
            
            template_path = self.base_dir / template.file
            
            if not template_path.exists():
                raise FileNotFoundError(f"テンプレートファイルが見つかりません: {template_path}")
            
            with open(template_path, 'r', encoding='utf-8') as f:
                return f.read()
                
        except Exception as e:
            logger.error(f"テンプレート内容取得エラー: {e}")
            return ""
    
    def _generate_template_key(self, name: str) -> str:
        """テンプレート名からキーを生成"""
        # 英数字と一部記号のみに変換
        key = name.lower().replace(' ', '_').replace('　', '_')
        key = ''.join(c for c in key if c.isalnum() or c in ['_', '-'])
        
        # 重複チェック
        original_key = key
        counter = 1
        while key in self.get_all_templates():
            key = f"{original_key}_{counter}"
            counter += 1
        
        return key
    
    def search_templates(self, query: str) -> List[ImageTemplate]:
        """テンプレート検索"""
        query_lower = query.lower()
        results = []
        
        for template in self.get_all_templates().values():
            # 名前、説明、スタイル、キーワードで検索
            searchable_text = f"{template.name} {template.description} {template.style} {' '.join(template.preview_keywords)}".lower()
            
            if query_lower in searchable_text:
                results.append(template)
        
        return results
    
    def export_template(self, template_key: str, export_path: str) -> bool:
        """テンプレートをエクスポート"""
        try:
            template = self.get_template_by_name(template_key)
            if not template:
                raise ValueError(f"テンプレートが見つかりません: {template_key}")
            
            # テンプレート情報とファイル内容を含む
            content = self.get_template_content(template)
            
            export_data = {
                'template_info': asdict(template),
                'prompt_content': content
            }
            
            with open(export_path, 'w', encoding='utf-8') as f:
                json.dump(export_data, f, indent=2, ensure_ascii=False)
            
            logger.info(f"テンプレートエクスポート完了: {export_path}")
            return True
            
        except Exception as e:
            logger.error(f"テンプレートエクスポートエラー: {e}")
            return False
    
    def import_template(self, import_path: str) -> str:
        """テンプレートをインポート"""
        try:
            with open(import_path, 'r', encoding='utf-8') as f:
                import_data = json.load(f)
            
            template_info = import_data['template_info']
            prompt_content = import_data['prompt_content']
            
            # 新規テンプレートとして作成
            return self.create_template(
                name=template_info['name'],
                description=template_info['description'],
                category=template_info['category'],
                style=template_info['style'],
                prompt_content=prompt_content,
                preview_keywords=template_info['preview_keywords'],
                tags=template_info.get('tags', [])
            )
            
        except Exception as e:
            logger.error(f"テンプレートインポートエラー: {e}")
            raise

def test_image_template_manager():
    """テスト関数"""
    manager = ImageTemplateManager()
    
    print("=== Image Template Manager Test ===")
    
    # 全テンプレート取得
    templates = manager.get_all_templates()
    print(f"検出されたテンプレート数: {len(templates)}")
    
    for key, template in list(templates.items())[:5]:  # 最初の5個のみ表示
        print(f"キー: {key}")
        print(f"名前: {template.name}")
        print(f"カテゴリー: {template.category}")
        print(f"スタイル: {template.style[:50]}...")
        print(f"キーワード: {', '.join(template.preview_keywords)}")
        print("---")
    
    # カテゴリー一覧
    categories = manager.get_categories()
    print(f"利用可能なカテゴリー: {categories}")
    
    # 新規テンプレート作成テスト
    try:
        template_key = manager.create_template(
            name="テストテンプレート",
            description="テスト用のテンプレート",
            category="テスト",
            style="test style, simple",
            prompt_content="This is a test prompt template.",
            preview_keywords=["テスト", "シンプル"]
        )
        print(f"テストテンプレート作成成功: {template_key}")
        
        # 削除
        manager.delete_template(template_key)
        print("テストテンプレート削除成功")
        
    except Exception as e:
        print(f"テストエラー: {e}")

if __name__ == "__main__":
    test_image_template_manager()
