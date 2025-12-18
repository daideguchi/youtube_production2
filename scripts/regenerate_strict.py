#!/usr/bin/env python3
"""
CH10-001の音声とSRTをstrict pipelineで再生成するためのスクリプト
辞書更新と修正された設定を反映する
"""
import sys
from pathlib import Path

from _bootstrap import bootstrap

REPO_ROOT = bootstrap()

from audio_tts_v2.tts.strict_orchestrator import run_strict_pipeline
from factory_common.paths import video_root

def regenerate_audio_srt_strict_ch10_001():
    # パラメータを設定
    channel = "CH10"
    video_no = "001"
    engine = "voicevox"

    video_dir = video_root(channel, video_no)

    # 入力テキストをassembled.mdからロード
    input_path = video_dir / "content" / "assembled.md"
    if not input_path.exists():
        print(f"Input text not found at {input_path}")
        return

    input_text = input_path.read_text(encoding="utf-8")

    # 出力パスを設定
    output_dir = video_dir / "audio_prep"
    output_wav = output_dir / "CH10-001-strict.wav"
    output_log = output_dir / "CH10-001-strict.log.json"

    # voicepeak_configはNone
    voicepeak_config = None

    # artifact_rootは同じディレクトリ
    artifact_root = output_dir

    print("Starting strict pipeline for CH10-001...")
    print(f"Input text length: {len(input_text)} characters")

    # strict pipelineを実行
    run_strict_pipeline(
        channel=channel,
        video_no=video_no,
        input_text=input_text,
        output_wav=output_wav,
        output_log=output_log,
        engine=engine,
        voicepeak_config=voicepeak_config,
        artifact_root=artifact_root
    )

    print("Strict pipeline completed!")
    print(f"Audio output: {output_wav}")
    print(f"Log output: {output_log}")

if __name__ == "__main__":
    regenerate_audio_srt_strict_ch10_001()
