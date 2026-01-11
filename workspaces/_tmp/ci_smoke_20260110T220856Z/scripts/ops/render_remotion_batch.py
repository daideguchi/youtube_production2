#!/usr/bin/env python3
"""
render_remotion_batch
Batch render Remotion outputs from run_dir while keeping disk usage low.

Defaults:
  - Channel: CH08
  - Videos: 1-29 (skip 002)
  - Output: workspaces/video/runs/<run>/remotion/output/final.mp4
  - Cleanup: remove chunks/_auto/_bgm after each successful render
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from _bootstrap import bootstrap


bootstrap(load_env=False)

from factory_common import paths as repo_paths
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock


REPO_ROOT = repo_paths.repo_root()


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except Exception:
        return str(path)


def _ensure_under_repo(path: Path) -> None:
    base = (path if path.is_absolute() else (REPO_ROOT / path)).absolute()
    _ = base.relative_to(REPO_ROOT)


def _parse_video_list(raw_items: list[str]) -> list[str]:
    nums: set[int] = set()
    for raw in raw_items:
        token = (raw or "").strip()
        if not token:
            continue
        parts = [p.strip() for p in token.split(",") if p.strip()]
        for part in parts:
            m_range = re.fullmatch(r"(\d{1,3})\s*-\s*(\d{1,3})", part)
            if m_range:
                a = int(m_range.group(1))
                b = int(m_range.group(2))
                lo, hi = (a, b) if a <= b else (b, a)
                for n in range(lo, hi + 1):
                    nums.add(int(n))
                continue
            m_one = re.fullmatch(r"\d{1,3}", part)
            if m_one:
                nums.add(int(part))
                continue
            raise ValueError(f"Invalid token: {part!r}")
    return [str(n).zfill(3) for n in sorted(nums)]


def _parse_skip_list(raw_items: list[str]) -> set[str]:
    return set(_parse_video_list(raw_items or []))


def _is_nonempty_file(path: Path, *, min_bytes: int = 1024 * 1024) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= min_bytes
    except Exception:
        return False


def _delete_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _remotion_app_root() -> Path:
    return REPO_ROOT / "apps" / "remotion"


@dataclass(frozen=True)
class RenderResult:
    video: str
    run_id: str
    status: str  # ok | skipped | failed | locked
    out_mp4: Optional[str] = None
    note: Optional[str] = None
    returncode: Optional[int] = None


def _run_node(cmd: list[str], *, cwd: Path, dry_run: bool) -> int:
    print(f"[render_remotion_batch] $ {' '.join(cmd)}", flush=True)
    if dry_run:
        return 0
    proc = subprocess.run(cmd, cwd=str(cwd), check=False)
    return int(proc.returncode)


def _cleanup_after_success(*, run_dir: Path, final_out: Path, clean: bool, locks: list, dry_run: bool) -> list[str]:
    if not clean:
        return []
    notes: list[str] = []

    out_dir = final_out.parent
    if out_dir.exists():
        for child in sorted(out_dir.iterdir()):
            if child.name == final_out.name:
                continue
            if locks and find_blocking_lock(child, locks):
                notes.append(f"skip_locked:{_rel(child)}")
                continue
            try:
                _ensure_under_repo(child)
                if dry_run:
                    print(f"[render_remotion_batch] (dry-run) delete {child}", flush=True)
                else:
                    _delete_path(child)
            except Exception as e:
                notes.append(f"delete_failed:{_rel(child)}:{e}")

    app = _remotion_app_root()
    basename = run_dir.name
    candidates = [
        app / "public" / "_auto" / basename,
        app / "public" / "_bgm" / basename,
        app / "out" / f"chunks_{basename}",
    ]
    for p in candidates:
        if not p.exists():
            continue
        if locks and find_blocking_lock(p, locks):
            notes.append(f"skip_locked:{_rel(p)}")
            continue
        try:
            _ensure_under_repo(p)
            if dry_run:
                print(f"[render_remotion_batch] (dry-run) delete {p}", flush=True)
            else:
                _delete_path(p)
        except Exception as e:
            notes.append(f"delete_failed:{_rel(p)}:{e}")

    return notes


def render_one(
    *,
    channel: str,
    video: str,
    run_suffix: str,
    chunk_sec: int,
    resume_chunks: bool,
    bgm_volume: float,
    bgm_fade: float,
    force: bool,
    clean: bool,
    dry_run: bool,
    locks: list,
) -> RenderResult:
    run_id = f"{channel}-{video}{run_suffix}"
    run_dir = repo_paths.video_run_dir(run_id)
    if not run_dir.exists():
        return RenderResult(video=video, run_id=run_id, status="failed", note=f"run_dir_missing:{_rel(run_dir)}")

    out_dir = run_dir / "remotion" / "output"
    final_out = out_dir / "final.mp4"

    if locks and find_blocking_lock(out_dir, locks):
        lock = find_blocking_lock(out_dir, locks)
        note = f"locked_by:{lock.created_by}:{lock.lock_id}" if lock else "locked"
        return RenderResult(video=video, run_id=run_id, status="locked", note=note)

    if not force and _is_nonempty_file(final_out):
        notes = _cleanup_after_success(run_dir=run_dir, final_out=final_out, clean=clean, locks=locks, dry_run=dry_run)
        return RenderResult(video=video, run_id=run_id, status="skipped", out_mp4=_rel(final_out), note=";".join(notes) or None)

    audio_dir = repo_paths.audio_final_dir(channel, video)
    wav = audio_dir / f"{channel}-{video}.wav"
    srt = audio_dir / f"{channel}-{video}.srt"
    if not wav.exists():
        return RenderResult(video=video, run_id=run_id, status="failed", note=f"audio_missing:{_rel(wav)}")
    if not srt.exists():
        return RenderResult(video=video, run_id=run_id, status="failed", note=f"srt_missing:{_rel(srt)}")

    out_dir.mkdir(parents=True, exist_ok=True)

    cmd: list[str] = [
        "node",
        str((REPO_ROOT / "apps" / "remotion" / "scripts" / "render.js").resolve()),
        "--run",
        _rel(run_dir),
        "--channel",
        channel,
        "--bgm",
        str(wav.resolve()),
        "--srt",
        str(srt.resolve()),
        "--out",
        _rel(final_out),
        "--bgm-volume",
        str(bgm_volume),
        "--bgm-fade",
        str(bgm_fade),
    ]
    if chunk_sec > 0:
        cmd += ["--chunk-sec", str(chunk_sec)]
    if resume_chunks:
        cmd += ["--resume-chunks"]

    rc = _run_node(cmd, cwd=REPO_ROOT, dry_run=dry_run)
    if rc != 0:
        return RenderResult(video=video, run_id=run_id, status="failed", out_mp4=_rel(final_out), returncode=rc, note="render_failed")

    if not _is_nonempty_file(final_out, min_bytes=1024 * 256):
        return RenderResult(video=video, run_id=run_id, status="failed", out_mp4=_rel(final_out), returncode=rc, note="output_missing")

    notes = _cleanup_after_success(run_dir=run_dir, final_out=final_out, clean=clean, locks=locks, dry_run=dry_run)
    return RenderResult(video=video, run_id=run_id, status="ok", out_mp4=_rel(final_out), note=";".join(notes) or None, returncode=rc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="CH08")
    ap.add_argument("--videos", action="append", default=[], help="e.g. 1-29, 001-029, 1,3,4 (repeatable)")
    ap.add_argument("--skip", action="append", default=[], help="videos to skip (default includes: 2)")
    ap.add_argument("--run-suffix", default="_capcut_v1")
    ap.add_argument("--chunk-sec", type=int, default=30)
    ap.add_argument("--no-resume-chunks", action="store_true")
    ap.add_argument("--bgm-volume", type=float, default=0.32)
    ap.add_argument("--bgm-fade", type=float, default=1.5)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--no-clean", action="store_true")
    ap.add_argument("--stop-on-error", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--ignore-locks", action="store_true")
    args = ap.parse_args()

    if not args.run:
        args.dry_run = True

    channel = str(args.channel).strip().upper()
    videos_spec = list(args.videos or []) or ["1-29"]
    skip_spec = list(args.skip or []) + ["2"]
    videos = _parse_video_list(videos_spec)
    skip = _parse_skip_list(skip_spec)
    targets = [v for v in videos if v not in skip]

    locks = [] if args.ignore_locks else default_active_locks_for_mutation()
    results: list[RenderResult] = []

    for v in targets:
        r = render_one(
            channel=channel,
            video=v,
            run_suffix=str(args.run_suffix),
            chunk_sec=int(args.chunk_sec),
            resume_chunks=not bool(args.no_resume_chunks),
            bgm_volume=float(args.bgm_volume),
            bgm_fade=float(args.bgm_fade),
            force=bool(args.force),
            clean=not bool(args.no_clean),
            dry_run=bool(args.dry_run),
            locks=locks,
        )
        results.append(r)
        print(f"[render_remotion_batch] {r.run_id}: {r.status}", flush=True)
        if r.status == "failed" and args.stop_on_error:
            break

    report_dir = repo_paths.logs_root() / "regression" / "remotion_batch"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"render_remotion_batch__{channel}__{_utc_now_compact()}.json"
    payload = {
        "schema": "ytm.remotion_batch_render_report.v1",
        "channel": channel,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "args": {
            "videos": list(videos_spec),
            "skip": list(skip_spec),
            "run_suffix": args.run_suffix,
            "chunk_sec": args.chunk_sec,
            "resume_chunks": not bool(args.no_resume_chunks),
            "bgm_volume": args.bgm_volume,
            "bgm_fade": args.bgm_fade,
            "force": bool(args.force),
            "clean": not bool(args.no_clean),
            "dry_run": bool(args.dry_run),
            "run": bool(args.run),
            "ignore_locks": bool(args.ignore_locks),
        },
        "results": [r.__dict__ for r in results],
    }
    if args.dry_run:
        print(f"[render_remotion_batch] (dry-run) report -> {report_path}", flush=True)
    else:
        report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[render_remotion_batch] report -> {report_path}", flush=True)

    return 1 if any(r.status == "failed" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
