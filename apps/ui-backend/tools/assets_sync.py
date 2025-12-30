#!/usr/bin/env python3
"""Thumbnail assets synchronisation helper.

This CLI inspects ``workspaces/planning/channels/CHxx.csv`` (SoT) and makes sure the
``workspaces/thumbnails/assets/{CH}/{video}/`` tree contains a directory for every active
企画.  It can also write ``planning_meta.json`` files with the videoタイトル、作成フラグ等を
記録し、孤立ディレクトリのレポートも出力します。

Example usage::

    # 一覧＋未作成ディレクトリを作成
    python3 apps/ui-backend/tools/assets_sync.py ensure

    # CH02 だけ dry-run で確認
    python3 apps/ui-backend/tools/assets_sync.py ensure --channels CH02 --dry-run

    # 既存フォルダとの整合性をチェック
    python3 apps/ui-backend/tools/assets_sync.py report

"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

def _find_repo_root(start: Path) -> Path:
    override = os.getenv("YTM_REPO_ROOT") or os.getenv("YTM_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


# Allow running as a script (not only `python -m ...`) after removing root symlinks.
REPO_ROOT = _find_repo_root(Path(__file__).resolve())
PACKAGES_ROOT = REPO_ROOT / "packages"
for p in (REPO_ROOT, PACKAGES_ROOT):
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)

from factory_common.paths import planning_root, thumbnails_root  # noqa: E402

DEFAULT_PLANNING = planning_root() / "channels"
ASSETS_ROOT = thumbnails_root() / "assets"
META_FILENAME = "planning_meta.json"


def _normalize_channel(value: str) -> str:
    token = (value or "").strip().upper()
    if token and ":" in token:
        token = token.split(":", 1)[0]
    return token


def _normalize_video(value: str) -> str:
    token = (value or "").strip()
    if not token:
        return ""
    if "-" in token:
        token = token.split("-", 1)[-1]
    token = token.strip()
    if not token:
        return ""
    if not token.isdigit():
        return ""
    return f"{int(token):03d}"


@dataclass(frozen=True)
class PlanningEntry:
    channel: str
    video: str
    title: str
    flag: str
    progress: str
    row_number: int
    source_path: Path

    @property
    def key(self) -> Tuple[str, str]:
        return (self.channel, self.video)


def _iter_planning_csv_paths(path: Path) -> List[Path]:
    if path.is_dir():
        out: List[Path] = []
        for csv_path in sorted(path.glob("*.csv")):
            if csv_path.name.lower().endswith("_planning_template.csv"):
                continue
            if not csv_path.stem.upper().startswith("CH"):
                continue
            out.append(csv_path)
        return out
    return [path]


def load_planning_rows(path: Path) -> List[PlanningEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Planning CSV not found: {path}")
    entries: List[PlanningEntry] = []
    for csv_path in _iter_planning_csv_paths(path):
        if not csv_path.exists():
            continue
        with csv_path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for idx, row in enumerate(reader, start=2):
                channel = _normalize_channel(row.get("チャンネル", "")) or _normalize_channel(csv_path.stem)
                video = _normalize_video(
                    row.get("動画番号")
                    or row.get("No.")
                    or row.get("動画No.")
                    or row.get("台本番号")
                    or row.get("動画ID")
                    or ""
                )
                if not channel or not video:
                    continue
                entry = PlanningEntry(
                    channel=channel,
                    video=video,
                    title=str(row.get("タイトル", "")).strip(),
                    flag=str(row.get("作成フラグ", "")).strip(),
                    progress=str(row.get("進捗", "")).strip(),
                    row_number=idx,
                    source_path=csv_path,
                )
                entries.append(entry)
    return entries


def parse_channel_list(value: Optional[str]) -> Optional[Set[str]]:
    if not value:
        return None
    channels = {_normalize_channel(part) for part in value.split(",") if part.strip()}
    return {ch for ch in channels if ch}


def parse_video_filters(value: Optional[str]) -> Tuple[Dict[str, Set[str]], Set[str]]:
    if not value:
        return {}, set()
    channel_specific: Dict[str, Set[str]] = {}
    generic: Set[str] = set()
    for raw in value.split(","):
        token = raw.strip()
        if not token:
            continue
        if "-" in token:
            ch, vid = token.split("-", 1)
            ch_norm = _normalize_channel(ch)
            vid_norm = _normalize_video(vid)
            if ch_norm and vid_norm:
                channel_specific.setdefault(ch_norm, set()).add(vid_norm)
            continue
        vid_norm = _normalize_video(token)
        if vid_norm:
            generic.add(vid_norm)
    return channel_specific, generic


def parse_flag_list(value: Optional[str]) -> Optional[Set[str]]:
    if not value:
        return None
    flags = {token.strip() for token in value.split(",") if token.strip()}
    return flags or None


def filter_entries(
    entries: Sequence[PlanningEntry],
    *,
    channels: Optional[Set[str]],
    video_filters: Tuple[Dict[str, Set[str]], Set[str]],
    include_flags: Optional[Set[str]],
    exclude_flags: Optional[Set[str]],
) -> List[PlanningEntry]:
    filtered: List[PlanningEntry] = []
    by_channel, generic = video_filters
    for entry in entries:
        if channels and entry.channel not in channels:
            continue
        if include_flags and entry.flag not in include_flags:
            continue
        if exclude_flags and entry.flag in exclude_flags:
            continue
        if by_channel or generic:
            allowed = False
            if entry.channel in by_channel and entry.video in by_channel[entry.channel]:
                allowed = True
            if entry.video in generic:
                allowed = True
            if not allowed:
                continue
        filtered.append(entry)
    return filtered


def gather_existing_assets(root: Path) -> Dict[str, Set[str]]:
    existing: Dict[str, Set[str]] = {}
    if not root.exists():
        return existing
    for channel_dir in root.iterdir():
        if not channel_dir.is_dir():
            continue
        channel = _normalize_channel(channel_dir.name)
        if not channel:
            continue
        for video_dir in channel_dir.iterdir():
            if not video_dir.is_dir():
                continue
            video = _normalize_video(video_dir.name)
            if not video:
                continue
            existing.setdefault(channel, set()).add(video)
    return existing


def ensure_directories(
    assets_root: Path,
    entries: Sequence[PlanningEntry],
    *,
    dry_run: bool,
    refresh_meta: bool,
) -> Tuple[int, int]:
    created = 0
    meta_written = 0
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"
    for entry in entries:
        target = assets_root / entry.channel / entry.video
        meta_path = target / META_FILENAME
        if not target.exists():
            if dry_run:
                print(f"[DRY-RUN] Would create {target}")
            else:
                target.mkdir(parents=True, exist_ok=True)
                created += 1
                print(f"Created {target}")
        if refresh_meta or not meta_path.exists():
            payload = {
                "schema": "ytm.thumbnail.planning_meta.v1",
                "channel": entry.channel,
                "video": entry.video,
                "title": entry.title,
                "flag": entry.flag,
                "progress": entry.progress,
                "planning_row": entry.row_number,
                "source": str(entry.source_path),
                "synced_at": timestamp,
            }
            if dry_run:
                print(f"[DRY-RUN] Would write {meta_path}")
            else:
                meta_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = meta_path.with_suffix(meta_path.suffix + ".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                tmp.replace(meta_path)
                meta_written += 1
    return created, meta_written


def report_state(
    entries: Sequence[PlanningEntry],
    existing: Dict[str, Set[str]],
    *,
    channels: Optional[Set[str]] = None,
) -> Tuple[List[PlanningEntry], List[Tuple[str, str]]]:
    expected = {(entry.channel, entry.video): entry for entry in entries}
    missing: List[PlanningEntry] = []
    for key, entry in expected.items():
        channel, video = key
        if channels and channel not in channels:
            continue
        if channel not in existing or video not in existing[channel]:
            missing.append(entry)

    orphans: List[Tuple[str, str]] = []
    for channel, videos in existing.items():
        if channels and channel not in channels:
            continue
        for video in videos:
            if (channel, video) not in expected:
                orphans.append((channel, video))
    return missing, orphans


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Thumbnail assets synchronisation helper")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--planning",
        type=Path,
        default=DEFAULT_PLANNING,
        help=f"Planning root (directory with CHxx.csv) or a single CSV path (default: {DEFAULT_PLANNING})",
    )
    common.add_argument("--assets-root", type=Path, default=ASSETS_ROOT, help=f"thumbnails/assets root (default: {ASSETS_ROOT})")
    common.add_argument("--channels", help="Comma-separated channel codes (e.g. CH01,CH02)")
    common.add_argument("--videos", help="Comma-separated video numbers (001) or CHxx-### identifiers")
    common.add_argument("--include-flags", help="Only include rows whose 作成フラグ matches these values (comma separated)")
    common.add_argument("--exclude-flags", help="Exclude rows whose 作成フラグ matches these values (comma separated)")

    subparsers = parser.add_subparsers(dest="command", required=True)

    ensure_parser = subparsers.add_parser("ensure", parents=[common], help="Create missing asset directories")
    ensure_parser.add_argument("--dry-run", action="store_true", help="Report actions without writing files")
    ensure_parser.add_argument("--refresh-meta", action="store_true", default=False, help="Overwrite existing planning_meta.json files")

    report_parser = subparsers.add_parser("report", parents=[common], help="List missing directories and orphans")
    report_parser.add_argument("--fail-on-issues", action="store_true", help="Exit with status 1 when missing/orphan entries exist")

    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    planning_entries = load_planning_rows(args.planning)
    channels = parse_channel_list(args.channels)
    video_filters = parse_video_filters(args.videos)
    include_flags = parse_flag_list(args.include_flags)
    exclude_flags = parse_flag_list(args.exclude_flags)

    filtered = filter_entries(
        planning_entries,
        channels=channels,
        video_filters=video_filters,
        include_flags=include_flags,
        exclude_flags=exclude_flags,
    )
    if not filtered:
        print("No planning rows matched the provided filters.")
        return 0

    if args.command == "report":
        existing = gather_existing_assets(args.assets_root)
        missing, orphans = report_state(filtered, existing, channels=channels)
        if missing:
            print("Missing asset directories:")
            for entry in missing:
                print(f"  - {entry.channel}/{entry.video} (タイトル: {entry.title or 'N/A'})")
        else:
            print("No missing directories detected.")
        if orphans:
            print("Orphan directories (present on disk but not in planning CSVs):")
            for channel, video in sorted(orphans):
                print(f"  - {channel}/{video}")
        else:
            print("No orphan directories detected.")
        if args.fail_on_issues and (missing or orphans):
            return 1
        return 0

    # default ensure command
    created, meta_written = ensure_directories(
        args.assets_root,
        filtered,
        dry_run=args.dry_run,
        refresh_meta=args.refresh_meta,
    )
    print(
        f"Completed ensure: {len(filtered)} rows processed | "
        f"directories created: {created} | meta updated: {meta_written}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
