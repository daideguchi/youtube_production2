#!/usr/bin/env python3
"""
CH10-001の音声とSRTを再生成するためのスクリプト
辞書更新と修正された設定を反映する
"""
import json
import sys
from pathlib import Path

from _bootstrap import bootstrap

REPO_ROOT = bootstrap()

from audio_tts_v2.tts.synthesis import voicevox_synthesis_chunks
from audio_tts_v2.tts.routing import load_routing_config
from audio_tts_v2.tts.strict_synthesizer import generate_srt
from audio_tts_v2.tts.strict_structure import AudioSegment
from factory_common.paths import audio_pkg_root, video_root

def load_knowledge_base():
    """Knowledge baseをロードする関数"""
    kb_path = audio_pkg_root() / "data" / "global_knowledge_base.json"
    if kb_path.exists():
        data = json.loads(kb_path.read_text(encoding="utf-8"))
        return data
    else:
        print(f"Knowledge base not found at {kb_path}")
        return {"words": {}}

def regenerate_audio_and_srt_ch10_001():
    channel = "CH10"
    video_no = "001"
    video_dir = video_root(channel, video_no)

    # Knowledge baseをロード (辞書設定を反映)
    kb = load_knowledge_base()

    # 音声合成の設定をロード
    cfg = load_routing_config()

    # CH10-001用のテキストをロード
    b_text_path = video_dir / "audio_prep" / "b_text.txt"
    if not b_text_path.exists():
        print(f"b_text.txt not found at {b_text_path}")
        return

    # テキストを読み込む
    b_text_content = b_text_path.read_text(encoding="utf-8")

    # 改行でテキストを分割し、ブロックに変換
    blocks = []
    lines = b_text_content.split("\n")
    for i, line in enumerate(lines):
        if line.strip():
            blocks.append({
                "index": i,
                "text": line.strip(),
            })

    # 音声合成を実行
    output_path = video_dir / "audio_prep" / f"{channel}-{video_no}-regenerated.wav"

    print("Starting audio synthesis for CH10-001...")
    print(f"Input text length: {len(b_text_content)} characters")
    print(f"Number of blocks: {len(blocks)}")
    print("Using updated dictionary with the following key entries:")
    # Show key dictionary entries for verification
    key_entries = ["その分", "同じ道", "生"]
    for entry in key_entries:
        if entry in kb.get("words", {}):
            print(f"  '{entry}' -> '{kb['words'][entry]}'")
        else:
            print(f"  '{entry}' -> not in dictionary")

    # 結果を保存（音声合成）
    result = voicevox_synthesis_chunks(
        blocks=blocks,
        out_wav=output_path,
        channel="CH10",
        cfg=cfg
    )

    print(f"Audio synthesis completed!")
    print(f"Output file: {result.wav_path}")
    print(f"Duration: {result.duration_sec:.2f} seconds")
    print(f"Sample rate: {result.sample_rate} Hz")

    # SRTファイルを生成するためにログファイルからセグメント情報をロード
    log_path = video_dir / "audio_prep" / "log.json"
    if not log_path.exists():
        print(f"Log file not found at {log_path}, cannot generate SRT")
        return

    log_data = json.loads(log_path.read_text(encoding="utf-8"))
    segments_data = log_data.get("segments", [])

    # AudioSegmentオブジェクトに変換
    segments = []
    for seg_data in segments_data:
        # pre_pause_secとpost_pause_secはSRT生成用に保持
        # duration_secは音声の再生時間として使用
        segment = AudioSegment(
            text=seg_data.get("text", ""),
            reading=seg_data.get("reading"),
            pre_pause_sec=seg_data.get("pre", 0.0),
            post_pause_sec=seg_data.get("post", 0.0),
            is_heading=seg_data.get("heading", False),
            original_line_index=seg_data.get("index", 0),
            duration_sec=seg_data.get("duration", 0.0),
            mecab_reading=seg_data.get("mecab", ""),
            voicevox_reading=seg_data.get("voicevox", ""),
            arbiter_verdict=seg_data.get("verdict", "")
        )
        segments.append(segment)

    # SRTファイルを生成
    srt_path = output_path.with_suffix(".srt")
    generate_srt(segments, srt_path)

    print(f"SRT generation completed!")
    print(f"Output SRT file: {srt_path}")

if __name__ == "__main__":
    regenerate_audio_and_srt_ch10_001()
