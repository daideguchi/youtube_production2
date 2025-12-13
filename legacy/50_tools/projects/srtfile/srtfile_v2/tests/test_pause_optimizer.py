#!/usr/bin/env python3
"""
ポーズ最適化システムの動作テスト
既存の最適化済みセクションを使用してGemini 2.5 Proの話題変化分析のみを実行
"""

import sys
import json
from pathlib import Path
from loguru import logger

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.pause_optimizer import PauseOptimizer


def main():
    """メイン処理"""
    # 台本7の既存セクション読み込み
    optimized_json_path = Path("output/default/7_アカシック台本/debug/optimized_sections.json")

    if not optimized_json_path.exists():
        logger.error(f"最適化済みセクションが見つかりません: {optimized_json_path}")
        return

    logger.info(f"最適化済みセクション読み込み: {optimized_json_path}")
    sections = json.loads(optimized_json_path.read_text(encoding="utf-8"))
    logger.success(f"読み込み完了: {len(sections)}セクション")

    # 最初の10セクションを表示
    logger.info("セクション内容（先頭10個）:")
    for i, section in enumerate(sections[:10], 1):
        text = "\n".join(section)
        logger.info(f"  セクション{i}: {text[:40]}...")

    # ポーズ最適化実行
    logger.info("=" * 60)
    logger.info("Gemini 2.5 Proで話題変化分析開始")
    logger.info("=" * 60)

    try:
        pause_optimizer = PauseOptimizer()
        pause_durations = pause_optimizer.analyze_topic_changes(sections)

        logger.success(f"話題変化分析完了: {len(pause_durations)}個の無音長決定")

        # 結果表示
        logger.info("=" * 60)
        logger.info("無音長リスト（先頭20個）:")
        for i, duration in enumerate(pause_durations[:20], 1):
            section_text = "\n".join(sections[i-1]) if i <= len(sections) else ""
            logger.info(f"  セクション{i}後: {duration}秒 | {section_text[:30]}...")

        # 統計表示
        count_05 = sum(1 for d in pause_durations if d == 0.5)
        count_08 = sum(1 for d in pause_durations if d == 0.8)
        count_10 = sum(1 for d in pause_durations if d == 1.0)
        count_00 = sum(1 for d in pause_durations if d == 0.0)

        logger.info("=" * 60)
        logger.info("無音長統計:")
        logger.info(f"  0.5秒（話題継続）: {count_05}箇所 ({count_05/len(pause_durations)*100:.1f}%)")
        logger.info(f"  0.8秒（小さな転換）: {count_08}箇所 ({count_08/len(pause_durations)*100:.1f}%)")
        logger.info(f"  1.0秒（明確な転換）: {count_10}箇所 ({count_10/len(pause_durations)*100:.1f}%)")
        logger.info(f"  0.0秒（最終セクション）: {count_00}箇所")
        logger.info("=" * 60)

        # 結果保存
        output_path = Path("output/default/7_アカシック台本/debug/pause_durations.json")
        output_path.write_text(
            json.dumps(pause_durations, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.success(f"無音長リスト保存: {output_path}")

    except Exception as e:
        logger.error(f"話題変化分析失敗: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
