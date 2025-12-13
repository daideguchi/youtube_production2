
import argparse
import sys
import json
from pathlib import Path

# Add project root to path
sys.path.append("audio_tts_v2")

from tts.orchestrator import (
    preprocess_a_text,
    _presplit_headings,
    _raw_sentence_blocks_for_srt,
    _merge_numeric_blocks,
    _clean_srt_display_text
)

def dump_segments(channel: str, video: str):
    # Locate input file
    input_path = Path(f"audio_tts_v2/inputs/{channel}/{video}.txt")
    if not input_path.exists():
        print(f"[ERROR] Input not found: {input_path}")
        sys.exit(1)
        
    a_text = input_path.read_text(encoding="utf-8")
    
    # Replicate orchestrator segmentation logic EXACTLY
    
    # 1. Preprocess
    pre = preprocess_a_text(a_text, strip_markdown=False)
    a_text_clean = pre["a_text"]
    
    # 2. Strict Mechanical Segmentation
    # see orchestrator.py:139
    srt_source_text_raw = a_text_clean # assuming display_text is same for now or irrelevant for initial split
    srt_source_text = _presplit_headings(srt_source_text_raw)
    srt_blocks = _raw_sentence_blocks_for_srt(srt_source_text)
    
    # 3. Numeric Merge
    srt_blocks = _merge_numeric_blocks(srt_blocks)
    
    # 4. Cleaning (Line 157)
    for blk in srt_blocks:
        if "raw_text" not in blk:
            blk["raw_text"] = blk.get("text", "")
        raw_txt = str(blk.get("raw_text") or blk.get("text", ""))
        blk["text"] = _clean_srt_display_text(raw_txt)
        
    # Output
    out_path = Path(f"temp_srt_blocks_{channel}_{video}.json")
    out_path.write_text(json.dumps(srt_blocks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[SUCCESS] Dumped {len(srt_blocks)} blocks to {out_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    parser.add_argument("--video", required=True)
    args = parser.parse_args()
    
    dump_segments(args.channel, args.video)
