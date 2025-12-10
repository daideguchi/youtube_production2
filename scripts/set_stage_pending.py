#!/usr/bin/env python3
"""Batch downgrade stage statuses to pending so planning/status match assets."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "commentary_01_srtfile_v2" / "core" / "tools" / "progress_manager.py"

STAGE_ORDER = [
    "image_generation",
    "timeline_copy",
    "srt_generation",
    "audio_synthesis",
    "script_tts_prepare",
    "script_polish_ai",
    "script_validation",
    "quality_check",
    "script_review",
    "script_enhancement",
    "script_draft",
    "script_outline",
    "topic_research",
]


def normalize_video(value: str) -> str:
    token = value.strip()
    return f"{int(token):03d}" if token.isdigit() else token


def run(cmd):
    return subprocess.run(cmd, cwd=str(PROJECT_ROOT), text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Set stages to pending from latest to earliest")
    parser.add_argument("--channel-code", required=True)
    parser.add_argument("--videos", required=True, help="comma-separated video numbers")
    parser.add_argument(
        "--down-to",
        default="topic_research",
        choices=STAGE_ORDER,
        help="最終的に pending に戻したいステージ",
    )
    args = parser.parse_args()

    channel = args.channel_code.upper()
    videos = [normalize_video(token) for token in args.videos.split(",") if token.strip()]
    target_index = STAGE_ORDER.index(args.down_to)
    stages_to_reset = STAGE_ORDER[: target_index + 1]  # includes down_to

    for video in videos:
        print(f"== {channel}-{video} ==")
        for idx, stage in enumerate(stages_to_reset):
            cmd = [
                "python3",
                str(RUNNER),
                "update-stage",
                "--channel-code",
                channel,
                "--video-number",
                video,
                "--stage",
                stage,
                "--state",
                "pending",
                "--no-strict",
                "--no-update-sheet",
            ]
            if idx == len(stages_to_reset) - 1:
                cmd.remove("--no-update-sheet")
                cmd.append("--global-status")
                cmd.append("script_in_progress")
            print("  ->", stage)
            result = run(cmd)
            if result.returncode != 0:
                print(result.stdout)
                print(result.stderr)
                return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
