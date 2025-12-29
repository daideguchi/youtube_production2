#!/usr/bin/env python3
"""
pages_snapshot_export.py — Backendなしの「閲覧専用スナップショット」用データを書き出す

目的:
- GitHub Pages 上で、ローカルUIに近い「状況の俯瞰」をできるだけ再現する（読み取り専用）。
- backend (FastAPI) や workspaces のローカル参照に依存しない。

出力（GitHub Pagesで配信される想定）:
- docs/data/snapshot/channels.json
- docs/data/snapshot/{CH}.json

Usage:
  python3 scripts/ops/pages_snapshot_export.py --write
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import channels_csv_path, repo_root, script_data_root  # noqa: E402


CHANNEL_RE = re.compile(r"^CH\d{2}$")
VIDEO_DIR_RE = re.compile(r"^\d+$")

# Keep snapshot JSON small: do NOT dump long prompt/script columns.
PLANNING_KEYS_WHITELIST = [
    "動画番号",
    "タイトル",
    "進捗",
    "更新日時",
    "作成フラグ",
    "悩みタグ_メイン",
    "悩みタグ_サブ",
    "ライフシーン",
    "キーコンセプト",
    "ベネフィット一言",
    "内容（企画要約）",
    "説明文_リード",
]

SCRIPT_STAGE_KEYS = [
    "script_outline",
    "script_draft",
    "script_review",
    "quality_check",
    "script_validation",
    "audio_synthesis",
]


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _norm_channel(ch: str) -> str:
    s = str(ch or "").strip().upper()
    if re.fullmatch(r"CH\d+", s):
        return f"CH{int(s[2:]):02d}"
    return s


def _norm_video(video: str | int) -> str:
    try:
        return f"{int(video):03d}"
    except Exception:
        return str(video or "").strip().zfill(3)


def _read_csv_rows(csv_path: Path) -> list[dict[str, str]]:
    if not csv_path.exists():
        return []
    raw = csv_path.read_text(encoding="utf-8-sig")
    reader = csv.DictReader(raw.splitlines())
    out: list[dict[str, str]] = []
    for row in reader:
        if not isinstance(row, dict):
            continue
        normalized: dict[str, str] = {}
        for k, v in row.items():
            if k is None:
                continue
            normalized[str(k).strip()] = str(v or "").strip()
        out.append(normalized)
    return out


def _discover_assembled_path(episode_dir: Path) -> str | None:
    """
    Prefer new SoT path `content/assembled.md`, fallback to legacy `assembled.md`.
    Return repo-relative POSIX path.
    """
    root = repo_root()
    candidate = episode_dir / "content" / "assembled.md"
    if candidate.exists():
        return candidate.relative_to(root).as_posix()
    legacy = episode_dir / "assembled.md"
    if legacy.exists():
        return legacy.relative_to(root).as_posix()
    return None


def _read_json_optional(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _script_summary(channel: str, video: str, episode_dir: Path) -> dict[str, Any] | None:
    status_path = episode_dir / "status.json"
    obj = _read_json_optional(status_path)
    if not obj:
        return None
    stages = obj.get("stages") if isinstance(obj.get("stages"), dict) else {}
    stage_statuses: dict[str, str] = {}
    for k in SCRIPT_STAGE_KEYS:
        st = stages.get(k) if isinstance(stages, dict) else None
        if isinstance(st, dict):
            stage_statuses[k] = str(st.get("status") or "").strip()

    return {
        "exists": True,
        "status_path": status_path.relative_to(repo_root()).as_posix(),
        "status": str(obj.get("status") or "").strip(),
        "updated_at": str(obj.get("updated_at") or "").strip(),
        "stages": stage_statuses,
    }


@dataclass(frozen=True)
class ChannelSnapshot:
    channel: str
    planning_csv_rel: str
    planning_count: int
    scripts_count: int
    data_rel: str


def _build_channel_payload(channel: str) -> dict[str, Any]:
    root = repo_root()
    ch = _norm_channel(channel)
    planning_csv = channels_csv_path(ch)
    planning_rows = _read_csv_rows(planning_csv)

    planning_by_video: dict[str, dict[str, str]] = {}
    for row in planning_rows:
        raw_video = row.get("動画番号") or ""
        if not raw_video.strip():
            continue
        try:
            video_int = int(raw_video)
        except Exception:
            continue
        video = _norm_video(video_int)
        planning_by_video[video] = {k: row.get(k, "") for k in PLANNING_KEYS_WHITELIST if k in row}

    # Collect script dirs (union of planning videos + existing workspaces/scripts dirs)
    scripts_root = script_data_root() / ch
    script_videos: set[str] = set()
    if scripts_root.exists():
        for episode_dir in scripts_root.iterdir():
            if not episode_dir.is_dir():
                continue
            if not VIDEO_DIR_RE.match(episode_dir.name):
                continue
            script_videos.add(_norm_video(episode_dir.name))

    all_videos = sorted(set(planning_by_video.keys()) | script_videos, key=lambda v: int(v))

    episodes: list[dict[str, Any]] = []
    scripts_count = 0
    for video in all_videos:
        episode_dir = scripts_root / video
        assembled_path = _discover_assembled_path(episode_dir) if episode_dir.exists() else None
        script = _script_summary(ch, video, episode_dir) if episode_dir.exists() else None
        if script:
            scripts_count += 1
        planning = planning_by_video.get(video)
        title = (planning or {}).get("タイトル") or None
        episodes.append(
            {
                "channel": ch,
                "video": video,
                "video_id": f"{ch}-{video}",
                "title": title,
                "planning": planning,
                "assembled_path": assembled_path,
                "script": script,
            }
        )

    return {
        "schema": "ytm.pages.snapshot.channel.v1",
        "generated_at": _now_iso_utc(),
        "channel": ch,
        "planning_csv": planning_csv.relative_to(root).as_posix(),
        "planning_count": len(planning_by_video),
        "scripts_count": scripts_count,
        "episodes": episodes,
    }


def _discover_channels() -> list[str]:
    root = repo_root()
    planning_dir = root / "workspaces" / "planning" / "channels"
    if not planning_dir.exists():
        return []
    channels: list[str] = []
    for p in sorted(planning_dir.glob("CH*.csv")):
        if not p.is_file():
            continue
        ch = p.stem.upper()
        if not CHANNEL_RE.match(ch):
            continue
        channels.append(ch)
    return channels


def main() -> int:
    ap = argparse.ArgumentParser(description="Export snapshot JSON for GitHub Pages (read-only UI).")
    ap.add_argument("--write", action="store_true", help="Write under docs/data/snapshot/")
    args = ap.parse_args()

    root = repo_root()
    docs_root = root / "docs"
    out_dir = docs_root / "data" / "snapshot"

    channels = _discover_channels()
    snapshots: list[ChannelSnapshot] = []

    if args.write:
        out_dir.mkdir(parents=True, exist_ok=True)
        for ch in channels:
            payload = _build_channel_payload(ch)
            out_path = out_dir / f"{ch}.json"
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            snapshots.append(
                ChannelSnapshot(
                    channel=ch,
                    planning_csv_rel=str(payload.get("planning_csv")),
                    planning_count=int(payload.get("planning_count") or 0),
                    scripts_count=int(payload.get("scripts_count") or 0),
                    data_rel=out_path.relative_to(docs_root).as_posix(),
                )
            )

        index = {
            "schema": "ytm.pages.snapshot.index.v1",
            "generated_at": _now_iso_utc(),
            "generated_by": "scripts/ops/pages_snapshot_export.py",
            "channels": [
                {
                    "channel": s.channel,
                    "planning_csv": s.planning_csv_rel,
                    "planning_count": s.planning_count,
                    "scripts_count": s.scripts_count,
                    "data_path": s.data_rel,
                }
                for s in snapshots
            ],
        }
        (out_dir / "channels.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[pages_snapshot_export] wrote {len(snapshots)} channels under {out_dir.relative_to(root)}")
        return 0

    # default: stdout only (small)
    print(
        json.dumps(
            {
                "schema": "ytm.pages.snapshot.preview.v1",
                "generated_at": _now_iso_utc(),
                "channels": channels,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

