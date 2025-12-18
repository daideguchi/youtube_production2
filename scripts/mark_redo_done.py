#!/usr/bin/env python3
"""
Mark redo flags as done (set false) for specified channel/video pairs.
Usage:
  python3 scripts/mark_redo_done.py --channel CH02 --videos 019 020
  python3 scripts/mark_redo_done.py --channel CH02 --all --type audio
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, Any, Iterable

from _bootstrap import bootstrap

bootstrap()

from factory_common.paths import script_data_root
DATA_ROOT = script_data_root()


def load_status(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_status(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def list_video_dirs(channel: str) -> Iterable[Path]:
    ch_dir = DATA_ROOT / channel
    if not ch_dir.exists():
        return []
    return [p for p in ch_dir.iterdir() if p.is_dir()]


def mark(channel: str, video: str, type_filter: str) -> bool:
    st_path = DATA_ROOT / channel / video / "status.json"
    if not st_path.exists():
        return False
    payload = load_status(st_path)
    meta = payload.get("metadata") or {}
    if type_filter in ("script", "all"):
        meta["redo_script"] = False
    if type_filter in ("audio", "all"):
        meta["redo_audio"] = False
    # auto memo
    meta.setdefault("redo_note", "")
    if type_filter in ("script", "all") and "redo_note" in meta:
        meta["redo_note"] = (meta.get("redo_note") or "") + " [auto-cleared script]"
    if type_filter in ("audio", "all") and "redo_note" in meta:
        meta["redo_note"] = (meta.get("redo_note") or "") + " [auto-cleared audio]"
    payload["metadata"] = meta
    save_status(st_path, payload)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Mark redo flags as done (set false).")
    parser.add_argument("--channel", required=True, help="Channel code, e.g., CH02")
    parser.add_argument("--videos", nargs="*", help="Video numbers (e.g., 019 020)")
    parser.add_argument("--all", action="store_true", help="Apply to all videos in channel")
    parser.add_argument("--type", choices=["script", "audio", "all"], default="all", help="Which flags to clear")
    args = parser.parse_args()

    ch = args.channel.upper()
    target_videos = []
    if args.all:
        target_videos = [p.name for p in list_video_dirs(ch)]
    elif args.videos:
        target_videos = [v.zfill(3) for v in args.videos]
    else:
        parser.error("Specify --all or --videos")

    ok = 0
    for v in target_videos:
        if mark(ch, v, args.type):
            ok += 1
    print(f"cleared redo ({args.type}) for {ok} items in {ch}")


if __name__ == "__main__":
    main()
