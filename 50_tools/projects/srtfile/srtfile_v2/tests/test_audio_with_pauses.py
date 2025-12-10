#!/usr/bin/env python3
"""
ポーズ統合音声合成テスト
最初の20セクションで、セクション内改行0.5秒 + セクション間可変ポーズを検証
"""

import sys
import json
from pathlib import Path
from loguru import logger

# プロジェクトルートをパスに追加
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app import voicevox_api, wav_tools


def main():
    """メイン処理"""
    # 既存データ読み込み
    debug_dir = Path("output/default/7_アカシック台本/debug")
    sections_path = debug_dir / "optimized_sections.json"
    pauses_path = debug_dir / "pause_durations.json"

    sections = json.loads(sections_path.read_text(encoding="utf-8"))
    pause_durations = json.loads(pauses_path.read_text(encoding="utf-8"))

    # 最初の20セクションのみテスト
    test_sections = sections[:20]
    test_pauses = pause_durations[:20]

    logger.info(f"音声合成テスト開始: {len(test_sections)}セクション")
    logger.info(f"話者ID: 13 (青山龍星)")

    # VOICEVOX接続
    try:
        client = voicevox_api.VoicevoxClient()
        speakers = client.get_speakers()
        logger.success(f"VOICEVOX Engine接続成功: {len(speakers)}話者")
    except Exception as e:
        logger.error(f"VOICEVOX Engine接続失敗: {e}")
        return

    # 出力ディレクトリ
    test_dir = Path("output/test_audio_pauses")
    test_dir.mkdir(parents=True, exist_ok=True)
    sections_dir = test_dir / "sections"
    sections_dir.mkdir(exist_ok=True)

    # 各セクション音声合成
    wav_files = []
    durations = []

    for i, section in enumerate(test_sections, 1):
        logger.info(f"セクション{i}/{len(test_sections)}: {section[0][:30]}...")

        # 複数行セクションは行間に0.3秒無音
        if len(section) > 1:
            logger.debug(f"  複数行セクション（{len(section)}行）→ 行間0.3秒挿入")
            wav_bytes = client.synthesize_with_linebreaks(section, speaker=13, pause_duration=0.3)
        else:
            # 1行セクションは通常合成
            wav_bytes = client.synthesize_normal(section[0], speaker=13)

        # WAV保存
        wav_path = sections_dir / f"{i:03d}.wav"
        wav_tools.save_wav(wav_bytes, wav_path)
        wav_files.append(wav_path)

        # 実測長取得
        duration = wav_tools.duration(wav_bytes)
        durations.append(duration)

        logger.success(f"  セクション{i}完了: {duration:.2f}秒")

    logger.success(f"全セクション合成完了: {len(wav_files)}ファイル")

    # WAV連結（可変長無音挿入）
    logger.info("WAV連結開始（セクション間可変ポーズ）")
    final_wav = test_dir / "test_20sections.wav"
    wav_tools.concat(wav_files, final_wav, silence_durations=test_pauses)
    logger.success(f"WAV連結完了: {final_wav}")

    # 統計表示
    total_audio = sum(durations)
    total_silence = sum(test_pauses[:-1])  # 最後の0.0を除外
    total_duration = total_audio + total_silence

    logger.info("=" * 60)
    logger.info("音声統計:")
    logger.info(f"  純音声時間: {total_audio:.1f}秒")
    logger.info(f"  無音時間: {total_silence:.1f}秒")
    logger.info(f"  総時間: {total_duration:.1f}秒")
    logger.info(f"  出力ファイル: {final_wav}")
    logger.info("=" * 60)

    # ポーズ詳細
    logger.info("セクション間ポーズ詳細:")
    for i, pause in enumerate(test_pauses, 1):
        if i <= len(test_sections):
            section_text = "\n".join(test_sections[i-1])
            logger.info(f"  セクション{i}後: {pause}秒 | {section_text[:40]}...")


if __name__ == "__main__":
    main()
