#!/usr/bin/env python3
"""Force asset-driven progress sync.

This script treats the on-disk SoT (status.json +成果物) as the single truth and
invokes `progress_manager.py repair-status --auto-complete` for every planning
row (or filtered subset). Use this whenever planning.csv and UI state need to be
hard-aligned with the local assets.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commentary_01_srtfile_v2.core.tools import planning_store  # type: ignore

PROGRESS_MANAGER = ROOT / "commentary_01_srtfile_v2" / "core" / "tools" / "progress_manager.py"
LOG_ROOT = ROOT / "logs" / "regression"


def _normalize_channel(code: str) -> str:
    return (code or "").strip().upper()


def _load_rows(channel: Optional[str], exclude: Optional[List[str]] = None) -> Iterable[dict]:
    planning_store.refresh(force=True)
    excludes = {_normalize_channel(code) for code in (exclude or []) if code}
    if channel:
        channels = [_normalize_channel(channel)]
    else:
        channels = list(planning_store.list_channels())
    for ch in channels:
        if excludes and ch in excludes:
            continue
        for row in planning_store.get_rows(ch, force_refresh=False):
            yield row.raw


def _run_repair(channel: str, video: str, dry_run: bool) -> subprocess.CompletedProcess:
    cmd: List[str] = [
        "python3",
        str(PROGRESS_MANAGER),
        "repair-status",
        "--channel-code",
        channel,
        "--video-number",
        video,
    ]
    if dry_run:
        cmd.append("--dry-run")
    cmd.append("--auto-complete")
    return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Force asset state onto planning/status via repair-status")
    parser.add_argument("--channel-code", help="対象チャンネル (例: CH06)。省略時は全件")
    parser.add_argument("--exclude-channel", action="append", help="除外するチャンネルコード（繰り返し可）")
    parser.add_argument("--video-number", help="特定動画番号 (例: 016)。チャンネル指定が必要")
    parser.add_argument("--limit", type=int, help="上限件数")
    parser.add_argument("--dry-run", action="store_true", help="repair-status を dry-run で実行")
    args = parser.parse_args()

    rows = list(_load_rows(args.channel_code, exclude=args.exclude_channel))
    if args.video_number:
        rows = [row for row in rows if row.get("動画番号") == args.video_number]
    if args.limit:
        rows = rows[: args.limit]

    if not rows:
        print("該当する行がありません。")
        return 0

    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_ROOT / f"asset_sync_{timestamp}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    successes = 0
    with log_path.open("w", encoding="utf-8") as log_file:
        log_file.write(f"Asset sync started at {timestamp}\n")
        log_file.write(f"Rows: {len(rows)} dry_run={args.dry_run}\n\n")
        for idx, row in enumerate(rows, 1):
            channel = (row.get("チャンネル") or row.get("channel") or "").strip()
            video = (row.get("動画番号") or row.get("video_number") or "").strip()
            script_id = row.get("台本番号") or row.get("script_id") or ""
            if not channel or not video:
                log_file.write(f"[{idx}/{len(rows)}] skip: channel/video missing (row={script_id})\n")
                continue
            proc = _run_repair(channel, video, args.dry_run)
            if proc.returncode == 0:
                successes += 1
                status = "OK"
            else:
                status = f"FAIL ({proc.returncode})"
            log_file.write(f"[{idx}/{len(rows)}] {channel}-{video} {status}\n")
            if proc.stdout:
                log_file.write(proc.stdout + "\n")
            if proc.stderr:
                log_file.write(proc.stderr + "\n")
            log_file.write("-" * 40 + "\n")

    print(f"Completed {len(rows)} rows (success={successes}). Log: {log_path}")
    return 0 if successes == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
