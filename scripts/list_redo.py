#!/usr/bin/env python3
"""
List redo targets (script/audio) based on status.json metadata.
Defaults to listing all channels, where redo_script or redo_audio is True (or missing).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "script_pipeline" / "data"


def load_status(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    meta = payload.get("metadata") or {}
    redo_script = meta.get("redo_script")
    redo_audio = meta.get("redo_audio")
    # default to True when missing
    if redo_script is None:
        redo_script = True
    if redo_audio is None:
        redo_audio = True
    return {
        "channel": payload.get("channel"),
        "video": payload.get("video") or path.parent.name,
        "title": meta.get("sheet_title") or meta.get("title"),
        "status": payload.get("status"),
        "redo_script": bool(redo_script),
        "redo_audio": bool(redo_audio),
        "redo_note": meta.get("redo_note"),
    }


def iter_statuses(channel_filter: str | None):
    for ch_dir in DATA_ROOT.iterdir():
        if not ch_dir.is_dir():
            continue
        ch_code = ch_dir.name.upper()
        if channel_filter and ch_code != channel_filter:
            continue
        for vid_dir in ch_dir.iterdir():
            if not vid_dir.is_dir():
                continue
            st_path = vid_dir / "status.json"
            if not st_path.exists():
                continue
            try:
                yield load_status(st_path)
            except Exception:
                continue


def main() -> None:
    parser = argparse.ArgumentParser(description="List redo targets (script/audio) from status.json")
    parser.add_argument("--channel", help="Channel code (e.g., CH02)")
    parser.add_argument("--type", choices=["script", "audio", "all"], default="all", help="Filter by redo type")
    args = parser.parse_args()

    channel_filter = args.channel.upper() if args.channel else None
    type_filter = args.type

    rows = []
    for item in iter_statuses(channel_filter):
        if type_filter == "script" and not item["redo_script"]:
            continue
        if type_filter == "audio" and not item["redo_audio"]:
            continue
        if type_filter == "all" and not (item["redo_script"] or item["redo_audio"]):
            continue
        rows.append(item)

    rows = sorted(rows, key=lambda x: (x["channel"], x["video"]))
    if not rows:
        print("no redo targets")
        return

    header = ["channel", "video", "status", "redo_script", "redo_audio", "title", "redo_note"]
    print("\t".join(header))
    for r in rows:
        print(
            "\t".join(
                [
                    str(r.get("channel") or ""),
                    str(r.get("video") or ""),
                    str(r.get("status") or ""),
                    "1" if r.get("redo_script") else "0",
                    "1" if r.get("redo_audio") else "0",
                    (r.get("title") or "").replace("\t", " "),
                    (r.get("redo_note") or "").replace("\t", " "),
                ]
            )
        )


if __name__ == "__main__":
    main()
