#!/usr/bin/env python3
"""
pages_script_viewer_index.py — GitHub Pages用 Script Viewer の index.json を生成

目的:
  - `workspaces/scripts/**/content/assembled_human.md`（優先）/ `content/assembled.md` をブラウザで閲覧/コピーするための「索引」を用意する
  - 台本本文の複製はせず、GitHub の raw URL から参照する（Pages 側は静的）

出力:
  - `docs/data/index.json`

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
    Prefer canonical A-text `content/assembled_human.md`, fallback to `content/assembled.md`,
    then legacy `assembled.md`.
    """
    human = episode_dir / "content" / "assembled_human.md"
    if human.exists():
        return human
    candidate = episode_dir / "content" / "assembled.md"
    if candidate.exists():
        return candidate
    legacy = episode_dir / "assembled.md"
    if legacy.exists():
        return legacy
    return None


@dataclass(frozen=True)
class PlanningMeta:
    title: str
    status: str
    description_lead: str
    description_body: str
    main_tag: str
    sub_tag: str

    def tags(self) -> list[str]:
        out: list[str] = []
        for raw in (self.main_tag, self.sub_tag):
            s = str(raw or "").strip()
            if s and s not in out:
                out.append(s)
        return out

    def to_json(self) -> dict[str, object]:
        out: dict[str, object] = {}
        if self.status:
            out["status"] = self.status
        if self.description_lead:
            out["description_lead"] = self.description_lead
        if self.description_body:
            out["description_body"] = self.description_body
        if self.main_tag:
            out["main_tag"] = self.main_tag
        if self.sub_tag:
            out["sub_tag"] = self.sub_tag
        tags = self.tags()
        if tags:
            out["tags"] = tags
        return out


def _load_planning_meta(repo_root: Path) -> dict[tuple[str, int], PlanningMeta]:
    """
    Map (CHxx, video_number_int) -> subset of planning metadata from Planning CSV.
    """
    out: dict[tuple[str, int], PlanningMeta] = {}
    planning_root = repo_root / "workspaces" / "planning" / "channels"
    if not planning_root.exists():
        return out

    for csv_path in sorted(planning_root.glob("CH*.csv")):
        channel = csv_path.stem
        if not CHANNEL_RE.match(channel):
            continue
        try:
            with csv_path.open(newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
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
                    status = (row.get("進捗") or "").strip()
                    description_lead = (row.get("説明文_リード") or "").strip()
                    description_body = (row.get("説明文_この動画でわかること") or "").strip()
                    main_tag = (row.get("悩みタグ_メイン") or "").strip()
                    sub_tag = (row.get("悩みタグ_サブ") or "").strip()

                    out[(channel, video_num)] = PlanningMeta(
                        title=title,
                        status=status,
                        description_lead=description_lead,
                        description_body=description_body,
                        main_tag=main_tag,
                        sub_tag=sub_tag,
                    )
        except Exception:
            continue
    return out


@dataclass(frozen=True)
class ScriptIndexItem:
    channel: str
    video: str
    video_int: int
    title: str | None
    planning: PlanningMeta | None
    assembled_path: str


def build_index(repo_root: Path) -> dict:
    scripts_root = repo_root / "workspaces" / "scripts"
    planning = _load_planning_meta(repo_root)
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
                meta = planning.get((channel, video_int))
                title = meta.title if meta else None
                items.append(
                    ScriptIndexItem(
                        channel=channel,
                        video=video.zfill(3),
                        video_int=video_int,
                        title=title or None,
                        planning=meta,
                        assembled_path=assembled.relative_to(repo_root).as_posix(),
                    )
                )

    items = sorted(items, key=lambda it: (_channel_sort_key(it.channel), it.video_int))
    payload = {
        "generated_at": _now_iso_utc(),
        "generated_by": "scripts/ops/pages_script_viewer_index.py",
        "source": "workspaces/scripts/**/(content/assembled_human.md|content/assembled.md|assembled.md)",
        "count": len(items),
        "items": [
            {
                "channel": it.channel,
                "video": it.video,
                "video_id": f"{it.channel}-{it.video}",
                "title": it.title,
                **({"planning": it.planning.to_json()} if it.planning else {}),
                "assembled_path": it.assembled_path,
            }
            for it in items
        ],
    }
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate docs/data/index.json (script viewer index).")
    ap.add_argument("--write", action="store_true", help="Write docs/data/index.json")
    ap.add_argument("--stdout", action="store_true", help="Print JSON to stdout (default)")
    args = ap.parse_args()

    repo_root = bootstrap(load_env=False)
    payload = build_index(repo_root)
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"

    if args.write:
        out_path = repo_root / "docs" / "data" / "index.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        print(f"[pages_script_viewer_index] wrote {out_path.relative_to(repo_root)} (items={payload['count']})")
        return 0

    print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
