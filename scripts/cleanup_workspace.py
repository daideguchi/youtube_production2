#!/usr/bin/env python3
"""
cleanup_workspace — Unified cleanup for rebuildable artifacts (safe by default).

This command is an orchestrator around existing, battle-tested cleanup scripts.
It is designed to keep SoT / final artifacts intact while removing "残骸" that
adds noise and disk usage.

Currently covered:
- audio domain:
  - workspaces/scripts/**/audio_prep/chunks/            (rebuildable chunk WAVs)
  - workspaces/scripts/**/audio_prep/{CH}-{NNN}.wav/srt (duplicate binaries)
  - workspaces/audio/final/**/chunks/                   (rebuildable chunk WAVs)
- logs domain (L3 only):
  - workspaces/logs/** (excluding L1 JSONL/DB + agent queues)
- scripts domain:
  - workspaces/scripts/**/audio_prep + per-video logs older than keep-days

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
    print(f"[cleanup_workspace] $ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    return int(proc.returncode)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audio", action="store_true", help="Cleanup audio artifacts (default when no domain is specified).")
    ap.add_argument("--logs", action="store_true", help="Cleanup L3 logs under logs_root().")
    ap.add_argument("--scripts", action="store_true", help="Cleanup workspaces/scripts intermediates/logs (L3).")
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
    ap.add_argument("--logs-keep-days", type=int, default=30, help="Keep logs newer than this many days (default: 30).")
    ap.add_argument("--scripts-keep-days", type=int, default=14, help="Keep script intermediates newer than this many days (default: 14).")
    ap.add_argument("--include-llm-api-cache", action="store_true", help="Also prune logs/llm_api_cache (default: keep).")
    args = ap.parse_args()

    do_run = bool(args.run)
    dry_run = bool(args.dry_run) or not do_run

    domains: set[str] = set()
    if args.logs:
        domains.add("logs")
    if args.scripts:
        domains.add("scripts")
    if args.audio or (not args.logs and not args.scripts):
        domains.add("audio")

    if "audio" in domains:
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

    audio_scripts = [
        REPO_ROOT / "scripts" / "cleanup_audio_prep.py",
        REPO_ROOT / "scripts" / "purge_audio_prep_binaries.py",
        REPO_ROOT / "scripts" / "purge_audio_final_chunks.py",
    ]

    print(f"[cleanup_workspace] domains={sorted(domains)} mode={'run' if (do_run and not dry_run) else 'dry-run'}", flush=True)
    if "audio" in domains:
        print(f"[cleanup_workspace] audio.keep_recent_minutes={keep_recent}", flush=True)
        if args.all:
            print("[cleanup_workspace] audio.scope=ALL channels/videos", flush=True)
        else:
            print(f"[cleanup_workspace] audio.scope=channels={channels} videos={videos}", flush=True)
    if "logs" in domains:
        print(f"[cleanup_workspace] logs.keep_days={int(args.logs_keep_days)} include_llm_api_cache={bool(args.include_llm_api_cache)}", flush=True)
    if "scripts" in domains:
        print(f"[cleanup_workspace] scripts.keep_days={int(args.scripts_keep_days)}", flush=True)

    worst = 0

    if "logs" in domains:
        script_path = REPO_ROOT / "scripts" / "ops" / "cleanup_logs.py"
        if not script_path.exists():
            print(f"[cleanup_workspace] WARN missing script: {script_path}")
            worst = max(worst, 2)
        else:
            args_logs: list[str] = []
            if do_run and not dry_run:
                args_logs.append("--run")
            args_logs += ["--keep-days", str(int(args.logs_keep_days))]
            if args.include_llm_api_cache:
                args_logs.append("--include-llm-api-cache")
            rc = _run_script(script_path, args_logs)
            worst = max(worst, rc)

    if "scripts" in domains:
        script_path = REPO_ROOT / "scripts" / "cleanup_data.py"
        if not script_path.exists():
            print(f"[cleanup_workspace] WARN missing script: {script_path}")
            worst = max(worst, 2)
        else:
            args_scripts: list[str] = []
            if do_run and not dry_run:
                args_scripts.append("--run")
            args_scripts += ["--keep-days", str(int(args.scripts_keep_days))]
            rc = _run_script(script_path, args_scripts)
            worst = max(worst, rc)

    if "audio" in domains:
        base_args_audio: list[str] = ["--run"] if do_run and not dry_run else ["--dry-run"]
        base_args_audio += ["--keep-recent-minutes", keep_recent]
        if channels:
            for ch in channels:
                base_args_audio += ["--channel", ch]
        if videos:
            for v in videos:
                base_args_audio += ["--video", v]

        for script_path in audio_scripts:
            if not script_path.exists():
                print(f"[cleanup_workspace] WARN missing script: {script_path}")
                worst = max(worst, 2)
                continue
            rc = _run_script(script_path, base_args_audio)
            worst = max(worst, rc)

    return 0 if worst == 0 else worst


if __name__ == "__main__":
    raise SystemExit(main())
