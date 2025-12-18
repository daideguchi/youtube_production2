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
- video domain:
  - workspaces/video/runs/{run_id}/                     (archive older, non-selected runs)
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

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)
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
    ap.add_argument("--video-runs", action="store_true", help="Archive older video run dirs under workspaces/video/runs.")
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
    ap.add_argument("--video-keep-last-runs", type=int, default=2, help="Keep at least N run dirs per episode (video domain).")
    ap.add_argument("--video-archive-unscoped", action="store_true", help="Also archive unscoped run dirs that look like trash (video domain; requires --all).")
    ap.add_argument(
        "--video-archive-unscoped-legacy",
        action="store_true",
        help="Also archive unscoped legacy run dirs (numeric/api_/jinsei*/CHxx... patterns; video domain; requires --all).",
    )
    ap.add_argument("--video-unscoped-only", action="store_true", help="Only process unscoped dirs (video domain).")
    ap.add_argument("--video-include-hidden-runs", action="store_true", help="Include runs starting with _ or . (video domain).")
    ap.add_argument("--video-exclude-run-glob", action="append", help="Skip run dirs matching these globs (repeatable; video domain).")
    ap.add_argument("--logs-keep-days", type=int, default=30, help="Keep logs newer than this many days (default: 30).")
    ap.add_argument("--scripts-keep-days", type=int, default=14, help="Keep script intermediates newer than this many days (default: 14).")
    ap.add_argument("--include-llm-api-cache", action="store_true", help="Also prune logs/llm_api_cache (default: keep).")
    args = ap.parse_args()

    do_run = bool(args.run)
    dry_run = bool(args.dry_run) or not do_run

    domains: set[str] = set()
    if args.video_runs:
        domains.add("video_runs")
    if args.logs:
        domains.add("logs")
    if args.scripts:
        domains.add("scripts")
    if args.audio or (not args.video_runs and not args.logs and not args.scripts):
        domains.add("audio")

    if args.video and not args.channel and not args.all:
        ap.error("--video requires --channel (or use --all)")
    if "audio" in domains and not args.all and not args.channel:
        ap.error("provide --channel (repeatable) or use --all")
    if "video_runs" in domains and not args.all and not args.channel and not args.video_unscoped_only:
        ap.error("provide --channel (repeatable) or use --all")
    if do_run and not args.yes and ("audio" in domains or "video_runs" in domains):
        if args.all:
            ap.error("--run --all requires --yes")
        if "video_runs" in domains and args.video_unscoped_only:
            ap.error("--run --video-unscoped-only requires --yes")

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
    if "video_runs" in domains:
        print(
            f"[cleanup_workspace] video.keep_last_runs={int(args.video_keep_last_runs)} "
            f"archive_unscoped={bool(args.video_archive_unscoped)} "
            f"archive_unscoped_legacy={bool(args.video_archive_unscoped_legacy)} "
            f"unscoped_only={bool(args.video_unscoped_only)} "
            f"include_hidden={bool(args.video_include_hidden_runs)}",
            flush=True,
        )

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

    if "video_runs" in domains:
        script_path = REPO_ROOT / "scripts" / "ops" / "cleanup_video_runs.py"
        if not script_path.exists():
            print(f"[cleanup_workspace] WARN missing script: {script_path}")
            worst = max(worst, 2)
        else:
            args_video: list[str] = []
            if do_run and not dry_run:
                args_video.append("--run")
            if args.all:
                args_video.append("--all")
            if args.yes:
                args_video.append("--yes")
            args_video += ["--keep-recent-minutes", keep_recent]
            args_video += ["--keep-last-runs", str(int(args.video_keep_last_runs))]
            if args.video_archive_unscoped:
                args_video.append("--archive-unscoped")
            if args.video_archive_unscoped_legacy:
                args_video.append("--archive-unscoped-legacy")
            if args.video_unscoped_only:
                args_video.append("--unscoped-only")
            if args.video_include_hidden_runs:
                args_video.append("--include-hidden-runs")
            if args.video_exclude_run_glob:
                for glob in args.video_exclude_run_glob:
                    args_video += ["--exclude-run-glob", str(glob)]
            if channels:
                for ch in channels:
                    args_video += ["--channel", ch]
            if videos:
                for v in videos:
                    args_video += ["--video", v]
            rc = _run_script(script_path, args_video)
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
