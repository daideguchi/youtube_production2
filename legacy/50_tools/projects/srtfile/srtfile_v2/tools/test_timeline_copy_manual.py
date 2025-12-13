"""
Timeline自動コピー手動テスト
既存の出力ファイルを使って実際にコピーをテスト
"""

import sys
import os

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.timeline_copier import TimelineCopier
from loguru import logger
from pathlib import Path


def manual_copy_test():
    """手動コピーテスト"""
    logger.info("=" * 60)
    logger.info("🧪 手動Timeline自動コピーテスト")
    logger.info("=" * 60)

    copier = TimelineCopier()

    # テストケース: アカシック（CH05）の既存ファイル
    test_cases = [
        {
            "output_base": "output/default",
            "script_id": "16_アカシック台本",
            "channel_code": "CH05",
            "description": "アカシック台本16（existing）",
        },
    ]

    for case in test_cases:
        logger.info(f"テストケース: {case['description']}")
        logger.info(f"  script_id: {case['script_id']}")
        logger.info(f"  channel_code: {case['channel_code']}")

        # まずドライランでファイル検出確認
        logger.info("  [1] ドライラン実行...")
        success, copied_files = copier.copy_to_timeline(
            case["output_base"], case["script_id"], case["channel_code"], dry_run=True
        )

        if not success:
            logger.error(f"  ❌ ドライラン失敗（SRT未検出）")
            continue

        logger.success(f"  ✅ ドライラン成功: {len(copied_files)}件")

        # 実際のコピー実行（ユーザー確認）
        user_input = input(f"\n  実際にコピーを実行しますか？ (y/N): ")

        if user_input.lower() == "y":
            logger.info("  [2] 実際のコピー実行...")
            success, copied_files = copier.copy_to_timeline(
                case["output_base"],
                case["script_id"],
                case["channel_code"],
                dry_run=False,
            )

            if success:
                logger.success(f"  ✅ コピー成功: {len(copied_files)}件")

                # コピー先確認
                channel_dir = copier.get_channel_dir(case["channel_code"])
                if channel_dir:
                    logger.info(f"  📁 コピー先確認:")
                    for file in copied_files:
                        file_path = channel_dir / file
                        if file_path.exists():
                            size_kb = file_path.stat().st_size / 1024
                            logger.info(f"    ✅ {file} ({size_kb:.1f}KB)")
                        else:
                            logger.error(f"    ❌ {file} (ファイルが見つかりません)")
            else:
                logger.error(f"  ❌ コピー失敗")
        else:
            logger.info("  ⏭️  スキップ")

        logger.info("")


def main():
    """テスト実行"""
    logger.info("🚀 Timeline自動コピー手動テスト開始")
    logger.info("")

    manual_copy_test()

    logger.success("🎉 テスト完了")


if __name__ == "__main__":
    main()
