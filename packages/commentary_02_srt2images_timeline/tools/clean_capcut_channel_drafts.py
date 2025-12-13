#!/usr/bin/env python3
"""
Clean CapCut drafts for a single channel by archiving non-final / broken drafts.

Policy (safe default):
  - KEEP:
      - Final drafts: directories starting with "★<CH>-"
      - The active template from channel_presets.json (capcut_template)
  - ARCHIVE:
      - Anything else under the CapCut draft root that belongs to the channel (CHxx or ★CHxx)

This keeps CapCut UI tidy while preserving a rollback path.

Usage:
  cd commentary_02_srt2images_timeline
  python3 tools/clean_capcut_channel_drafts.py --channel CH05 --apply

Notes:
  - Requires macOS Full Disk Access for the running Terminal/Python to write under
    ~/Movies/CapCut/User Data/Projects/com.lveditor.draft
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Iterable

def _bootstrap_repo_root() -> Path:
    start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


_BOOTSTRAP_REPO = _bootstrap_repo_root()
if str(_BOOTSTRAP_REPO) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_REPO))

from factory_common.paths import video_pkg_root  # noqa: E402

PROJECT_ROOT = video_pkg_root()
DEFAULT_DRAFT_ROOT = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"
CHANNEL_PRESETS_PATH = PROJECT_ROOT / "config" / "channel_presets.json"


def _load_capcut_template_from_presets(channel: str) -> str | None:
    if not CHANNEL_PRESETS_PATH.exists():
        return None
    try:
        data = json.loads(CHANNEL_PRESETS_PATH.read_text(encoding="utf-8"))
        preset = (data.get("channels") or {}).get(channel)
        if isinstance(preset, dict):
            t = (preset.get("capcut_template") or "").strip()
            return t or None
    except Exception:
        return None
    return None


def _iter_channel_entries(draft_root: Path, channel: str) -> Iterable[Path]:
    prefix = channel
    star_prefix = f"★{channel}"
    for p in draft_root.iterdir():
        if not p.is_dir():
            continue
        name = p.name
        if name.startswith(prefix) or name.startswith(star_prefix):
            yield p


def _safe_archive_path(archive_dir: Path, name: str) -> Path:
    """
    Resolve name collisions in archive by suffixing __dupN.
    """
    target = archive_dir / name
    if not target.exists():
        return target
    for i in range(1, 1000):
        alt = archive_dir / f"{name}__dup{i}"
        if not alt.exists():
            return alt
    raise RuntimeError(f"archive collision: too many duplicates for {name}")


def _voiceover_segments_count(draft_dir: Path) -> int | None:
    """
    Return voiceover segment count from draft_content.json.
    None if draft_content.json is missing or voiceover track is missing.
    """
    try:
        content_path = draft_dir / "draft_content.json"
        if not content_path.exists():
            return None
        data = json.loads(content_path.read_text(encoding="utf-8"))
        tracks = data.get("tracks", []) or []
        for tr in tracks:
            if not isinstance(tr, dict):
                continue
            if (tr.get("type") or "").strip() != "audio":
                continue
            if (tr.get("name") or "").strip() != "voiceover":
                continue
            segs = tr.get("segments") or []
            return len(segs) if isinstance(segs, list) else 0
        return None
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="Channel ID (e.g., CH05)")
    ap.add_argument("--draft-root", default=str(DEFAULT_DRAFT_ROOT), help="CapCut draft root")
    ap.add_argument("--apply", action="store_true", help="Actually move drafts to archive (default: dry-run)")
    ap.add_argument("--delete", action="store_true", help="Permanently delete instead of archiving (DANGEROUS)")
    ap.add_argument("--list", action="store_true", help="Print the keep/move item names")
    ap.add_argument(
        "--require-voiceover",
        action="store_true",
        help="Abort if kept ★final drafts do not have exactly 1 voiceover segment (prevents 'final' without audio)",
    )
    ap.add_argument("--force", action="store_true", help="Proceed even if require-voiceover check fails")
    args = ap.parse_args()

    channel = args.channel.upper().strip()
    if not re.fullmatch(r"CH\d{2}", channel):
        raise SystemExit(f"Invalid --channel: {args.channel}")

    draft_root = Path(args.draft_root).expanduser().resolve()
    if not draft_root.exists():
        raise SystemExit(f"draft_root not found: {draft_root}")

    # Keep set: final star drafts + active template
    capcut_template = _load_capcut_template_from_presets(channel)
    keep_exact: set[str] = set()
    if capcut_template:
        keep_exact.add(capcut_template)

    def is_keep(name: str) -> bool:
        if name.startswith(f"★{channel}-") or name.startswith(f"★{channel}_"):
            return True
        if name in keep_exact:
            return True
        return False

    entries = sorted(_iter_channel_entries(draft_root, channel), key=lambda p: p.name)

    keep = [p for p in entries if is_keep(p.name)]
    move = [p for p in entries if not is_keep(p.name)]

    print(f"[plan] channel={channel} draft_root={draft_root}")
    print(f"[keep] {len(keep)} items (★final + active template)")
    print(f"[move] {len(move)} items")
    if args.list:
        for p in keep:
            print(f"[keep] {p.name}")
        for p in move:
            print(f"[move] {p.name}")

    if args.require_voiceover:
        bad: list[tuple[str, int | None]] = []
        for p in keep:
            if not (p.name.startswith(f"★{channel}-") or p.name.startswith(f"★{channel}_")):
                continue
            c = _voiceover_segments_count(p)
            if c != 1:
                bad.append((p.name, c))
        if bad:
            print("[check] require-voiceover failed for ★final drafts:")
            for name, c in bad[:50]:
                print(f"  - {name} voiceover_segments={c}")
            if args.apply and (not args.force):
                raise SystemExit(
                    "Aborting cleanup: ★final drafts are not complete (missing voiceover). "
                    "Rebuild drafts with audio insertion first, or rerun with --force."
                )

    if not args.apply:
        print("[dry-run] No changes made. Use --apply to execute.")
        if capcut_template:
            print(f"[keep] capcut_template={capcut_template}")
        return

    if args.delete:
        for p in move:
            print(f"[delete] {p.name}")
            shutil.rmtree(p)
        print("[done] Deleted.")
        return

    # Archive directory lives under ~/Movies/CapCut/
    capcut_base = draft_root.parents[2] if len(draft_root.parents) >= 3 else Path.home() / "Movies/CapCut"
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_dir = capcut_base / f"Archive_{ts}" / channel
    archive_dir.mkdir(parents=True, exist_ok=True)

    for p in move:
        dst = _safe_archive_path(archive_dir, p.name)
        print(f"[archive] {p.name} -> {dst}")
        shutil.move(str(p), str(dst))

    print(f"[done] Archived to: {archive_dir}")


if __name__ == "__main__":
    # Reduce surprise from inherited PYTHONPATH in some shells
    os.environ.pop("PYTHONPATH", None)
    main()
