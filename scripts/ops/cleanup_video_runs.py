#!/usr/bin/env python3
"""
cleanup_video_runs — Archive unneeded video run dirs under workspaces/video/runs.

Safety principles:
- Default is dry-run (prints + writes a report under workspaces/logs/regression/).
- --run archives (moves) to workspaces/video/_archive/<timestamp>/... (no delete).
- Never touch anything "recent" (keep-recent-minutes) or explicitly marked with `.keep`.

This targets "runs" only (SoT: workspaces/video/runs/{run_id}/). It does not modify
episode status.json by default; it purely reduces clutter/disk usage by archiving
older, non-selected runs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


# Make repo-root imports work even when executed from another CWD.
_REPO_ROOT = _discover_repo_root(Path(__file__).resolve())
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


from factory_common.paths import (  # noqa: E402
    status_path,
    video_runs_root,
    workspace_root,
)
from factory_common.timeline_manifest import parse_episode_id  # noqa: E402


REPORT_SCHEMA = "ytm.video_runs_cleanup_report.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _move_dir(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise SystemExit(f"archive destination already exists: {dest}")
    try:
        src.rename(dest)
        return
    except OSError:
        import shutil

        shutil.move(str(src), str(dest))


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _activity_mtime(run_dir: Path) -> float:
    """
    Conservative "activity" mtime estimate without scanning all images:
    - directory mtime
    - key files mtime
    - images/ directory mtime
    """
    mt = _safe_mtime(run_dir)
    for rel in ("timeline_manifest.json", "image_cues.json", "capcut_draft_info.json"):
        mt = max(mt, _safe_mtime(run_dir / rel))
    images_dir = run_dir / "images"
    if images_dir.exists():
        mt = max(mt, _safe_mtime(images_dir))
    return mt


def _extract_episode_key(run_dir: Path) -> Optional[tuple[str, str]]:
    """
    Return (CHxx, NNN) if the run can be mapped to an episode, else None.
    Prefer timeline_manifest.json when available.
    """
    tm = run_dir / "timeline_manifest.json"
    if tm.exists():
        try:
            data = _load_json(tm)
            ep_raw = data.get("episode") or {}
            ep_id = str(ep_raw.get("id") or "")
            ep = parse_episode_id(ep_id) or parse_episode_id(f"{ep_raw.get('channel','')}-{ep_raw.get('video','')}")
            if ep:
                return (ep.channel, ep.video)
        except Exception:
            pass

    ep = parse_episode_id(run_dir.name)
    if ep:
        return (ep.channel, ep.video)
    return None


def _selected_run_id(channel: str, video: str) -> Optional[str]:
    p = status_path(channel, video)
    if not p.exists():
        return None
    try:
        data = _load_json(p)
    except Exception:
        return None
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if isinstance(meta, dict):
        v = meta.get("video_run_id")
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


_LEGACY_CH_DIR_RE = re.compile(r"^CH\\d{2}$")


def _is_definitely_unscoped_trash(name: str) -> bool:
    n = name.lower()
    if n.startswith(("_tmp", "tmp_", "_failed", "failed_", ".tmp", "_test", "test_", "debug_", "_debug")):
        return True
    if _LEGACY_CH_DIR_RE.match(name):
        return True
    return False


@dataclass(frozen=True)
class RunInfo:
    run_id: str
    run_dir: Path
    episode: Optional[str]
    channel: Optional[str]
    video: Optional[str]
    has_capcut_draft: bool
    has_timeline_manifest: bool
    has_image_cues: bool
    has_images_dir: bool
    has_belt_config: bool
    activity_mtime: float
    draft_info_mtime: float

    def sort_key(self) -> tuple[Any, ...]:
        return (self.has_capcut_draft, self.draft_info_mtime, self.activity_mtime, self.run_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "episode": self.episode,
            "has_capcut_draft": self.has_capcut_draft,
            "has_timeline_manifest": self.has_timeline_manifest,
            "has_image_cues": self.has_image_cues,
            "has_images_dir": self.has_images_dir,
            "has_belt_config": self.has_belt_config,
            "activity_mtime": self.activity_mtime,
            "draft_info_mtime": self.draft_info_mtime,
        }


def _iter_runs(*, include_hidden_runs: bool) -> list[RunInfo]:
    root = video_runs_root()
    out: list[RunInfo] = []
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        if not include_hidden_runs and run_dir.name.startswith(("_", ".")):
            continue
        ep_key = _extract_episode_key(run_dir)
        ch = ep_key[0] if ep_key else None
        vid = ep_key[1] if ep_key else None
        episode = f"{ch}-{vid}" if (ch and vid) else None
        capcut_link = run_dir / "capcut_draft"
        draft_info = run_dir / "capcut_draft_info.json"
        out.append(
            RunInfo(
                run_id=run_dir.name,
                run_dir=run_dir,
                episode=episode,
                channel=ch,
                video=vid,
                has_capcut_draft=capcut_link.exists(),
                has_timeline_manifest=(run_dir / "timeline_manifest.json").exists(),
                has_image_cues=(run_dir / "image_cues.json").exists(),
                has_images_dir=(run_dir / "images").exists(),
                has_belt_config=(run_dir / "belt_config.json").exists(),
                activity_mtime=_activity_mtime(run_dir),
                draft_info_mtime=_safe_mtime(draft_info) if draft_info.exists() else 0.0,
            )
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive unneeded run dirs under workspaces/video/runs (safe dry-run by default).")
    ap.add_argument("--run", action="store_true", help="Actually archive (move) directories (default: dry-run).")
    ap.add_argument("--all", action="store_true", help="Scan all channels/videos (dangerous with --run; requires --yes).")
    ap.add_argument("--yes", action="store_true", help="Required when using --run with --all.")
    ap.add_argument("--channel", action="append", help="Target channel (repeatable). e.g. CH02")
    ap.add_argument("--video", action="append", help="Target video (repeatable). Requires --channel unless --all.")
    ap.add_argument("--keep-recent-minutes", type=int, default=360, help="Skip recently active runs (default: 360).")
    ap.add_argument("--keep-last-runs", type=int, default=2, help="Keep at least N top candidates per episode (default: 2).")
    ap.add_argument("--archive-unscoped", action="store_true", help="Also archive unscoped dirs that look like trash.")
    ap.add_argument("--include-hidden-runs", action="store_true", help="Include runs starting with _ or .")
    ap.add_argument("--archive-root", help="Override archive root (default: workspaces/video/_archive/<timestamp>).")
    args = ap.parse_args()

    if args.video and not args.channel and not args.all:
        ap.error("--video requires --channel (or use --all)")
    if not args.all and not args.channel:
        ap.error("provide --channel (repeatable) or use --all")
    if args.run and args.all and not args.yes:
        ap.error("--run --all requires --yes")

    channels = {str(ch).strip().upper() for ch in (args.channel or []) if str(ch).strip()}
    videos = {str(v).strip().zfill(3) for v in (args.video or []) if str(v).strip()}

    keep_recent_sec = max(0, int(args.keep_recent_minutes)) * 60
    now = time.time()

    archive_root = (
        Path(args.archive_root).expanduser().resolve()
        if args.archive_root
        else (workspace_root() / "video" / "_archive" / _utc_now_compact())
    )

    include_hidden = bool(args.include_hidden_runs)
    keep_last = max(1, int(args.keep_last_runs))
    do_run = bool(args.run)

    runs = _iter_runs(include_hidden_runs=include_hidden)

    # Filter runs to requested channels/videos (scoped runs only).
    scoped: list[RunInfo] = []
    unscoped: list[RunInfo] = []
    for r in runs:
        if r.episode is None:
            unscoped.append(r)
            continue
        if not args.all:
            if channels and r.channel not in channels:
                continue
            if videos and r.video not in videos:
                continue
        scoped.append(r)

    groups: dict[str, list[RunInfo]] = {}
    for r in scoped:
        groups.setdefault(r.episode or "(unknown)", []).append(r)
    for k in groups:
        groups[k].sort(key=lambda ri: ri.sort_key(), reverse=True)

    moves: list[dict[str, Any]] = []
    skipped_recent: list[str] = []
    skipped_keep: list[str] = []
    warnings: list[str] = []

    def is_recent(run: RunInfo) -> bool:
        return bool(keep_recent_sec and (now - run.activity_mtime) < keep_recent_sec)

    for episode, items in sorted(groups.items()):
        ch, vid = (episode.split("-", 1) + [""])[:2]
        selected = _selected_run_id(ch, vid) if ch and vid else None
        protected: set[str] = set()
        if selected:
            protected.add(selected)

        # Always keep recent runs.
        for r in items:
            if is_recent(r):
                protected.add(r.run_id)

        # Keep top-N candidates for safety (even if not recent).
        for r in items[:keep_last]:
            protected.add(r.run_id)

        for r in items:
            if (r.run_dir / ".keep").exists():
                protected.add(r.run_id)
                continue

        for r in items:
            if r.run_id in protected:
                if is_recent(r):
                    skipped_recent.append(r.run_id)
                else:
                    skipped_keep.append(r.run_id)
                continue

            dest = archive_root / (r.channel or "_unknown") / "runs" / r.run_id
            record = {
                "run_id": r.run_id,
                "episode": episode,
                "src": str(r.run_dir),
                "dest": str(dest),
                "reason": "episode_unselected",
                "selected_run_id": selected,
                "has_capcut_draft": r.has_capcut_draft,
                "draft_info_mtime": r.draft_info_mtime,
                "activity_mtime": r.activity_mtime,
            }
            moves.append(record)
            if do_run:
                try:
                    _move_dir(r.run_dir, dest)
                except Exception as exc:
                    warnings.append(f"failed to archive {r.run_id}: {exc}")

    if args.archive_unscoped:
        for r in unscoped:
            if not args.all:
                # Unscoped cannot be reliably filtered by channel; only allow in --all mode.
                continue
            if (r.run_dir / ".keep").exists():
                skipped_keep.append(r.run_id)
                continue
            if is_recent(r):
                skipped_recent.append(r.run_id)
                continue
            if not _is_definitely_unscoped_trash(r.run_id):
                continue

            dest = archive_root / "_unscoped" / "runs" / r.run_id
            record = {
                "run_id": r.run_id,
                "episode": None,
                "src": str(r.run_dir),
                "dest": str(dest),
                "reason": "unscoped_definite",
                "has_capcut_draft": r.has_capcut_draft,
                "draft_info_mtime": r.draft_info_mtime,
                "activity_mtime": r.activity_mtime,
            }
            moves.append(record)
            if do_run:
                try:
                    _move_dir(r.run_dir, dest)
                except Exception as exc:
                    warnings.append(f"failed to archive {r.run_id}: {exc}")

    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": _utc_now_iso(),
        "mode": "run" if do_run else "dry-run",
        "filters": {
            "all": bool(args.all),
            "channels": sorted(channels),
            "videos": sorted(videos),
            "include_hidden_runs": include_hidden,
        },
        "policy": {
            "keep_recent_minutes": int(args.keep_recent_minutes),
            "keep_last_runs": keep_last,
            "archive_unscoped": bool(args.archive_unscoped),
        },
        "archive_root": str(archive_root),
        "counters": {
            "runs_total": len(runs),
            "runs_scoped": len(scoped),
            "runs_unscoped": len(unscoped),
            "episodes": len(groups),
            "planned_moves": len(moves),
            "skipped_recent": len(set(skipped_recent)),
            "skipped_keep": len(set(skipped_keep)),
            "warnings": len(warnings),
        },
        "warnings": warnings,
        "moves": moves,
    }

    if do_run:
        _save_json(archive_root / "archive_report.json", report)
        print(f"[cleanup_video_runs] mode=run archived={len(moves)} report={archive_root / 'archive_report.json'}")
    else:
        log_dir = workspace_root() / "logs" / "regression"
        log_dir.mkdir(parents=True, exist_ok=True)
        out = log_dir / f"video_runs_cleanup_dryrun_{_utc_now_compact()}.json"
        _save_json(out, report)
        print(f"[cleanup_video_runs] mode=dry-run planned={len(moves)} report={out}")

    if warnings:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

