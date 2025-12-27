#!/usr/bin/env python3
"""
Format SRT cue texts by inserting intentional in-cue newlines.

Goal:
  - Prevent GUI auto-wrap from breaking Japanese meaning chunks at awkward points.
  - Keep audio/subtitle CONTENT identical (no rewrites); only adjust line breaks.

This is designed to be loosely coupled:
  - Can be run as a post-process on any .srt file (CapCut/Remotion compatible).
  - The main TTS pipeline also calls the same formatter when generating SRT.

Usage:
  python3 scripts/format_srt_linebreaks.py path/to/in.srt --out path/to/out.srt
  python3 scripts/format_srt_linebreaks.py path/to/in.srt --in-place
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap()

from audio_tts.tts.llm_adapter import format_srt_lines  # noqa: E402

SRT_SEPARATOR = re.compile(r"\r?\n\r?\n")
TIMING_LINE = re.compile(r"^(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})$")


@dataclass(frozen=True)
class SRTCue:
    index: int
    start: str
    end: str
    text: str


def parse_srt(path: Path) -> List[SRTCue]:
    raw = path.read_text(encoding="utf-8")
    blocks = [b.strip("\n\r ") for b in SRT_SEPARATOR.split(raw) if b.strip("\n\r ")]
    cues: List[SRTCue] = []
    for block in blocks:
        lines = block.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if len(lines) < 3:
            raise ValueError(f"Invalid SRT block (too short): {block[:120]}")
        index = int(lines[0].strip())
        m = TIMING_LINE.match(lines[1].strip())
        if not m:
            raise ValueError(f"Invalid timing line: {lines[1]}")
        start, end = m.groups()
        body = "\n".join(lines[2:]).strip()
        cues.append(SRTCue(index=index, start=start, end=end, text=body))
    return cues


def write_srt(path: Path, cues: List[SRTCue]) -> None:
    lines: list[str] = []
    for cue in cues:
        body = cue.text.strip()
        lines.append(str(cue.index))
        lines.append(f"{cue.start} --> {cue.end}")
        lines.append(body)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Insert intentional line breaks into SRT cue texts (LLM-assisted, safe fallback).")
    parser.add_argument("srt", help="Input .srt path")
    parser.add_argument("--out", help="Output .srt path (default: <input>.linebreak.srt)")
    parser.add_argument("--in-place", action="store_true", help="Overwrite input file in-place")
    parser.add_argument("--max-lines", type=int, default=2, help="MAX_LINES (default: 2)")
    parser.add_argument("--max-chars", type=int, default=24, help="MAX_CHARS_PER_LINE (default: 24)")
    parser.add_argument("--retry-limit", type=int, default=1, help="RETRY_LIMIT (default: 1)")
    parser.add_argument("--timeout", type=int, default=30, help="LLM timeout seconds (default: 30)")
    parser.add_argument("--batch-size", type=int, default=20, help="LLM batch size (default: 20)")
    parser.add_argument("--llm-model", help="Optional model key override (sets LLM_FORCE_MODELS)")
    parser.add_argument("--disable", action="store_true", help="Disable linebreak formatting (pass-through)")
    args = parser.parse_args()

    in_path = Path(args.srt).expanduser().resolve()
    if not in_path.exists():
        raise FileNotFoundError(in_path)

    if args.llm_model:
        os.environ["LLM_FORCE_MODELS"] = str(args.llm_model).strip()

    os.environ["SRT_LINEBREAK_ENABLED"] = "0" if args.disable else "1"
    os.environ["SRT_LINEBREAK_MAX_LINES"] = str(max(1, int(args.max_lines)))
    os.environ["SRT_LINEBREAK_MAX_CHARS_PER_LINE"] = str(max(4, int(args.max_chars)))
    os.environ["SRT_LINEBREAK_RETRY_LIMIT"] = str(max(0, int(args.retry_limit)))

    cues = parse_srt(in_path)
    entries = [{"index": c.index, "start_ts": c.start, "end_ts": c.end, "text": c.text} for c in cues]

    # model/api_key args are kept for backward compatibility; routing is handled by LLMRouter/env.
    formatted = format_srt_lines(
        entries,
        model="",
        api_key="",
        target_len=int(args.max_chars),
        timeout=int(args.timeout),
        batch_size=int(args.batch_size),
    )

    out_cues: List[SRTCue] = []
    for cue, ent in zip(cues, formatted):
        out_cues.append(
            SRTCue(
                index=cue.index,
                start=cue.start,
                end=cue.end,
                text=str(ent.get("text", cue.text) or cue.text),
            )
        )

    if args.in_place and args.out:
        raise SystemExit("Use either --in-place OR --out (not both).")

    if args.in_place:
        out_path = in_path
    else:
        out_path = Path(args.out).expanduser().resolve() if args.out else in_path.with_suffix(".linebreak.srt")

    write_srt(out_path, out_cues)
    print(f"✅ Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

