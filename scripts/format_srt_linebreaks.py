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

from factory_common.routing_lockdown import (  # noqa: E402
    assert_no_llm_model_overrides,
    assert_task_overrides_unchanged,
    lockdown_active,
)
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
    parser = argparse.ArgumentParser(
        description="Insert intentional line breaks into SRT cue texts (heuristic by default; optional LLM mode)."
    )
    parser.add_argument("srt", help="Input .srt path")
    parser.add_argument("--out", help="Output .srt path (default: <input>.linebreak.srt)")
    parser.add_argument("--in-place", action="store_true", help="Overwrite input file in-place")
    parser.add_argument("--mode", choices=["heuristic", "llm", "off"], default="heuristic", help="Linebreak mode (default: heuristic)")
    parser.add_argument("--max-lines", type=int, default=2, help="MAX_LINES (default: 2)")
    parser.add_argument("--max-chars", type=int, default=24, help="MAX_CHARS_PER_LINE (default: 24)")
    parser.add_argument("--retry-limit", type=int, default=1, help="RETRY_LIMIT (default: 1)")
    parser.add_argument("--timeout", type=int, default=30, help="LLM timeout seconds (default: 30)")
    parser.add_argument("--batch-size", type=int, default=20, help="LLM batch size (default: 20)")
    parser.add_argument("--llm-model", help="Optional model key override (sets LLM_FORCE_MODELS). DEBUG ONLY; prefer --llm-slot.")
    parser.add_argument(
        "--llm-slot",
        type=int,
        help="Force numeric LLM routing slot (sets LLM_MODEL_SLOT). Preferred over --llm-model.",
    )
    parser.add_argument("--disable", action="store_true", help="Disable linebreak formatting (pass-through)")
    args = parser.parse_args()

    assert_no_llm_model_overrides(context="format_srt_linebreaks.py (startup)")
    assert_task_overrides_unchanged(context="format_srt_linebreaks.py (startup)")

    in_path = Path(args.srt).expanduser().resolve()
    if not in_path.exists():
        raise FileNotFoundError(in_path)

    if getattr(args, "llm_slot", None) is not None:
        try:
            slot = int(args.llm_slot)
        except Exception:
            raise SystemExit(f"--llm-slot must be an integer; got: {args.llm_slot}")
        if slot < 0:
            raise SystemExit(f"--llm-slot must be >= 0; got: {slot}")
        os.environ["LLM_MODEL_SLOT"] = str(slot)

    if args.llm_model:
        raw = str(args.llm_model).strip()
        # Compatibility: pure integer means slot selection.
        if raw.isdigit():
            os.environ["LLM_MODEL_SLOT"] = raw
        else:
            if lockdown_active():
                raise SystemExit(
                    "Forbidden: --llm-model with non-numeric values under YTM_ROUTING_LOCKDOWN=1. "
                    "Use --llm-slot (numeric) instead, or set YTM_EMERGENCY_OVERRIDE=1 for one-off debugging."
                )
            os.environ["LLM_FORCE_MODELS"] = raw

    os.environ["SRT_LINEBREAK_ENABLED"] = "0" if args.disable else "1"
    os.environ["SRT_LINEBREAK_MODE"] = "off" if args.mode == "off" else str(args.mode)
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
