#!/usr/bin/env python3
"""
Purge rebuildable chunk WAVs under workspaces/audio/final/**/chunks safely.

Goal:
- Keep final SoT artifacts (wav/srt/log.json/etc.)
- Delete only the `chunks/` directory once final WAV exists.
- Skip very recent chunk dirs to avoid interfering with an in-progress synthesis.

Usage:
  python3 scripts/purge_audio_final_chunks.py --dry-run
  python3 scripts/purge_audio_final_chunks.py --run

Options:
  --channel CH02        (repeatable)
  --video 014           (repeatable; requires --channel)
  --keep-recent-minutes 60
"""

from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_common.paths import audio_artifacts_root  # noqa: E402


@dataclass(frozen=True)
class Candidate:
    channel: str
    video: str
    chunks_dir: Path
    size_bytes: int
    mtime: datetime


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _mtime_utc(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, timezone.utc)


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except Exception:
            continue
    return total


def _fmt_bytes(num: int) -> str:
    n = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


def _iter_channels(root: Path, channels: Optional[list[str]]) -> Iterable[Path]:
    if channels:
        for ch in channels:
            p = root / ch.upper()
            if p.is_dir():
                yield p
        return
    for p in sorted(root.glob("CH*")):
        if p.is_dir():
            yield p


def _iter_videos(channel_dir: Path, videos: Optional[list[str]]) -> Iterable[Path]:
    if videos:
        for v in videos:
            p = channel_dir / str(v).zfill(3)
            if p.is_dir():
                yield p
        return
    for p in sorted(channel_dir.iterdir()):
        if p.is_dir() and p.name.isdigit():
            yield p


def collect_candidates(
    *,
    channels: Optional[list[str]],
    videos: Optional[list[str]],
    keep_recent_minutes: int,
) -> list[Candidate]:
    root = audio_artifacts_root() / "final"
    cutoff = _now_utc() - timedelta(minutes=keep_recent_minutes)
    out: list[Candidate] = []

    if not root.exists():
        return out

    for ch_dir in _iter_channels(root, channels):
        ch = ch_dir.name.upper()
        for v_dir in _iter_videos(ch_dir, videos):
            v = str(v_dir.name).zfill(3)
            final_wav = v_dir / f"{ch}-{v}.wav"
            if not final_wav.exists():
                continue

            chunks_dir = v_dir / "chunks"
            if not chunks_dir.is_dir():
                continue

            mtime = _mtime_utc(chunks_dir)
            if mtime >= cutoff:
                continue

            size = _dir_size_bytes(chunks_dir)
            out.append(Candidate(channel=ch, video=v, chunks_dir=chunks_dir, size_bytes=size, mtime=mtime))

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print what would be deleted (default).")
    ap.add_argument("--run", action="store_true", help="Actually delete.")
    ap.add_argument("--channel", action="append", help="Target channel (repeatable). e.g. CH02")
    ap.add_argument("--video", action="append", help="Target video number (repeatable). Requires --channel.")
    ap.add_argument("--keep-recent-minutes", type=int, default=60, help="Skip chunks modified within this window.")
    args = ap.parse_args()

    if args.video and not args.channel:
        ap.error("--video requires --channel (to avoid accidental broad deletes)")

    do_run = bool(args.run)
    dry_run = bool(args.dry_run) or not do_run

    candidates = collect_candidates(
        channels=args.channel,
        videos=args.video,
        keep_recent_minutes=args.keep_recent_minutes,
    )

    total = sum(c.size_bytes for c in candidates)
    print(f"[purge_audio_final_chunks] candidates={len(candidates)} total={_fmt_bytes(total)} dry_run={dry_run}")
    if not candidates:
        return 0

    for c in sorted(candidates, key=lambda x: x.size_bytes, reverse=True)[:20]:
        age_h = (_now_utc() - c.mtime).total_seconds() / 3600.0
        print(f"  - {c.channel}-{c.video} size={_fmt_bytes(c.size_bytes)} age={age_h:.1f}h path={c.chunks_dir}")

    if dry_run:
        print("[purge_audio_final_chunks] dry-run only; pass --run to delete")
        return 0

    deleted = 0
    failed = 0
    for c in candidates:
        try:
            shutil.rmtree(c.chunks_dir)
            deleted += 1
        except Exception as exc:
            failed += 1
            print(f"[purge_audio_final_chunks] FAILED delete {c.chunks_dir}: {exc}", file=sys.stderr)

    print(f"[purge_audio_final_chunks] deleted={deleted} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

