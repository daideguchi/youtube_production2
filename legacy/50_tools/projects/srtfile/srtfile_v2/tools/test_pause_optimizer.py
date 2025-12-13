"""
PauseOptimizer テストスクリプト
小規模・大規模セクションでのバッチ処理動作確認
"""

import sys
import os

# プロジェクトルートをパスに追加
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.pause_optimizer import PauseOptimizer
from loguru import logger


def create_test_sections(num_sections: int) -> list:
    """テスト用セクション生成"""
    test_topics = [
        "今日は天気が良いですね。",
        "さて、話は変わりますが、経済について考えてみましょう。",
        "次のポイントは非常に重要です。",
        "具体的な例を見てみましょう。",
        "それでは、新しいテーマに移ります。",
        "この点について、もう少し詳しく説明します。",
        "前の話と関連して、次の内容をお伝えします。",
        "ここで重要な転換点があります。",
        "続いて、別の観点から見てみましょう。",
        "最後に、まとめとして申し上げます。",
    ]

    sections = []
    for i in range(num_sections):
        # トピックをループして使用
        text = test_topics[i % len(test_topics)]
        sections.append([text])

    return sections


def test_small_scale():
    """小規模テスト（10セクション）"""
    logger.info("=" * 60)
    logger.info("🧪 小規模テスト開始（10セクション）")
    logger.info("=" * 60)

    optimizer = PauseOptimizer()
    sections = create_test_sections(10)

    logger.info(f"テストセクション数: {len(sections)}")

    try:
        pause_durations = optimizer.analyze_topic_changes(sections)

        logger.success(f"✅ 小規模テスト成功")
        logger.info(f"生成された無音長: {len(pause_durations)}個")
        logger.info(f"無音長リスト: {pause_durations}")

        # 検証
        assert len(pause_durations) == len(sections), f"無音長の数が不正: 期待{len(sections)}、実際{len(pause_durations)}"
        assert pause_durations[-1] == 0.0, "最後の無音長は0.0であるべき"
        assert all(0.3 <= d <= 1.5 or d == 0.0 for d in pause_durations), "無音長が範囲外"

        logger.success("✅ 小規模テスト: 全検証パス")
        return True

    except Exception as e:
        logger.error(f"❌ 小規模テスト失敗: {e}")
        return False


def test_large_scale():
    """大規模テスト（50セクション）- バッチ処理確認"""
    logger.info("=" * 60)
    logger.info("🧪 大規模テスト開始（50セクション）- バッチ処理検証")
    logger.info("=" * 60)

    optimizer = PauseOptimizer()
    sections = create_test_sections(50)

    logger.info(f"テストセクション数: {len(sections)}")
    logger.info("期待動作: 30セクション/バッチで2バッチ実行")

    try:
        pause_durations = optimizer.analyze_topic_changes(sections)

        logger.success(f"✅ 大規模テスト成功")
        logger.info(f"生成された無音長: {len(pause_durations)}個")
        logger.info(f"無音長リスト（先頭10個）: {pause_durations[:10]}")
        logger.info(f"無音長リスト（末尾10個）: {pause_durations[-10:]}")

        # 検証
        assert len(pause_durations) == len(sections), f"無音長の数が不正: 期待{len(sections)}、実際{len(pause_durations)}"
        assert pause_durations[-1] == 0.0, "最後の無音長は0.0であるべき"
        assert all(0.3 <= d <= 1.5 or d == 0.0 for d in pause_durations), "無音長が範囲外"

        logger.success("✅ 大規模テスト: 全検証パス")
        return True

    except Exception as e:
        logger.error(f"❌ 大規模テスト失敗: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False


def test_error_handling():
    """エラーハンドリングテスト"""
    logger.info("=" * 60)
    logger.info("🧪 エラーハンドリングテスト")
    logger.info("=" * 60)

    optimizer = PauseOptimizer()

    # 1セクションのみ（無音不要）
    logger.info("テスト: 1セクションのみ")
    sections = create_test_sections(1)
    pause_durations = optimizer.analyze_topic_changes(sections)

    assert len(pause_durations) == 1, "1セクションの場合、無音長は[0.0]のみ"
    assert pause_durations[0] == 0.0, "1セクションの無音長は0.0"

    logger.success("✅ エラーハンドリングテスト: パス")
    return True


def main():
    """全テスト実行"""
    logger.info("🚀 PauseOptimizer テストスイート開始")

    results = {
        "小規模テスト（10セクション）": test_small_scale(),
        "大規模テスト（50セクション・バッチ処理）": test_large_scale(),
        "エラーハンドリングテスト": test_error_handling(),
    }

    logger.info("=" * 60)
    logger.info("📊 テスト結果サマリー")
    logger.info("=" * 60)

    for test_name, result in results.items():
        status = "✅ 成功" if result else "❌ 失敗"
        logger.info(f"{test_name}: {status}")

    all_passed = all(results.values())

    if all_passed:
        logger.success("🎉 全テスト成功！")
        sys.exit(0)
    else:
        logger.error("💥 一部テスト失敗")
        sys.exit(1)


if __name__ == "__main__":
    main()
