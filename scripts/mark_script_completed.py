"""
Mark script stages as completed for CH03 videos that have assembled.md ready.

Usage:
python3 scripts/mark_script_completed.py --channel CH03 --videos 053 054 ...
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import List

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "script_pipeline" / "data"


def mark_completed(channel: str, video: str) -> None:
    status_path = DATA_ROOT / channel / video / "status.json"
    if not status_path.exists():
        raise FileNotFoundError(status_path)
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    stages = payload.get("stages") or {}
    for key in [
        "topic_research",
        "script_outline",
        "chapter_brief",
        "script_draft",
        "script_enhancement",
        "script_review",
        "quality_check",
        "script_validation",
        "script_polish_ai",
        "script_tts_prepare",
    ]:
        if key in stages:
            stages[key]["status"] = "completed"
        else:
            stages[key] = {"status": "completed", "details": {}}
    payload["stages"] = stages
    payload["status"] = "completed"
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"updated {channel}-{video}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    parser.add_argument("--videos", nargs="+", required=True)
    args = parser.parse_args()
    for vid in args.videos:
        mark_completed(args.channel, vid)


if __name__ == "__main__":
    main()
