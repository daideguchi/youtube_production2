#!/usr/bin/env python3
"""
Sync repo-managed Voicepeak user dictionary into the local Voicepeak settings folder.

Source of truth:
  - packages/audio_tts/data/voicepeak/dic.json

Destination:
  - ~/Library/Application Support/Dreamtonics/Voicepeak/settings/dic.json

This is intended for the "manual Voicepeak export" workflow so the GUI uses the same
dictionary the repo tracks.

Usage:
  python3 -m audio_tts.scripts.sync_voicepeak_user_dict
  python3 -m audio_tts.scripts.sync_voicepeak_user_dict --dry-run
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from factory_common.paths import audio_pkg_root


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha1(path: Path) -> str:
    import hashlib

    h = hashlib.sha1()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_dst_path() -> Path:
    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "Dreamtonics"
        / "Voicepeak"
        / "settings"
        / "dic.json"
    )


@dataclass(frozen=True)
class SyncResult:
    changed: bool
    src: Path
    dst: Path
    src_sha1: str
    dst_sha1: str | None
    backup_path: Path | None


def sync_voicepeak_user_dict(*, dry_run: bool = False) -> SyncResult:
    src = audio_pkg_root() / "data" / "voicepeak" / "dic.json"
    dst = _default_dst_path()

    if not src.exists():
        raise SystemExit(f"[VoicepeakDict] source missing: {src}")

    # Validate JSON to avoid pushing a broken dict into the app settings.
    try:
        payload = json.loads(src.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            raise ValueError("dict must be a JSON array")
    except Exception as exc:
        raise SystemExit(f"[VoicepeakDict] invalid source JSON: {src} ({exc})") from exc

    src_sha1 = _sha1(src)
    dst_sha1 = _sha1(dst) if dst.exists() else None

    if dst_sha1 == src_sha1:
        return SyncResult(
            changed=False,
            src=src,
            dst=dst,
            src_sha1=src_sha1,
            dst_sha1=dst_sha1,
            backup_path=None,
        )

    backup_path: Path | None = None
    if dst.exists():
        backup_path = dst.with_suffix(dst.suffix + f".bak_{_utc_now_compact()}")
        if not dry_run:
            shutil.copy2(dst, backup_path)

    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    return SyncResult(
        changed=True,
        src=src,
        dst=dst,
        src_sha1=src_sha1,
        dst_sha1=dst_sha1,
        backup_path=backup_path,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    res = sync_voicepeak_user_dict(dry_run=bool(args.dry_run))
    if res.changed:
        print("[VoicepeakDict] synced")
        print(f"- src: {res.src}")
        print(f"- dst: {res.dst}")
        print(f"- src_sha1: {res.src_sha1}")
        if res.dst_sha1:
            print(f"- prev_dst_sha1: {res.dst_sha1}")
        if res.backup_path:
            print(f"- backup: {res.backup_path}")
    else:
        print("[VoicepeakDict] already up-to-date")
        print(f"- src: {res.src}")
        print(f"- dst: {res.dst}")
        print(f"- sha1: {res.src_sha1}")


if __name__ == "__main__":
    main()
