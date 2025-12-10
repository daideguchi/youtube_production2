#!/usr/bin/env python3
"""
Sync channel CSV progress columns from status.json (SoT).

Usage:
  python3 scripts/sync_status_mirrors.py [--channel CH06]

Rules:
  - SoT: commentary_01_srtfile_v2/data/CHxx/<video>/status.json
  - Mirrors: progress/channels/CHxx.csv
  - Only the progress column is updated; other columns stay untouched.
"""

import argparse
import csv
import glob
import json
from enum import Enum
from pathlib import Path
from typing import Dict, Iterable

ROOT = Path(__file__).resolve().parents[1]
DATA_BASE = ROOT / "commentary_01_srtfile_v2" / "data"

CHANNELS_DIR = ROOT / "progress" / "channels"


class _ProgressStatus(Enum):
    STAGE_PENDING = "pending"
    STAGE_PROCESSING = "processing"
    STAGE_COMPLETED = "completed"
    STAGE_FAILED = "failed"
    STAGE_RERUN_REQUESTED = "rerun_requested"
    STAGE_RERUN_IN_PROGRESS = "rerun_in_progress"
    STAGE_RERUN_COMPLETED = "rerun_completed"
    SCRIPT_IN_PROGRESS = "script_in_progress"
    SCRIPT_READY = "script_ready"
    SCRIPT_VALIDATED = "script_validated"
    PROCESSING = "processing"
    AUDIO_DONE = "audio_done"
    TIMELINE_READY = "timeline_ready"
    IMAGES_DONE = "images_done"
    CAPCUT_DONE = "capcut_done"
    COMPLETED = "completed"
    RERUN_REQUESTED = "rerun_requested"
    RERUN_IN_PROGRESS = "rerun_in_progress"
    RERUN_COMPLETED = "rerun_completed"


def _normalize_status_value(value) -> str:
    if isinstance(value, _ProgressStatus):
        return value.value
    if isinstance(value, str):
        raw = value.strip().lower()
        # ProgressStatus.prefix を除去して解釈できるようにする
        if raw.startswith("progressstatus."):
            raw = raw.split(".", 1)[1]
        for member in _ProgressStatus:
            if raw == member.name.lower() or raw == member.value:
                return member.value
        return raw
    return "pending"


def _normalize_entire_status(data: Dict) -> Dict[str, str]:
    # status.json を mirror 用に安全な文字列へ統一
    data = dict(data)
    data["status"] = _normalize_status_value(data.get("status"))
    stages = data.get("stages", {})
    for _, payload in stages.items():
        if isinstance(payload, dict) and "status" in payload:
            payload["status"] = _normalize_status_value(payload.get("status"))
    return data


def load_status_map(ch_filter: str=None) -> Dict[str, str]:
    status_map: Dict[str, str] = {}
    pattern = f"{ch_filter}" if ch_filter else "*"
    for status_path in glob.glob(str(DATA_BASE / pattern / "*" / "status.json")):
        with open(status_path, encoding="utf-8") as f:
            data = _normalize_entire_status(json.load(f))
        script_id = data.get("script_id")
        if not script_id:
            continue
        status_map[script_id] = data.get("status", "")
    return status_map


def sync_planning(status_map: Dict[str, str]) -> None:
    if not PLANNING_CSV.exists():
        return
    with open(PLANNING_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
    for row in rows:
        sid = row.get("動画ID")
        if sid and sid in status_map:
            row["進捗"] = status_map[sid]
    with open(PLANNING_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def sync_channels(status_map: Dict[str, str], ch_filter: str=None) -> None:
    targets: Iterable[Path]
    if ch_filter:
        targets = [CHANNELS_DIR / f"{ch_filter}.csv"]
    else:
        targets = CHANNELS_DIR.glob("CH*.csv")
    for csv_path in targets:
        if not csv_path.exists():
            continue
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = reader.fieldnames or []
        for row in rows:
            sid = row.get("動画ID")
            if sid and sid in status_map and "進捗" in row:
                row["進捗"] = status_map[sid]
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sync planning/channels progress columns from status.json (SoT)."
    )
    parser.add_argument("--channel", help="channel code (e.g., CH06)", default=None)
    args = parser.parse_args()
    status_map = load_status_map(args.channel)
    # planning master is deprecated; skip
    sync_channels(status_map, args.channel)


if __name__ == "__main__":
    main()
