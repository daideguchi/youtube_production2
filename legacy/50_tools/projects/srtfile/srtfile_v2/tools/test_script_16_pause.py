"""
台本16（146セクション）での PauseOptimizer 動作テスト
実際のデータでGeminiタイムアウト問題が解決されたか確認
"""

import sys
import os
import json

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.pause_optimizer import PauseOptimizer
from loguru import logger


def load_optimized_sections(script_id: str):
    """
    台本の最適化済みセクションを読み込み

    既存の output/default/{script_id}/debug/optimized_sections.json から読み込む
    存在しない場合はエラー
    """
    base_path = f"output/default/{script_id}/debug/optimized_sections.json"

    if not os.path.exists(base_path):
        logger.error(f"最適化済みセクションファイルが見つかりません: {base_path}")
        logger.info("まず pipeline.py を実行してセクションを作成してください")
        return None

    with open(base_path, "r", encoding="utf-8") as f:
        sections = json.load(f)  # 直接リストのリスト

    # データ検証
    if not isinstance(sections, list) or not all(isinstance(s, list) for s in sections):
        logger.error("不正なデータ形式")
        return None

    logger.info(f"最適化済みセクション読み込み完了: {len(sections)}セクション")
    return sections


def test_real_script_pause_optimization():
    """実際の台本16（146セクション）で PauseOptimizer をテスト"""
    script_id = "16_アカシック台本"

    logger.info("=" * 60)
    logger.info(f"🧪 台本16 PauseOptimizer テスト開始")
    logger.info("=" * 60)

    # セクション読み込み
    sections = load_optimized_sections(script_id)

    if sections is None:
        logger.error("テスト失敗: セクションを読み込めませんでした")
        return False

    logger.info(f"テスト対象: {len(sections)}セクション")
    logger.info(f"期待動作: 30セクション/バッチで約5バッチ実行")
    logger.info(f"以前の動作: 全セクション一括 → 10分38秒後タイムアウト")
    logger.info(f"期待結果: バッチ処理により各バッチ30秒以内で完了")

    # PauseOptimizer 初期化
    optimizer = PauseOptimizer()

    # 分析実行（時間計測）
    import time

    start_time = time.time()

    try:
        pause_durations = optimizer.analyze_topic_changes(sections)
        elapsed_time = time.time() - start_time

        logger.success(f"✅ テスト成功！")
        logger.info(f"処理時間: {elapsed_time:.1f}秒")
        logger.info(f"生成された無音長: {len(pause_durations)}個")
        logger.info(f"無音長リスト（先頭10個）: {pause_durations[:10]}")
        logger.info(f"無音長リスト（末尾10個）: {pause_durations[-10:]}")

        # 検証
        expected_count = len(sections)
        if len(pause_durations) != expected_count:
            logger.error(
                f"❌ 無音長の数が不正: 期待{expected_count}、実際{len(pause_durations)}"
            )
            return False

        if pause_durations[-1] != 0.0:
            logger.error(f"❌ 最後の無音長は0.0であるべき: 実際{pause_durations[-1]}")
            return False

        if not all(0.3 <= d <= 1.5 or d == 0.0 for d in pause_durations):
            logger.error("❌ 無音長が範囲外（0.3-1.5秒）")
            return False

        logger.success("✅ 全検証パス")
        logger.info("=" * 60)
        logger.info("📊 結果サマリー")
        logger.info("=" * 60)
        logger.info(f"セクション数: {len(sections)}")
        logger.info(f"処理時間: {elapsed_time:.1f}秒（以前: 638秒タイムアウト）")
        logger.info(f"改善率: {((638 - elapsed_time) / 638 * 100):.1f}%")
        logger.info(f"生成された無音長: {len(pause_durations)}個（正しい）")

        return True

    except Exception as e:
        elapsed_time = time.time() - start_time
        logger.error(f"❌ テスト失敗: {e}")
        logger.error(f"処理時間（エラー時）: {elapsed_time:.1f}秒")
        import traceback

        logger.error(traceback.format_exc())
        return False


def main():
    """テスト実行"""
    logger.info("🚀 台本16 PauseOptimizer 実データテスト開始")

    success = test_real_script_pause_optimization()

    if success:
        logger.success("🎉 実データテスト成功！Geminiタイムアウト問題完全解決")
        sys.exit(0)
    else:
        logger.error("💥 実データテスト失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
