#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from _bootstrap import bootstrap


bootstrap(load_env=False)

from factory_common.paths import repo_root, script_data_root, video_root
from factory_common.text_sanitizer import strip_meta_from_script


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _norm_channel(value: str) -> str:
    ch = (value or "").strip().upper()
    if not ch:
        raise SystemExit("channel is required (e.g. CH06)")
    return ch


def _norm_video(value: str) -> str:
    token = (value or "").strip()
    if not token:
        raise SystemExit("video is required (e.g. 004)")
    digits = "".join(ch for ch in token if ch.isdigit())
    if not digits:
        raise SystemExit(f"invalid video: {value}")
    return f"{int(digits):03d}"


def _parse_videos(values: Optional[Iterable[str]]) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for raw in values:
        if raw is None:
            continue
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            out.append(_norm_video(part))
    return sorted(set(out))


@dataclass(frozen=True)
class TargetPaths:
    base_dir: Path
    assembled_human: Path
    assembled: Path

    @property
    def canonical(self) -> Path:
        return self.assembled_human if self.assembled_human.exists() else self.assembled


def _resolve_targets(channel: str, video: str) -> TargetPaths:
    base = video_root(channel, video)
    content_dir = base / "content"
    return TargetPaths(
        base_dir=base,
        assembled_human=content_dir / "assembled_human.md",
        assembled=content_dir / "assembled.md",
    )


def _backup_path(backup_root: Path, original: Path) -> Path:
    root = repo_root()
    rel = original.resolve().relative_to(root)
    return backup_root / rel


def _backup_file(path: Path, backup_root: Path) -> Path:
    dst = _backup_path(backup_root, path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return dst


def main() -> None:
    ap = argparse.ArgumentParser(description="Sanitize A-text (assembled_human/assembled) by removing meta citations/URLs.")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--videos", required=True, help="Comma-separated list, e.g. 004,005")
    ap.add_argument("--mode", choices=["dry-run", "run"], default="run")
    ap.add_argument(
        "--backup-dir",
        help="Override backup dir (default: workspaces/scripts/_archive/a_text_sanitize_<timestamp>)",
    )
    args = ap.parse_args()

    channel = _norm_channel(args.channel)
    videos = _parse_videos([args.videos])
    if not videos:
        raise SystemExit("no videos specified")

    backup_root = (
        Path(args.backup_dir).expanduser().resolve()
        if args.backup_dir
        else (script_data_root() / "_archive" / f"a_text_sanitize_{_utc_now_compact()}")
    )

    changed_any = False
    for video in videos:
        targets = _resolve_targets(channel, video)
        canonical = targets.canonical
        if not canonical.exists():
            raise SystemExit(f"A-text not found: {canonical}")

        raw = canonical.read_text(encoding="utf-8")
        res = strip_meta_from_script(raw)
        changed = (res.text != raw)

        print(f"[CHECK] {channel}-{video}: {canonical.name} changed={changed} removed={res.removed_counts}")

        if not changed or args.mode != "run":
            continue

        # Backup canonical + mirror if needed
        _backup_file(canonical, backup_root)
        if targets.assembled.exists():
            assembled_raw = targets.assembled.read_text(encoding="utf-8")
            if targets.assembled.resolve() != canonical.resolve() and assembled_raw != res.text:
                _backup_file(targets.assembled, backup_root)

        # Write canonical and mirror to the same sanitized content to avoid SoT split-brain.
        canonical.write_text(res.text, encoding="utf-8")
        targets.assembled.parent.mkdir(parents=True, exist_ok=True)
        targets.assembled.write_text(res.text, encoding="utf-8")

        changed_any = True

    if args.mode == "run" and changed_any:
        print(f"[OK] Updated scripts. Backup: {backup_root}")
    elif args.mode == "run":
        print("[OK] No changes needed.")


if __name__ == "__main__":
    main()
