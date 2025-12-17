#!/usr/bin/env python3
"""
cleanup_workspace — Unified cleanup for rebuildable artifacts (safe by default).

This command is an orchestrator around existing, battle-tested cleanup scripts.
It is designed to keep SoT / final artifacts intact while removing "残骸" that
adds noise and disk usage.

Currently covered (audio domain):
- workspaces/scripts/**/audio_prep/chunks/            (rebuildable chunk WAVs)
- workspaces/scripts/**/audio_prep/{CH}-{NNN}.wav/srt (duplicate binaries)
- workspaces/audio/final/**/chunks/                   (rebuildable chunk WAVs)

Default is dry-run. Use --run to actually delete.
For --all + --run, --yes is required.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON_BIN = sys.executable or "python3"


def _normalize_channel(raw: str) -> str:
    ch = (raw or "").strip().upper()
    if not ch:
        raise ValueError("channel is empty")
    if not ch.startswith("CH"):
        raise ValueError(f"invalid channel: {raw}")
    return ch


def _normalize_video(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        raise ValueError("video is empty")
    if not s.isdigit():
        raise ValueError(f"invalid video: {raw}")
    return s.zfill(3)


def _run_script(script_path: Path, args: list[str]) -> int:
    cmd = [PYTHON_BIN, str(script_path), *args]
    print(f"[cleanup_workspace] $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    return int(proc.returncode)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print actions (default).")
    ap.add_argument("--run", action="store_true", help="Actually delete.")
    ap.add_argument(
        "--all",
        action="store_true",
        help="Scan all channels/videos (dangerous with --run; requires --yes).",
    )
    ap.add_argument(
        "--yes",
        action="store_true",
        help="Required when using --run with --all.",
    )
    ap.add_argument("--channel", action="append", help="Target channel (repeatable). e.g. CH02")
    ap.add_argument("--video", action="append", help="Target video (repeatable). Requires --channel unless --all.")
    ap.add_argument("--keep-recent-minutes", type=int, default=360, help="Skip recently modified artifacts.")
    args = ap.parse_args()

    do_run = bool(args.run)
    dry_run = bool(args.dry_run) or not do_run

    if args.video and not args.channel and not args.all:
        ap.error("--video requires --channel (or use --all)")
    if not args.all and not args.channel:
        ap.error("provide --channel (repeatable) or use --all")
    if do_run and args.all and not args.yes:
        ap.error("--run --all requires --yes")

    channels: list[str] | None = None
    videos: list[str] | None = None
    if args.channel:
        channels = []
        for ch in args.channel:
            channels.append(_normalize_channel(ch))
    if args.video:
        videos = []
        for v in args.video:
            videos.append(_normalize_video(v))

    keep_recent = str(int(args.keep_recent_minutes))
    base_args: list[str] = ["--run"] if do_run and not dry_run else ["--dry-run"]
    base_args += ["--keep-recent-minutes", keep_recent]
    if channels:
        for ch in channels:
            base_args += ["--channel", ch]
    if videos:
        for v in videos:
            base_args += ["--video", v]

    scripts = [
        REPO_ROOT / "scripts" / "cleanup_audio_prep.py",
        REPO_ROOT / "scripts" / "purge_audio_prep_binaries.py",
        REPO_ROOT / "scripts" / "purge_audio_final_chunks.py",
    ]

    print(f"[cleanup_workspace] mode={'run' if (do_run and not dry_run) else 'dry-run'} keep_recent_minutes={keep_recent}")
    if args.all:
        print("[cleanup_workspace] scope=ALL channels/videos")
    else:
        print(f"[cleanup_workspace] scope=channels={channels} videos={videos}")

    worst = 0
    for script_path in scripts:
        if not script_path.exists():
            print(f"[cleanup_workspace] WARN missing script: {script_path}")
            worst = max(worst, 2)
            continue
        rc = _run_script(script_path, base_args)
        worst = max(worst, rc)

    return 0 if worst == 0 else worst


if __name__ == "__main__":
    raise SystemExit(main())

