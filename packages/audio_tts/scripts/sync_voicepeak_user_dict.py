#!/usr/bin/env python3
"""
Sync repo-managed Voicepeak user dictionary into the local Voicepeak settings folder.

Source of truth:
  - packages/audio_tts/data/voicepeak/dic.json

Destination:
  - ~/Library/Application Support/Dreamtonics/Voicepeak/settings/dic.json

This is intended for the "manual Voicepeak export" workflow so the GUI/CLI uses the same
dictionary the repo tracks.

IMPORTANT (safety):
  - We NEVER overwrite the local dictionary destructively.
  - If the local dict already contains entries, we only *add* missing entries from the repo.
    (Local entries win when the same surface exists.)

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

    def _load_json_array(path: Path) -> list[dict]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"[VoicepeakDict] invalid JSON: {path} ({exc})") from exc
        if not isinstance(payload, list):
            raise SystemExit(f"[VoicepeakDict] dict must be a JSON array: {path}")
        out: list[dict] = []
        for item in payload:
            if isinstance(item, dict):
                out.append(item)
        return out

    def _key(ent: dict) -> tuple[str, str, str]:
        sur = str(ent.get("sur") or "").strip()
        pos = str(ent.get("pos") or "").strip()
        lang = str(ent.get("lang") or "").strip()
        return (sur, pos, lang)

    src_sha1 = _sha1(src)
    src_entries = _load_json_array(src)

    if not dst.exists():
        if not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return SyncResult(
            changed=True,
            src=src,
            dst=dst,
            src_sha1=src_sha1,
            dst_sha1=None,
            backup_path=None,
        )

    dst_sha1 = _sha1(dst)
    dst_entries = _load_json_array(dst)

    dst_keys = {_key(ent) for ent in dst_entries if _key(ent)[0]}
    to_add = [ent for ent in src_entries if (_key(ent)[0] and _key(ent) not in dst_keys)]

    if not to_add:
        return SyncResult(
            changed=False,
            src=src,
            dst=dst,
            src_sha1=src_sha1,
            dst_sha1=dst_sha1,
            backup_path=None,
        )

    backup_path: Path | None = None
    backup_path = dst.with_suffix(dst.suffix + f".bak_{_utc_now_compact()}")
    if not dry_run:
        shutil.copy2(dst, backup_path)

    merged = dst_entries + to_add
    if not dry_run:
        dst.parent.mkdir(parents=True, exist_ok=True)
        # Keep ensure_ascii=True so VOICEPEAK's settings format stays consistent (\u escapes).
        dst.write_text(json.dumps(merged, ensure_ascii=True, indent=2), encoding="utf-8")

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
