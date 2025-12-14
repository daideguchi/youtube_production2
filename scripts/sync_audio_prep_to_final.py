#!/usr/bin/env python3
"""
One-shot sync: copy audio outputs from workspaces/scripts/**/audio_prep/ into
workspaces/audio/final/** when final artifacts are missing.

Rationale:
- Downstream (CapCut / auto-draft / UI) treats workspaces/audio/final as canonical.
- Older runs may have WAV/SRT only under audio_prep.

Safety:
- Never overwrites existing final files (copies only when missing).
- Skips very recent audio_prep dirs to avoid interfering with in-progress runs.

Usage:
  python3 scripts/sync_audio_prep_to_final.py --dry-run
  python3 scripts/sync_audio_prep_to_final.py --run
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


@dataclass(frozen=True)
class SyncItem:
    channel: str
    video: str
    prep_dir: Path
    final_dir: Path
    wav: bool
    srt: bool
    log: bool
    a_text: bool
    reason: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _mtime_utc(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, timezone.utc)


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


def _read_a_text(video_dir: Path) -> Optional[str]:
    candidates = [
        video_dir / "content" / "assembled_human.md",
        video_dir / "content" / "assembled.md",
        video_dir / "audio_prep" / "script_audio_human.txt",
        video_dir / "audio_prep" / "script_sanitized.txt",
    ]
    for p in candidates:
        try:
            if p.exists() and p.is_file():
                return p.read_text(encoding="utf-8")
        except Exception:
            continue
    return None


def collect_items(
    *,
    channels: Optional[list[str]],
    videos: Optional[list[str]],
    keep_recent_minutes: int,
) -> list[SyncItem]:
    root = script_data_root()
    cutoff = _now_utc() - timedelta(minutes=keep_recent_minutes)
    out: list[SyncItem] = []

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

            # Skip very recent synthesis outputs (avoid in-progress runs).
            latest = max(_mtime_utc(prep_wav), _mtime_utc(prep_srt), _mtime_utc(prep_dir / "log.json"))
            if latest >= cutoff:
                continue

            final_dir = audio_final_dir(ch, v)
            final_wav = final_dir / prep_wav.name
            final_srt = final_dir / prep_srt.name
            final_log = final_dir / "log.json"
            final_a_text = final_dir / "a_text.txt"

            wav_missing = not final_wav.exists()
            srt_missing = not final_srt.exists()
            log_missing = not final_log.exists() and (prep_dir / "log.json").exists()
            a_text_missing = not final_a_text.exists()

            if not (wav_missing or srt_missing or log_missing or a_text_missing):
                continue

            out.append(
                SyncItem(
                    channel=ch,
                    video=v,
                    prep_dir=prep_dir,
                    final_dir=final_dir,
                    wav=wav_missing,
                    srt=srt_missing,
                    log=log_missing,
                    a_text=a_text_missing,
                    reason="final_missing",
                )
            )

    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Print actions (default).")
    ap.add_argument("--run", action="store_true", help="Actually copy.")
    ap.add_argument("--channel", action="append", help="Target channel (repeatable). e.g. CH08")
    ap.add_argument("--video", action="append", help="Target video (repeatable). Requires --channel.")
    ap.add_argument("--keep-recent-minutes", type=int, default=360, help="Skip recent directories (avoid in-progress).")
    args = ap.parse_args()

    if args.video and not args.channel:
        ap.error("--video requires --channel")

    do_run = bool(args.run)
    dry_run = bool(args.dry_run) or not do_run

    items = collect_items(channels=args.channel, videos=args.video, keep_recent_minutes=args.keep_recent_minutes)
    print(f"[sync_audio_prep_to_final] items={len(items)} dry_run={dry_run} keep_recent_minutes={args.keep_recent_minutes}")

    if dry_run:
        for it in items[:40]:
            flags = ",".join([k for k, v in (("wav", it.wav), ("srt", it.srt), ("log", it.log), ("a_text", it.a_text)) if v])
            print(f"  - {it.channel}-{it.video} -> {it.final_dir} ({flags})")
        if len(items) > 40:
            print(f"  ... ({len(items)-40} more)")
        print("[sync_audio_prep_to_final] dry-run only; pass --run to copy")
        return 0

    copied = 0
    for it in items:
        it.final_dir.mkdir(parents=True, exist_ok=True)
        ch, v = it.channel, it.video
        prep_wav = it.prep_dir / f"{ch}-{v}.wav"
        prep_srt = it.prep_dir / f"{ch}-{v}.srt"
        final_wav = it.final_dir / prep_wav.name
        final_srt = it.final_dir / prep_srt.name

        if it.wav and prep_wav.exists() and not final_wav.exists():
            shutil.copy2(prep_wav, final_wav)
            copied += 1
        if it.srt and prep_srt.exists() and not final_srt.exists():
            shutil.copy2(prep_srt, final_srt)
            copied += 1

        prep_log = it.prep_dir / "log.json"
        final_log = it.final_dir / "log.json"
        if it.log and prep_log.exists() and not final_log.exists():
            shutil.copy2(prep_log, final_log)
            copied += 1

        final_a_text = it.final_dir / "a_text.txt"
        if it.a_text and not final_a_text.exists():
            # video_dir is parent of audio_prep
            video_dir = it.prep_dir.parent
            text = _read_a_text(video_dir)
            if text is not None:
                final_a_text.write_text(text, encoding="utf-8")
                copied += 1

    print(f"[sync_audio_prep_to_final] copied_files={copied} items={len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
