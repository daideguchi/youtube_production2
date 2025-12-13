#!/usr/bin/env python3
"""
Generate a new SRT that keeps the original timing but swaps the body text for
segment A (display script) content.

Usage:
  python3 scripts/generate_subtitles.py CH06-001 \
      --source-srt workspaces/audio/final/CH06/001/CH06-001.srt \
      --subtitle-json workspaces/scripts/CH06/001/audio_prep/subtitle_segments.json \
      --output workspaces/audio/final/CH06/001/CH06-001_from_A.srt

If --source-srt/--subtitle-json/--output are omitted, defaults inside the
chapter directory are used.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factory_common.paths import audio_final_dir, video_root


def resolve_default_paths(chapter_id: str) -> tuple[Path, Path, Path]:
    part1, part2 = chapter_id.split("-", 1)
    channel = part1.strip().upper()
    video = str(part2).strip().zfill(3)
    final_dir = audio_final_dir(channel, video)
    source_srt = final_dir / f"{channel}-{video}.srt"
    subtitle_json = video_root(channel, video) / "audio_prep" / "subtitle_segments.json"
    output_srt = final_dir / f"{channel}-{video}_from_A.srt"
    return source_srt, subtitle_json, output_srt


@dataclass
class SRTCue:
    index: int
    start: str
    end: str
    text: str


SRT_SEPARATOR = re.compile(r"\r?\n\r?\n")
TIMING_LINE = re.compile(r"^(\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2},\d{3})$")


def parse_srt(path: Path) -> List[SRTCue]:
    raw = path.read_text(encoding="utf-8")
    blocks = [b.strip() for b in SRT_SEPARATOR.split(raw) if b.strip()]
    cues: List[SRTCue] = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError(f"Invalid SRT block: {block[:80]}")
        index = int(lines[0].strip())
        m = TIMING_LINE.match(lines[1].strip())
        if not m:
            raise ValueError(f"Invalid timing line: {lines[1]}")
        start, end = m.groups()
        body = "\n".join(lines[2:]).strip()
        cues.append(SRTCue(index=index, start=start, end=end, text=body))
    return cues


def flatten_subtitle_segments(path: Path) -> List[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    lines: List[str] = []
    for entry in payload:
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line:
                lines.append(line)
    return lines


def load_tts_segments(path: Path) -> List[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    texts: List[str] = []
    for entry in payload:
        text = (entry.get("text") or "").strip()
        if text:
            texts.append(text)
    return texts


def distribute_lines(lines: List[str], cue_count: int, max_lines_per_cue: int) -> List[str]:
    assignments: List[str] = []
    idx = 0
    total_lines = len(lines)
    for cue_idx in range(cue_count):
        remaining_cues = cue_count - cue_idx
        remaining_lines = total_lines - idx
        if remaining_lines <= 0:
            assignments.append("")
            continue
        min_allocation = max(1, remaining_lines - (remaining_cues - 1))
        avg_allocation = math.ceil(remaining_lines / remaining_cues)
        take = min(max_lines_per_cue, avg_allocation)
        take = max(take, 1)
        take = min(take, min_allocation)
        chunk = lines[idx : idx + take]
        idx += take
        assignments.append("\n".join(chunk))
    if idx < total_lines:
        raise ValueError("Not all subtitle lines were consumed by the SRT cues.")
    return assignments


def fit_tts_texts_to_cues(texts: List[str], cue_count: int) -> List[str]:
    if cue_count <= 0:
        return []
    if len(texts) == cue_count:
        return texts
    assignments: List[str] = []
    idx = 0
    total = len(texts)
    while len(assignments) < cue_count:
        remaining_cues = cue_count - len(assignments)
        remaining_texts = total - idx
        if remaining_texts <= 0:
            assignments.append("")
            continue
        take = max(1, remaining_texts // remaining_cues)
        if remaining_texts % remaining_cues != 0:
            take += 1
        take = max(1, take)
        take = min(take, remaining_texts)
        chunk = texts[idx : idx + take]
        idx += take
        assignments.append("\n".join(chunk))
    return assignments


def write_srt(path: Path, cues: List[SRTCue], texts: List[str]) -> None:
    assert len(cues) == len(texts)
    lines = []
    for cue, text in zip(cues, texts):
        body = text.strip() or ""
        lines.append(f"{cue.index}\n{cue.start} --> {cue.end}\n{body}\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def _timecode_to_ms(code: str) -> int:
    hh, mm, rest = code.split(":")
    ss, ms = rest.split(",")
    return (int(hh) * 3600 + int(mm) * 60 + int(ss)) * 1000 + int(ms)


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate SRT text while keeping existing timings.")
    parser.add_argument("chapter_id")
    parser.add_argument("--source-srt", dest="source_srt")
    parser.add_argument("--subtitle-json", dest="subtitle_json")
    parser.add_argument("--output", dest="output_srt")
    parser.add_argument("--max-lines-per-cue", type=int, default=3, help="Maximum number of A-lines to pack into one SRT cue (default: 3)")
    parser.add_argument("--text-source", choices=["subtitle", "tts"], default="subtitle", help="Use subtitle_segments (A) or tts_segments (B) as the text source")
    parser.add_argument("--tts-json", dest="tts_json", help="Path to tts_segments.json when --text-source=tts")
    parser.add_argument("--timeline-json", dest="timeline_json", help="Path to write the timeline metadata JSON when --text-source=tts")
    args = parser.parse_args()

    default_source, default_subs, default_output = resolve_default_paths(args.chapter_id)
    source_srt = Path(args.source_srt) if args.source_srt else default_source
    subtitle_json = Path(args.subtitle_json) if args.subtitle_json else default_subs
    tts_json = Path(args.tts_json) if args.tts_json else (subtitle_json.parent / "tts_segments.json")
    output_srt = Path(args.output_srt) if args.output_srt else default_output

    cues = parse_srt(source_srt)
    if args.text_source == "subtitle":
        subtitle_lines = flatten_subtitle_segments(subtitle_json)
        if not subtitle_lines:
            raise ValueError("subtitle_segments.json does not contain any text lines.")
        new_texts = distribute_lines(subtitle_lines, len(cues), max(1, args.max_lines_per_cue))
        payload_count = len(subtitle_lines)
    else:
        payload = json.loads(tts_json.read_text(encoding="utf-8"))
        if not payload:
            raise ValueError("tts_segments.json does not contain any text segments.")
        tts_segments = [
            {
                "chunk_id": entry.get("chunk_id"),
                "text": (entry.get("text") or "").strip(),
                "is_heading": bool(entry.get("is_heading")),
                "pause_before_ms": int(entry.get("pause_before_ms", 0)),
                "pause_after_ms": int(entry.get("pause_after_ms", 0)),
                "inline_silences_total_ms": int(entry.get("inline_silences_total_ms", 0)),
            }
            for entry in payload
            if (entry.get("text") or "").strip()
        ]
        if not tts_segments:
            raise ValueError("tts_segments.json does not contain any text segments.")
        tts_texts = [seg["text"] for seg in tts_segments]
        new_texts = fit_tts_texts_to_cues(tts_texts, len(cues))
        payload_count = len(tts_texts)
        if args.timeline_json:
            timeline_entries = []
            for cue, text in zip(cues, new_texts):
                seg = tts_segments[min(len(timeline_entries), len(tts_segments) - 1)]
                timeline_entries.append(
                    {
                        "index": cue.index,
                        "start": cue.start,
                        "end": cue.end,
                        "chunk_id": seg["chunk_id"],
                        "text": text,
                        "pause_before_ms": seg["pause_before_ms"],
                        "pause_after_ms": seg["pause_after_ms"],
                        "inline_silences_total_ms": seg["inline_silences_total_ms"],
                        "duration_ms": _timecode_to_ms(cue.end) - _timecode_to_ms(cue.start),
                    }
                )
            Path(args.timeline_json).parent.mkdir(parents=True, exist_ok=True)
            Path(args.timeline_json).write_text(json.dumps(timeline_entries, ensure_ascii=False, indent=2), encoding="utf-8")

    write_srt(output_srt, cues, new_texts)
    print(f"Generated {output_srt} using {payload_count} source segments across {len(cues)} cues (text_source={args.text_source}).")


if __name__ == "__main__":
    main()
