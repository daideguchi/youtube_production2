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
import shutil
import subprocess
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


def _git_ls_files(repo_root: Path, path: str) -> set[str]:
    try:
        p = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return set()
    if p.returncode != 0:
        return set()
    return {line.strip() for line in p.stdout.splitlines() if line.strip()}


def _best_tracked_assembled_by_key(tracked_paths: set[str]) -> dict[tuple[str, int], str]:
    """
    Map (channel, video_int) -> best assembled path, using tracked files only.

    Priority:
      1) content/assembled_human.md
      2) content/assembled.md
      3) assembled.md (legacy)
    """
    best: dict[tuple[str, int], tuple[int, str]] = {}
    for path in tracked_paths:
        if not path.startswith("workspaces/scripts/"):
            continue

        m = re.match(r"^workspaces/scripts/(CH\d+)/(\d+)/", path)
        if not m:
            continue
        channel = m.group(1)
        try:
            video_int = int(m.group(2))
        except Exception:
            continue

        prio: int | None = None
        if path.endswith("/content/assembled_human.md"):
            prio = 0
        elif path.endswith("/content/assembled.md"):
            prio = 1
        elif path.endswith("/assembled.md"):
            prio = 2
        else:
            continue

        key = (channel, video_int)
        cur = best.get(key)
        if cur is None or prio < cur[0]:
            best[key] = (prio, path)

    return {k: v for k, (_prio, v) in best.items()}


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
    assembled_path: str | None


def build_index(repo_root: Path) -> dict:
    planning = _load_planning_meta(repo_root)
    items: list[ScriptIndexItem] = []

    tracked = _git_ls_files(repo_root, "workspaces/scripts")
    assembled_by_key = _best_tracked_assembled_by_key(tracked)

    all_keys = set(planning.keys()) | set(assembled_by_key.keys())
    for (channel, video_int) in sorted(all_keys, key=lambda k: (_channel_sort_key(k[0]), k[1])):
        if not CHANNEL_RE.match(channel):
            continue
        video = f"{int(video_int):03d}"
        if not VIDEO_DIR_RE.match(video):
            continue
        meta = planning.get((channel, video_int))
        title = meta.title if meta else None
        assembled_path = assembled_by_key.get((channel, video_int))
        items.append(
            ScriptIndexItem(
                channel=channel,
                video=video,
                video_int=video_int,
                title=title or None,
                planning=meta,
                assembled_path=assembled_path,
            )
        )

    payload = {
        "generated_at": _now_iso_utc(),
        "generated_by": "scripts/ops/pages_script_viewer_index.py",
        "source": "Planning CSV (workspaces/planning/channels/*.csv) + git-tracked workspaces/scripts/**/(content/assembled_human.md|content/assembled.md|assembled.md)",
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


def _sync_release_archive_index(repo_root: Path) -> None:
    """
    GitHub Pages deploys `docs/` only.

    The archive UI (`/archive/`) loads `gh_releases_archive/index/latest.json`,
    so we mirror the tracked index into `docs/archive/gh_releases_archive/...`
    during the Pages build step.
    """
    src = repo_root / "gh_releases_archive" / "index" / "latest.json"
    dest = repo_root / "docs" / "archive" / "gh_releases_archive" / "index" / "latest.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, dest)
        return
    dest.write_text("[]\n", encoding="utf-8")


def _sync_script_viewer_deps(repo_root: Path) -> None:
    """
    Script Viewer on GitHub Pages should avoid cross-origin (raw.githubusercontent.com) fetches
    for large JSON metadata to keep mobile loading stable.

    We mirror a small set of repo-tracked JSON files into `docs/data/` so the UI can fetch them
    from the same origin (GitHub Pages).
    """
    deps: list[tuple[Path, Path, str]] = [
        (
            repo_root / "packages" / "script_pipeline" / "channels" / "channels_info.json",
            repo_root / "docs" / "data" / "channels_info.json",
            "[]\n",
        ),
        (
            repo_root / "workspaces" / "thumbnails" / "projects.json",
            repo_root / "docs" / "data" / "thumb_projects.json",
            json.dumps({"version": 1, "updated_at": _now_iso_utc(), "projects": []}, ensure_ascii=False, indent=2) + "\n",
        ),
    ]

    for src, dest, fallback in deps:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.copy2(src, dest)
            continue
        dest.write_text(fallback, encoding="utf-8")


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
        _sync_release_archive_index(repo_root)
        _sync_script_viewer_deps(repo_root)
        print(f"[pages_script_viewer_index] wrote {out_path.relative_to(repo_root)} (items={payload['count']})")
        return 0

    print(content, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
