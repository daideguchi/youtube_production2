#!/usr/bin/env python3
"""
Purge duplicate WAV/SRT binaries under workspaces/scripts/**/audio_prep/
when workspaces/audio/final/** already contains the final artifacts.

Goal:
- Keep text/json inputs in audio_prep (B-text, overrides, logs, etc.)
- Remove only large duplicate binaries:
    audio_prep/{CH}-{NNN}.wav
    audio_prep/{CH}-{NNN}.srt

Safety:
- Never deletes final artifacts.
- Skips recently modified files (default: 6 hours) to avoid interfering with in-progress runs.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_common.paths import audio_final_dir, script_data_root  # noqa: E402


@dataclass(frozen=True)
class PurgeItem:
    channel: str
    video: str
    prep_wav: Path
    prep_srt: Path
    size_bytes: int
    latest_mtime: datetime


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _mtime_utc(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, timezone.utc)


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


def collect_items(
    *,
    channels: Optional[list[str]],
    videos: Optional[list[str]],
    keep_recent_minutes: int,
) -> list[PurgeItem]:
    root = script_data_root()
    cutoff = _now_utc() - timedelta(minutes=keep_recent_minutes)
    out: list[PurgeItem] = []

    for ch_dir in _iter_channels(root, channels):
        ch = ch_dir.name.upper()
        for v_dir in _iter_videos(ch_dir, videos):
            v = str(v_dir.name).zfill(3)
            prep_dir = v_dir / "audio_prep"
            if not prep_dir.is_dir():
                continue

            prep_wav = prep_dir / f"{ch}-{v}.wav"
            prep_srt = prep_dir / f"{ch}-{v}.srt"
            if not (prep_wav.exists() and prep_srt.exists()):
                continue

            final_dir = audio_final_dir(ch, v)
            final_wav = final_dir / prep_wav.name
            final_srt = final_dir / prep_srt.name
            if not (final_wav.exists() and final_srt.exists()):
                continue

            latest = max(_mtime_utc(prep_wav), _mtime_utc(prep_srt))
            if latest >= cutoff:
                continue

            size = 0
            try:
                size += prep_wav.stat().st_size
            except Exception:
                pass
            try:
                size += prep_srt.stat().st_size
            except Exception:
                pass

            out.append(
                PurgeItem(
                    channel=ch,
                    video=v,
                    prep_wav=prep_wav,
                    prep_srt=prep_srt,
                    size_bytes=size,
                    latest_mtime=latest,
                )
            )

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print actions (default).")
    ap.add_argument("--run", action="store_true", help="Actually delete.")
    ap.add_argument("--channel", action="append", help="Target channel (repeatable). e.g. CH02")
    ap.add_argument("--video", action="append", help="Target video (repeatable). Requires --channel.")
    ap.add_argument("--keep-recent-minutes", type=int, default=360, help="Skip recently modified files.")
    args = ap.parse_args()

    if args.video and not args.channel:
        ap.error("--video requires --channel")

    do_run = bool(args.run)
    dry_run = bool(args.dry_run) or not do_run

    items = collect_items(channels=args.channel, videos=args.video, keep_recent_minutes=args.keep_recent_minutes)
    total = sum(i.size_bytes for i in items)
    print(f"[purge_audio_prep_binaries] items={len(items)} total={_fmt_bytes(total)} dry_run={dry_run}")

    if dry_run:
        for it in items[:20]:
            age_h = (_now_utc() - it.latest_mtime).total_seconds() / 3600.0
            print(f"  - {it.channel}-{it.video} size={_fmt_bytes(it.size_bytes)} age={age_h:.1f}h")
        if len(items) > 20:
            print(f"  ... ({len(items)-20} more)")
        print("[purge_audio_prep_binaries] dry-run only; pass --run to delete")
        return 0

    deleted = 0
    failed = 0
    for it in items:
        for p in (it.prep_wav, it.prep_srt):
            try:
                p.unlink()
                deleted += 1
            except Exception as exc:
                failed += 1
                print(f"[purge_audio_prep_binaries] FAILED delete {p}: {exc}", file=sys.stderr)

    print(f"[purge_audio_prep_binaries] deleted_files={deleted} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

