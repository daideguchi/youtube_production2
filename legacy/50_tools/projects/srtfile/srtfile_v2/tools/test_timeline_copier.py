"""
Timeline自動コピー機能テスト
"""

import sys
import os

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.timeline_copier import TimelineCopier
from loguru import logger


def test_channel_mapping():
    """チャンネルマッピングテスト"""
    logger.info("=" * 60)
    logger.info("🧪 チャンネルマッピングテスト")
    logger.info("=" * 60)

    copier = TimelineCopier()

    # 全チャンネルテスト
    test_channels = ["CH01", "CH02", "CH03", "CH04", "CH05"]

    for channel_code in test_channels:
        channel_dir = copier.get_channel_dir(channel_code)
        if channel_dir:
            logger.success(f"✅ {channel_code} → {channel_dir.name}")
        else:
            logger.error(f"❌ {channel_code} → マッピング失敗")

    logger.info("")


def test_file_detection():
    """ファイル検出テスト"""
    logger.info("=" * 60)
    logger.info("🧪 ファイル検出テスト")
    logger.info("=" * 60)

    copier = TimelineCopier()

    # テスト用に存在する出力を探す
    output_base = "output"

    # 実際に存在する出力ディレクトリを探す
    import os
    from pathlib import Path

    output_path = Path(output_base)
    if not output_path.exists():
        logger.warning(f"出力ディレクトリが存在しません: {output_base}")
        return

    # ディレクトリ一覧取得
    script_dirs = [d for d in output_path.iterdir() if d.is_dir()]

    if not script_dirs:
        logger.warning("テスト用の出力ディレクトリが見つかりません")
        return

    # 最初のディレクトリでテスト
    test_dir = script_dirs[0]
    logger.info(f"テスト対象ディレクトリ: {test_dir}")

    # ディレクトリ名からscript_idを抽出
    # 例: "CH04_人生の道標_178" → "178"
    dir_parts = test_dir.name.split("_")
    if len(dir_parts) >= 3:
        script_id = dir_parts[-1]
        channel_code = dir_parts[0]

        logger.info(f"script_id: {script_id}, channel_code: {channel_code}")

        # ファイル検出テスト
        found_files = copier.find_output_files(output_base, script_id, channel_code)

        if found_files:
            logger.success(f"✅ ファイル検出成功: {len(found_files)}件")
            for ext, path in found_files.items():
                logger.info(f"  [{ext}] {path}")
        else:
            logger.warning(f"⚠️ ファイル未検出")
    else:
        logger.warning(f"ディレクトリ名形式が不正: {test_dir.name}")

    logger.info("")


def test_dry_run_copy():
    """ドライランコピーテスト"""
    logger.info("=" * 60)
    logger.info("🧪 ドライランコピーテスト")
    logger.info("=" * 60)

    copier = TimelineCopier()

    # テスト用に存在する出力を探す
    output_base = "output"

    from pathlib import Path

    output_path = Path(output_base)
    if not output_path.exists():
        logger.warning(f"出力ディレクトリが存在しません: {output_base}")
        return

    # ディレクトリ一覧取得
    script_dirs = [d for d in output_path.iterdir() if d.is_dir()]

    if not script_dirs:
        logger.warning("テスト用の出力ディレクトリが見つかりません")
        return

    # 最初のディレクトリでテスト
    test_dir = script_dirs[0]

    # ディレクトリ名からscript_idを抽出
    dir_parts = test_dir.name.split("_")
    if len(dir_parts) >= 3:
        script_id = dir_parts[-1]
        channel_code = dir_parts[0]

        logger.info(f"テスト対象: {script_id} ({channel_code})")

        # ドライランコピー
        success, copied_files = copier.copy_to_timeline(
            output_base, script_id, channel_code, dry_run=True
        )

        if success:
            logger.success(f"✅ ドライラン成功: {len(copied_files)}件")
        else:
            logger.error(f"❌ ドライラン失敗")
    else:
        logger.warning(f"ディレクトリ名形式が不正: {test_dir.name}")

    logger.info("")


def main():
    """全テスト実行"""
    logger.info("🚀 Timeline自動コピー機能テスト開始")
    logger.info("")

    # テスト実行
    test_channel_mapping()
    test_file_detection()
    test_dry_run_copy()

    logger.success("🎉 全テスト完了")


if __name__ == "__main__":
    main()
