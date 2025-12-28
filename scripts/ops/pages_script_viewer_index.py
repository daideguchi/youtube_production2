#!/usr/bin/env python3
"""
pages_script_viewer_index.py — GitHub Pages用 Script Viewer の index.json を生成

目的:
  - `workspaces/scripts/**/assembled.md` をブラウザで閲覧/コピーするための「索引」を用意する
  - 台本本文の複製はせず、GitHub の raw URL から参照する（Pages 側は静的）

出力:
  - `pages/script_viewer/data/index.json`

Usage:
  python3 scripts/ops/pages_script_viewer_index.py --stdout
  python3 scripts/ops/pages_script_viewer_index.py --write
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from _bootstrap import bootstrap


CHANNEL_RE = re.compile(r"^CH\d+$")
VIDEO_DIR_RE = re.compile(r"^\d+$")


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _channel_sort_key(channel: str) -> tuple[int, str]:
    m = re.match(r"^CH(\d+)$", channel)
    return (int(m.group(1)) if m else 10**9, channel)


def _discover_assembled_path(episode_dir: Path) -> Path | None:
    """
    Prefer new SoT path `content/assembled.md`, fallback to legacy `assembled.md`.
    """
    candidate = episode_dir / "content" / "assembled.md"
    if candidate.exists():
        return candidate
    legacy = episode_dir / "assembled.md"
    if legacy.exists():
        return legacy
    return None


def _load_planning_titles(repo_root: Path) -> dict[tuple[str, int], str]:
    """
    Map (CHxx, video_number_int) -> title from Planning CSV.
    """
    out: dict[tuple[str, int], str] = {}
    planning_root = repo_root / "workspaces" / "planning" / "channels"
    if not planning_root.exists():
        return out

    for csv_path in sorted(planning_root.glob("CH*.csv")):
        channel = csv_path.stem
        if not CHANNEL_RE.match(channel):
            continue
        try:
            raw = csv_path.read_text(encoding="utf-8-sig")
        except Exception:
            continue
        try:
            reader = csv.DictReader(raw.splitlines())
        except Exception:
            continue
        if not reader.fieldnames:
            continue
        for row in reader:
            try:
                video_raw = (row.get("動画番号") or "").strip()
                if not video_raw:
                    continue
                video_num = int(video_raw)
            except Exception:
                continue
            title = (row.get("タイトル") or "").strip()
            if not title:
                continue
            out[(channel, video_num)] = title
    return out


@dataclass(frozen=True)
class ScriptIndexItem:
    channel: str
    video: str
    video_int: int
    title: str | None
    assembled_path: str


def build_index(repo_root: Path) -> dict:
    scripts_root = repo_root / "workspaces" / "scripts"
    titles = _load_planning_titles(repo_root)
    items: list[ScriptIndexItem] = []

    if scripts_root.exists():
        for channel_dir in sorted([p for p in scripts_root.iterdir() if p.is_dir()], key=lambda p: _channel_sort_key(p.name)):
            channel = channel_dir.name
            if not CHANNEL_RE.match(channel):
                continue
            for episode_dir in sorted([p for p in channel_dir.iterdir() if p.is_dir()], key=lambda p: p.name):
                video = episode_dir.name
                if not VIDEO_DIR_RE.match(video):
                    continue
                try:
                    video_int = int(video)
                except Exception:
                    continue
                assembled = _discover_assembled_path(episode_dir)
                if not assembled:
                    continue
                title = titles.get((channel, video_int))
                items.append(
                    ScriptIndexItem(
                        channel=channel,
                        video=video.zfill(3),
                        video_int=video_int,
                        title=title or None,
                        assembled_path=assembled.relative_to(repo_root).as_posix(),
                    )
                )

    items = sorted(items, key=lambda it: (_channel_sort_key(it.channel), it.video_int))
    payload = {
        "generated_at": _now_iso_utc(),
        "generated_by": "scripts/ops/pages_script_viewer_index.py",
        "source": "workspaces/scripts/**/(content/assembled.md|assembled.md)",
        "count": len(items),
        "items": [
            {
                "channel": it.channel,
                "video": it.video,
                "video_id": f"{it.channel}-{it.video}",
                "title": it.title,
                "assembled_path": it.assembled_path,
            }
            for it in items
        ],
    }
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate pages/script_viewer/data/index.json (script viewer index).")
    ap.add_argument("--write", action="store_true", help="Write pages/script_viewer/data/index.json")
    ap.add_argument("--stdout", action="store_true", help="Print JSON to stdout (default)")
    args = ap.parse_args()

    repo_root = bootstrap(load_env=False)
    payload = build_index(repo_root)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    if args.write:
        out_path = repo_root / "pages" / "script_viewer" / "data" / "index.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        print(f"[pages_script_viewer_index] wrote {out_path.relative_to(repo_root)} (items={payload['count']})")
        return 0

    print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

