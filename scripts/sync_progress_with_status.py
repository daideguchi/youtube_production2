#!/usr/bin/env python3
"""Align planning progress column with status.json stage states."""
from __future__ import annotations

import json
from pathlib import Path

from commentary_01_srtfile_v2.core.tools import planning_store  # type: ignore
from commentary_01_srtfile_v2.core.tools.kanban_validator import STAGE_DEPENDENCIES  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = ROOT / "commentary_01_srtfile_v2" / "data"
STAGE_ORDER = list(STAGE_DEPENDENCIES.keys()) + ["image_generation"]
EMOJI_MAP = {
    "completed": "✅",
    "pending": "🕗",
    "processing": "🔄",
    "failed": "❌",
    "rerun_requested": "♻️",
    "rerun_in_progress": "🔁",
    "rerun_completed": "✅",
}


def determine_stage_status(stage_entries: dict) -> tuple[str, str]:
    for stage in STAGE_ORDER:
        entry = stage_entries.get(stage, {})
        status = entry.get("status") or "pending"
        if status != "completed":
            return stage, status
    # all completed – show final stage as completed
    last_stage = STAGE_ORDER[-1]
    last_status = stage_entries.get(last_stage, {}).get("status", "completed")
    return last_stage, last_status or "completed"


def main() -> int:
    planning_store.refresh(force=True)
    updates = 0
    missing = 0
    for channel in planning_store.list_channels():
        for row in planning_store.get_rows(channel, force_refresh=False):
            script_id = row.script_id
            video_number = row.video_number
            status_path = DATA_ROOT / channel / video_number / "status.json"
            if not status_path.exists():
                missing += 1
                continue
            try:
                data = json.loads(status_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                missing += 1
                continue
            stage_entries = data.get("stages", {})
            stage, state = determine_stage_status(stage_entries)
            emoji = EMOJI_MAP.get(state, "•")
            message = f"{emoji} {stage}: {state}"
            current = (row.raw.get("進捗") or "").strip()
            if current == message:
                continue
            planning_store.update_progress_value(channel, script_id, message)
            updates += 1
    print(f"Updated progress cells: {updates}")
    if missing:
        print(f"Skipped rows with missing/invalid status.json: {missing}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
