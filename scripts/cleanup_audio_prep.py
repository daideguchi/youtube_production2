#!/usr/bin/env python3
"""
Cleanup workspaces/scripts/**/audio_prep/ chunk artifacts safely.

Design goals:
- Never delete final SoT artifacts (workspaces/audio/final/**).
- Delete only rebuildable chunk WAVs under audio_prep/chunks/ when a final WAV exists.
- Skip very recent chunk dirs to avoid interfering with an in-progress synthesis.

Usage:
  python3 scripts/cleanup_audio_prep.py --dry-run
  python3 scripts/cleanup_audio_prep.py --run

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

from factory_common.paths import audio_final_dir, script_data_root  # noqa: E402
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402


@dataclass(frozen=True)
class Candidate:
    channel: str
    video: str
    chunks_dir: Path
    reason: str
    size_bytes: int
    mtime: datetime


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


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _mtime_utc(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, timezone.utc)


def _final_wav_exists(channel: str, video: str) -> bool:
    d = audio_final_dir(channel, video)
    name = f"{channel.upper()}-{str(video).zfill(3)}.wav"
    return (d / name).exists()


def _prep_wav_exists(video_dir: Path, channel: str, video: str) -> bool:
    prep = video_dir / "audio_prep"
    name = f"{channel.upper()}-{str(video).zfill(3)}.wav"
    return (prep / name).exists()


def collect_candidates(
    *,
    channels: Optional[list[str]],
    videos: Optional[list[str]],
    keep_recent_minutes: int,
    ignore_locks: bool,
) -> tuple[list[Candidate], int]:
    root = script_data_root()
    cutoff = _now_utc() - timedelta(minutes=keep_recent_minutes)

    out: list[Candidate] = []
    locks = [] if ignore_locks else default_active_locks_for_mutation()
    skipped_locked = 0
    for ch_dir in _iter_channels(root, channels):
        ch = ch_dir.name.upper()
        for v_dir in _iter_videos(ch_dir, videos):
            v = str(v_dir.name).zfill(3)
            chunks_dir = v_dir / "audio_prep" / "chunks"
            if not chunks_dir.is_dir():
                continue

            mtime = _mtime_utc(chunks_dir)
            if mtime >= cutoff:
                continue

            final_ok = _final_wav_exists(ch, v)
            prep_ok = _prep_wav_exists(v_dir, ch, v)
            if not (final_ok or prep_ok):
                continue

            size = _dir_size_bytes(chunks_dir)
            reason = "final_wav" if final_ok else "prep_wav"
            if locks and find_blocking_lock(chunks_dir, locks):
                skipped_locked += 1
                continue
            out.append(Candidate(channel=ch, video=v, chunks_dir=chunks_dir, reason=reason, size_bytes=size, mtime=mtime))

    return out, skipped_locked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print what would be deleted (default).")
    ap.add_argument("--run", action="store_true", help="Actually delete.")
    ap.add_argument("--channel", action="append", help="Target channel (repeatable). e.g. CH02")
    ap.add_argument("--video", action="append", help="Target video number (repeatable). Requires --channel.")
    ap.add_argument("--keep-recent-minutes", type=int, default=60, help="Skip chunks modified within this window.")
    ap.add_argument(
        "--ignore-locks",
        action="store_true",
        help="Do not respect coordination locks (dangerous; default: respect locks).",
    )
    args = ap.parse_args()

    if args.video and not args.channel:
        ap.error("--video requires --channel (to avoid accidental broad deletes)")

    do_run = bool(args.run)
    dry_run = bool(args.dry_run) or not do_run

    candidates, skipped_locked = collect_candidates(
        channels=args.channel,
        videos=args.video,
        keep_recent_minutes=args.keep_recent_minutes,
        ignore_locks=bool(args.ignore_locks),
    )

    total = sum(c.size_bytes for c in candidates)
    suffix = f" skipped_locked={skipped_locked}" if skipped_locked else ""
    print(f"[cleanup_audio_prep] candidates={len(candidates)} total={_fmt_bytes(total)} dry_run={dry_run}{suffix}")
    if not candidates:
        return 0

    # Show top 20 by size
    for c in sorted(candidates, key=lambda x: x.size_bytes, reverse=True)[:20]:
        age_h = (_now_utc() - c.mtime).total_seconds() / 3600.0
        print(
            f"  - {c.channel}-{c.video} {c.reason} size={_fmt_bytes(c.size_bytes)} age={age_h:.1f}h path={c.chunks_dir}"
        )

    if dry_run:
        print("[cleanup_audio_prep] dry-run only; pass --run to delete")
        return 0

    deleted = 0
    failed = 0
    for c in candidates:
        try:
            shutil.rmtree(c.chunks_dir)
            deleted += 1
        except Exception as exc:
            failed += 1
            print(f"[cleanup_audio_prep] FAILED delete {c.chunks_dir}: {exc}", file=sys.stderr)

    print(f"[cleanup_audio_prep] deleted={deleted} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
