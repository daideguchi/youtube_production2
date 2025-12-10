#!/usr/bin/env python3
"""Set stage statuses to pending and update planning CSV to match asset state."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "commentary_01_srtfile_v2" / "data"
PROGRESS_DIR = PROJECT_ROOT / "progress" / "channels"
STAGES = [
    "topic_research",
    "script_outline",
    "script_draft",
    "script_enhancement",
    "script_review",
    "quality_check",
    "script_validation",
    "script_polish_ai",
    "script_tts_prepare",
    "audio_synthesis",
    "srt_generation",
    "timeline_copy",
    "image_generation",
]


def normalize_video(value: str) -> str:
    token = value.strip()
    return f"{int(token):03d}" if token.isdigit() else token


def update_status_files(channel: str, video: str, stage_limit: str) -> None:
    target_index = STAGES.index(stage_limit)
    stage_set = set(STAGES[target_index:])
    status_paths = [
        DATA_ROOT / channel / video / "status.json",
        DATA_ROOT / "_progress" / channel / f"{video}.json",
    ]
    processing_status = DATA_ROOT / "_progress" / "processing_status.json"
    if processing_status.exists():
        status_paths.append(processing_status)

    for path in status_paths:
        if not path.exists():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        stages = payload.setdefault("stages", {})
        for stage in stage_set:
            entry = stages.setdefault(stage, {})
            entry["status"] = "pending"
            entry.pop("updated_at", None)
            entry.pop("research_path", None)
            entry.pop("references_path", None)
        payload["status"] = "script_in_progress"
        metadata = payload.setdefault("metadata", {})
        metadata["sheet_progress_cell"] = f"{stage_limit}: pending"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def update_planning(channel: str, video: str, stage_limit: str) -> None:
    csv_path = PROGRESS_DIR / f"{channel}.csv"
    rows = []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = reader.fieldnames or []
        for row in reader:
            if row.get("動画番号") == video:
                row["進捗"] = f"{stage_limit}: pending"
            rows.append(row)
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Downgrade stages to pending")
    parser.add_argument("--channel-code", required=True)
    parser.add_argument("--videos", required=True, help="comma-separated video numbers")
    parser.add_argument(
        "--down-to",
        default="topic_research",
        choices=STAGES,
        help="最後に pending として残すステージ",
    )
    args = parser.parse_args()

    channel = args.channel_code.upper()
    videos = [normalize_video(token) for token in args.videos.split(",") if token.strip()]
    for video in videos:
        print(f"Setting {channel}-{video} -> {args.down_to}: pending")
        update_status_files(channel, video, args.down_to)
        update_planning(channel, video, args.down_to)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
