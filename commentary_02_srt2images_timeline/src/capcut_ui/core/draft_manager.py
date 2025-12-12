#!/usr/bin/env python3
"""
CapCut Draft Manager
CapCutドラフトの読み込み・保存・バックアップを安全に管理する中核モジュール
"""
import os
import json
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List
import logging

class DraftManager:
    """CapCutドラフト管理のメインクラス"""

    def __init__(self, draft_path: str):
        """
        ドラフトマネージャーを初期化

        Args:
            draft_path: CapCutドラフトディレクトリの絶対パス、またはdraft_info.jsonファイルのパス
        """
        draft_path_obj = Path(draft_path)

        # draft_pathがファイルパスの場合はディレクトリパスに変換
        if draft_path_obj.is_file() and draft_path_obj.name in ["draft_info.json", "capcut_draft_info.json"]:
            self.draft_path = draft_path_obj.parent
            self.draft_info_file = draft_path_obj
        else:
            self.draft_path = draft_path_obj
            # draft_info.json または capcut_draft_info.json を探す
            if (self.draft_path / "draft_info.json").exists():
                self.draft_info_file = self.draft_path / "draft_info.json"
            elif (self.draft_path / "capcut_draft_info.json").exists():
                self.draft_info_file = self.draft_path / "capcut_draft_info.json"
            else:
                self.draft_info_file = self.draft_path / "draft_info.json"

        self.backup_dir = self.draft_path / "backups"

        # バックアップディレクトリ作成
        self.backup_dir.mkdir(exist_ok=True)

        # ログ設定
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)

        # ドラフトデータ
        self.draft_data: Optional[Dict] = None
        self.is_loaded = False

    def load_draft(self) -> bool:
        """
        ドラフトデータを読み込み

        Returns:
            bool: 読み込み成功時True
        """
        try:
            if not self.draft_info_file.exists():
                self.logger.error(f"Draft file not found: {self.draft_info_file}")
                return False

            with open(self.draft_info_file, 'r', encoding='utf-8') as f:
                self.draft_data = json.load(f)

            self.is_loaded = True
            self.logger.info(f"Draft loaded successfully: {self.draft_path.name}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to load draft: {e}")
            return False

    def save_draft(self, create_backup: bool = True) -> bool:
        """
        ドラフトデータを保存

        Args:
            create_backup: バックアップ作成フラグ

        Returns:
            bool: 保存成功時True
        """
        if not self.is_loaded or not self.draft_data:
            self.logger.error("No draft data to save")
            return False

        try:
            # バックアップ作成
            if create_backup:
                self.create_backup()

            # draft_info.json保存
            with open(self.draft_info_file, 'w', encoding='utf-8') as f:
                json.dump(self.draft_data, f, indent=None, separators=(',', ':'), ensure_ascii=False)

            self.logger.info("Draft saved successfully")
            return True

        except Exception as e:
            self.logger.error(f"Failed to save draft: {e}")
            return False

    def create_backup(self) -> str:
        """
        現在のドラフトのバックアップを作成

        Returns:
            str: バックアップファイルパス
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = self.backup_dir / f"draft_info_{timestamp}.json"

        try:
            shutil.copy2(self.draft_info_file, backup_file)
            self.logger.info(f"Backup created: {backup_file}")
            return str(backup_file)
        except Exception as e:
            self.logger.error(f"Failed to create backup: {e}")
            return ""

    def get_tracks(self) -> List[Dict]:
        """
        全トラック情報を取得

        Returns:
            List[Dict]: トラックリスト
        """
        if not self.is_loaded:
            return []

        return self.draft_data.get('tracks', [])

    def get_track(self, track_index: int) -> Optional[Dict]:
        """
        指定インデックスのトラック情報を取得

        Args:
            track_index: トラックインデックス（0-8）

        Returns:
            Dict: トラック情報、存在しない場合None
        """
        tracks = self.get_tracks()
        if 0 <= track_index < len(tracks):
            return tracks[track_index]
        return None

    def get_layer_info(self) -> Dict[int, Dict]:
        """
        レイヤー情報マップを取得

        Returns:
            Dict: {layer_index: layer_info}形式のマップ
        """
        layer_map = {
            0: {'name': 'テンプレート背景動画', 'type': 'video', 'role': '元テンプレートの背景動画素材'},
            1: {'name': 'メインオーディオ1', 'type': 'audio', 'role': '音声・ナレーション1'},
            2: {'name': 'メインオーディオ2', 'type': 'audio', 'role': 'BGM・音声2'},
            3: {'name': '補助動画', 'type': 'video', 'role': '補助的な動画素材'},
            4: {'name': '生成画像メインレイヤー', 'type': 'video', 'role': '16枚のスピリチュアル・ファンタジー画像'},
            5: {'name': 'エフェクトレイヤー', 'type': 'effect', 'role': 'テンプレート元エフェクト'},
            6: {'name': 'メイン字幕', 'type': 'text', 'role': 'SRTファイル83セグメント字幕'},
            7: {'name': 'タイトル・サブタイトル', 'type': 'text', 'role': 'テンプレート元タイトル'},
            8: {'name': '追加テキスト', 'type': 'text', 'role': 'テンプレート元追加テキスト'}
        }

        # 実際のトラック数と合わせる
        tracks = self.get_tracks()
        result = {}
        for i in range(len(tracks)):
            if i in layer_map:
                result[i] = {**layer_map[i], 'segments': len(tracks[i].get('segments', []))}
            else:
                result[i] = {'name': f'不明レイヤー{i}', 'type': 'unknown', 'role': '詳細不明', 'segments': 0}

        return result

    def get_project_info(self) -> Dict:
        """
        プロジェクト情報を取得

        Returns:
            Dict: プロジェクト基本情報
        """
        if not self.is_loaded:
            return {}

        tracks = self.get_tracks()
        total_segments = sum(len(track.get('segments', [])) for track in tracks)

        return {
            'draft_name': self.draft_path.name,
            'draft_path': str(self.draft_path),
            'total_tracks': len(tracks),
            'total_segments': total_segments,
            'file_size': self.draft_info_file.stat().st_size if self.draft_info_file.exists() else 0,
            'last_modified': datetime.fromtimestamp(self.draft_info_file.stat().st_mtime) if self.draft_info_file.exists() else None
        }

    def validate_draft(self) -> Dict[str, Any]:
        """
        ドラフトデータの整合性チェック

        Returns:
            Dict: バリデーション結果
        """
        if not self.is_loaded:
            return {'valid': False, 'error': 'Draft not loaded'}

        try:
            # 基本構造チェック
            if 'tracks' not in self.draft_data:
                return {'valid': False, 'error': 'No tracks found in draft'}

            tracks = self.draft_data['tracks']
            if not isinstance(tracks, list):
                return {'valid': False, 'error': 'Tracks is not a list'}

            # 各トラックの構造チェック
            for i, track in enumerate(tracks):
                if not isinstance(track, dict):
                    return {'valid': False, 'error': f'Track {i} is not a dictionary'}

                required_fields = ['type', 'segments']
                for field in required_fields:
                    if field not in track:
                        return {'valid': False, 'error': f'Track {i} missing required field: {field}'}

            return {'valid': True, 'tracks_count': len(tracks)}

        except Exception as e:
            return {'valid': False, 'error': str(e)}

    def cleanup_old_backups(self, keep_count: int = 10) -> int:
        """
        古いバックアップファイルを削除

        Args:
            keep_count: 保持するバックアップ数

        Returns:
            int: 削除したファイル数
        """
        try:
            backup_files = list(self.backup_dir.glob("draft_info_*.json"))
            backup_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)

            deleted_count = 0
            for backup_file in backup_files[keep_count:]:
                backup_file.unlink()
                deleted_count += 1

            if deleted_count > 0:
                self.logger.info(f"Cleaned up {deleted_count} old backup files")

            return deleted_count

        except Exception as e:
            self.logger.error(f"Failed to cleanup backups: {e}")
            return 0


# 使用例とテスト用の関数
def test_draft_manager():
    """DraftManagerのテスト関数"""
    draft_path = os.getenv("CAPCUT_DRAFT_PATH")
    if not draft_path:
        print("ℹ️ Set CAPCUT_DRAFT_PATH to a CapCut draft directory (or draft_info.json) to run this test.")
        return

    # DraftManager初期化
    manager = DraftManager(draft_path)

    # ドラフト読み込み
    if manager.load_draft():
        print("✅ Draft loaded successfully")

        # バリデーション
        validation = manager.validate_draft()
        print(f"📋 Validation: {validation}")

        # プロジェクト情報
        project_info = manager.get_project_info()
        print(f"📊 Project Info: {project_info}")

        # レイヤー情報
        layer_info = manager.get_layer_info()
        print(f"🎬 Layers: {len(layer_info)}")
        for i, info in layer_info.items():
            print(f"  Layer {i}: {info['name']} ({info['segments']} segments)")

    else:
        print("❌ Failed to load draft")


if __name__ == "__main__":
    test_draft_manager()
