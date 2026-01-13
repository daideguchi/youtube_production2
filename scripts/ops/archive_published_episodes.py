#!/usr/bin/env python3
"""
archive_published_episodes — Archive "published" artifacts by Planning SoT.

Published definition (SoT):
- workspaces/planning/channels/CHxx.csv: 進捗 == 投稿済み

Safety:
- Default is dry-run.
- --run executes the requested action.
- Default action is archive (MOVE).
- --delete switches action to DELETE (dangerous; irreversible).
- For batch runs, --yes is required.
- Respects coordination locks (scripts/agent_org.py lock).

Artifacts covered (opt-in per domain; default: all):
- audio final dirs
- thumbnails assets dirs
- video input files
- video runs dirs
- capcut local draft dirs
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from _bootstrap import bootstrap

_PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.locks import find_blocking_lock, load_active_locks  # noqa: E402
from factory_common.paths import (  # noqa: E402
    audio_artifacts_root,
    audio_final_dir,
    channels_csv_path,
    logs_root,
    repo_root,
    thumbnails_root,
    video_capcut_local_drafts_root,
    video_input_root,
    video_runs_root,
    workspace_root,
)
from factory_common.timeline_manifest import parse_episode_id  # noqa: E402


REPORT_SCHEMA = "ytm.ops.archive_published_episodes_report.v2"


def _is_under(path: Path, root: Path) -> bool:
    try:
        # Do not resolve symlinks here; we want to validate the *path itself* lives under the domain root.
        path.absolute().relative_to(root.absolute())
        return True
    except Exception:
        return False


def _allowed_root_for_domain(domain: str) -> Optional[Path]:
    if domain == "audio":
        return audio_artifacts_root() / "final"
    if domain == "thumbnails":
        return thumbnails_root() / "assets"
    if domain == "video_input":
        return video_input_root()
    if domain == "video_runs":
        return video_runs_root()
    if domain == "capcut_drafts":
        return video_capcut_local_drafts_root()
    return None


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _move_path(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise SystemExit(f"archive destination already exists: {dest}")
    try:
        src.rename(dest)
        return
    except OSError:
        shutil.move(str(src), str(dest))


def _delete_path(path: Path) -> None:
    # Safety: never follow symlinks (unlink the link itself).
    if path.is_symlink():
        path.unlink()
        return
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def _normalize_channel(raw: str) -> str:
    ch = (raw or "").strip().upper()
    if not ch.startswith("CH") or len(ch) < 3:
        raise SystemExit(f"invalid --channel: {raw!r}")
    return ch


def _normalize_video(raw: str) -> str:
    token = (raw or "").strip()
    if not token:
        raise SystemExit("video is empty")
    digits = "".join(ch for ch in token if ch.isdigit())
    if not digits:
        raise SystemExit(f"invalid video: {raw!r}")
    return f"{int(digits):03d}"


def _parse_videos(values: Optional[Iterable[str]]) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for raw in values:
        if raw is None:
            continue
        for part in str(raw).replace(",", " ").split():
            part = part.strip()
            if part:
                out.append(_normalize_video(part))
    return sorted(set(out))


def _planning_published_videos(csv_path: Path) -> list[str]:
    if not csv_path.exists():
        return []
    out: list[str] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            progress = (row.get("進捗") or "").strip()
            if progress != "投稿済み":
                continue
            raw_no = (row.get("動画番号") or row.get("No.") or "").strip()
            if raw_no.isdigit():
                out.append(f"{int(raw_no):03d}")
                continue
            vid = (row.get("動画ID") or row.get("台本番号") or "").strip()
            m = re.search(r"\bCH\d{2}-(\d{3})\b", vid)
            if m:
                out.append(m.group(1))
    return sorted(set(out))


@dataclass(frozen=True)
class WorkItem:
    action: str  # "archive" | "delete"
    domain: str
    channel: str
    video: str
    src: Path
    dest: Optional[Path]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "domain": self.domain,
            "channel": self.channel,
            "video": self.video,
            "src": str(self.src),
            "dest": str(self.dest) if self.dest else None,
        }


def _thumbnail_dir_candidates(ch: str, video: str) -> list[Path]:
    base = thumbnails_root() / "assets" / ch
    if not base.exists():
        return []
    prefixes = {video}
    try:
        prefixes.add(str(int(video)))
    except Exception:
        pass

    out: list[Path] = []
    for p in sorted(base.iterdir()):
        if not p.is_dir():
            continue
        name = p.name
        if any(name == pref or name.startswith(pref + "_") for pref in prefixes):
            out.append(p)
    return out


def _video_input_candidates(ch: str, video: str) -> list[Path]:
    root = video_input_root()
    if not root.exists():
        return []
    candidates: list[Path] = []
    input_dirs = [p for p in sorted(root.iterdir()) if p.is_dir() and p.name.startswith(ch)]
    if not input_dirs:
        return []

    prefixes: list[str] = [f"{ch}-{video}"]
    try:
        n = str(int(video))
        prefixes.extend([video, n])
    except Exception:
        prefixes.append(video)

    for d in input_dirs:
        for entry in sorted(d.iterdir()):
            name = entry.name
            if name.startswith(prefixes[0]):
                candidates.append(entry)
                continue
            for pref in prefixes[1:]:
                if name.startswith(pref + ".") or name.startswith(pref + "_") or name.startswith(pref + "-"):
                    candidates.append(entry)
                    break
    # de-dupe while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for p in candidates:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def _capcut_draft_candidates(ch: str, video: str) -> list[Path]:
    root = video_capcut_local_drafts_root()
    if not root.exists():
        return []
    key = f"{ch}-{video}"
    out: list[Path] = []
    for entry in sorted(root.iterdir()):
        if key not in entry.name:
            continue
        out.append(entry)
    return out


def _run_episode_key(run_dir: Path) -> Optional[tuple[str, str]]:
    tm = run_dir / "timeline_manifest.json"
    if tm.exists():
        try:
            data = json.loads(tm.read_text(encoding="utf-8"))
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


def _video_run_candidates(ch: str, video: str) -> list[Path]:
    root = video_runs_root()
    if not root.exists():
        return []
    out: list[Path] = []
    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        key = _run_episode_key(run_dir)
        if not key:
            continue
        if key == (ch, video):
            out.append(run_dir)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive (or delete) artifacts for episodes with Planning 進捗=投稿済み.")
    ap.add_argument("--channel", action="append", required=True, help="Target channel (repeatable). e.g. CH01")
    ap.add_argument("--video", action="append", help="Limit to specific video(s) (repeatable). e.g. 216")
    ap.add_argument("--audio", action="store_true", help="Target domain: workspaces/audio/final/**")
    ap.add_argument("--thumbnails", action="store_true", help="Target domain: workspaces/thumbnails/assets/**")
    ap.add_argument("--video-input", action="store_true", help="Target domain: workspaces/video/input/**")
    ap.add_argument("--video-runs", action="store_true", help="Target domain: workspaces/video/runs/**")
    ap.add_argument("--capcut-drafts", action="store_true", help="Target domain: workspaces/video/_capcut_drafts/**")
    ap.add_argument("--dry-run", action="store_true", help="Dry-run (default).")
    ap.add_argument("--run", action="store_true", help="Actually move/delete artifacts.")
    ap.add_argument("--delete", action="store_true", help="Delete artifacts instead of moving them to _archive (dangerous).")
    ap.add_argument("--yes", action="store_true", help="Safety confirm for dangerous runs (e.g. batch, or --delete --run).")
    ap.add_argument("--stamp", default=None, help="Override timestamp folder name (default: now UTC).")
    ap.add_argument(
        "--ignore-created-by",
        default=None,
        help="Ignore locks created_by=<value> (advanced; prefer setting LLM_AGENT_NAME).",
    )
    args = ap.parse_args()

    channels = sorted({_normalize_channel(c) for c in (args.channel or [])})
    videos_filter = _parse_videos(args.video)

    requested_domains = {k for k in ("audio", "thumbnails", "video_input", "video_runs", "capcut_drafts") if getattr(args, k, False)}
    domains = requested_domains or {"audio", "thumbnails", "video_input", "video_runs", "capcut_drafts"}

    do_delete = bool(args.delete)
    if do_delete and not requested_domains:
        raise SystemExit("--delete requires explicit domain flags (e.g. --audio --video-input --video-runs).")

    do_run = bool(args.run) and not bool(args.dry_run)
    if do_run and do_delete and not args.yes:
        raise SystemExit("--delete --run requires --yes (safety).")
    if do_run and not args.yes:
        # Require explicit --yes when this could touch more than 1 episode.
        if len(channels) != 1 or not videos_filter or len(videos_filter) != 1:
            raise SystemExit("--run requires --yes for batch archiving (safety).")

    stamp = str(args.stamp or _utc_now_compact())

    ignore_created_by = (args.ignore_created_by or os.getenv("LLM_AGENT_NAME") or os.getenv("AGENT_NAME") or "").strip() or None
    locks = load_active_locks(ignore_created_by=ignore_created_by)

    action = "delete" if do_delete else "archive"
    items: list[WorkItem] = []
    missing: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for ch in channels:
        csv_path = channels_csv_path(ch)
        published = _planning_published_videos(csv_path)
        if videos_filter:
            published = [v for v in published if v in videos_filter]

        for vid in published:
            if "audio" in domains:
                src = audio_final_dir(ch, vid)
                if src.exists():
                    dest = audio_artifacts_root() / "_archive_audio" / stamp / ch / vid
                    items.append(WorkItem(action=action, domain="audio", channel=ch, video=vid, src=src, dest=None if do_delete else dest))
                else:
                    missing.append({"domain": "audio", "channel": ch, "video": vid, "src": str(src)})

            if "thumbnails" in domains:
                thumbs = _thumbnail_dir_candidates(ch, vid)
                if thumbs:
                    for src in thumbs:
                        dest = thumbnails_root() / "_archive" / stamp / ch / "assets" / src.name
                        items.append(
                            WorkItem(action=action, domain="thumbnails", channel=ch, video=vid, src=src, dest=None if do_delete else dest)
                        )
                else:
                    missing.append({"domain": "thumbnails", "channel": ch, "video": vid, "src": str(thumbnails_root() / "assets" / ch / vid)})

            if "video_input" in domains:
                entries = _video_input_candidates(ch, vid)
                if entries:
                    for src in entries:
                        # preserve channel dir name in archive for easy restore
                        try:
                            rel = src.relative_to(video_input_root())
                        except Exception:
                            rel = Path(ch) / src.name
                        dest = workspace_root() / "video" / "_archive" / stamp / ch / "video_input" / rel
                        items.append(
                            WorkItem(action=action, domain="video_input", channel=ch, video=vid, src=src, dest=None if do_delete else dest)
                        )
                else:
                    missing.append({"domain": "video_input", "channel": ch, "video": vid, "src": str(video_input_root())})

            if "video_runs" in domains:
                runs = _video_run_candidates(ch, vid)
                if runs:
                    for src in runs:
                        dest = workspace_root() / "video" / "_archive" / stamp / ch / "runs" / src.name
                        items.append(
                            WorkItem(action=action, domain="video_runs", channel=ch, video=vid, src=src, dest=None if do_delete else dest)
                        )
                else:
                    # not an error: many episodes have no runs on disk
                    missing.append({"domain": "video_runs", "channel": ch, "video": vid, "src": str(video_runs_root())})

            if "capcut_drafts" in domains:
                drafts = _capcut_draft_candidates(ch, vid)
                if drafts:
                    for src in drafts:
                        dest = workspace_root() / "video" / "_archive" / stamp / ch / "capcut_drafts" / src.name
                        items.append(
                            WorkItem(action=action, domain="capcut_drafts", channel=ch, video=vid, src=src, dest=None if do_delete else dest)
                        )
                else:
                    # not an error: some channels may not use local CapCut drafts
                    missing.append({"domain": "capcut_drafts", "channel": ch, "video": vid, "src": str(video_capcut_local_drafts_root())})

    moved: list[dict[str, Any]] = []

    for item in items:
        src = item.src
        dest = item.dest

        allowed_root = _allowed_root_for_domain(item.domain)
        scripts_root = workspace_root() / "scripts"
        if allowed_root and not _is_under(src, allowed_root):
            skipped.append({**item.as_dict(), "reason": "out_of_scope_src", "allowed_root": str(allowed_root)})
            continue
        if _is_under(src, scripts_root) or (dest and _is_under(dest, scripts_root)):
            skipped.append({**item.as_dict(), "reason": "blocked_by_scripts_sot_guard"})
            continue

        if not src.exists():
            skipped.append({**item.as_dict(), "reason": "missing_at_move_time"})
            continue

        blocker = find_blocking_lock(src, locks) or (dest and find_blocking_lock(dest, locks))
        if blocker:
            skipped.append(
                {
                    **item.as_dict(),
                    "reason": "blocked_by_lock",
                    "lock_id": blocker.lock_id,
                    "lock_scopes": list(blocker.scopes),
                }
            )
            continue

        record: dict[str, Any] = {**item.as_dict(), "executed": False}
        if do_run:
            if item.action == "delete":
                _delete_path(src)
            else:
                if not dest:
                    raise SystemExit("internal error: dest is required for archive action")
                _move_path(src, dest)
            record["executed"] = True
        moved.append(record)

    report_dir = logs_root() / "regression" / "archive_published_episodes"
    report_path = report_dir / f"archive_published_episodes_{stamp}.json"
    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": _utc_now_iso(),
        "dry_run": not do_run,
        "channels": channels,
        "videos_filter": videos_filter,
        "domains": sorted(domains),
        "archive_stamp": stamp,
        "lock_ignore_created_by": ignore_created_by,
        "counts": {
            "planned_moves": len(items),
            "executed_moves": sum(1 for r in moved if r.get("executed")),
            "skipped": len(skipped),
            "missing": len(missing),
        },
        "items": moved,
        "skipped": skipped,
        "missing": missing,
        "notes": {
            "sot": "planning csv progress == 投稿済み",
            "action": action,
            "archive_roots": {
                "audio": str(audio_artifacts_root() / "_archive_audio" / stamp),
                "thumbnails": str(thumbnails_root() / "_archive" / stamp),
                "video": str(workspace_root() / "video" / "_archive" / stamp),
            },
            "repo_root": str(repo_root()),
        },
        "tool": {"argv": [str(a) for a in sys.argv], "cwd": os.getcwd()},
    }
    _save_json(report_path, report)

    print(f"[archive_published_episodes] report: {report_path}")
    print(
        f"[archive_published_episodes] planned={report['counts']['planned_moves']} "
        f"executed={report['counts']['executed_moves']} "
        f"skipped={report['counts']['skipped']} "
        f"missing={report['counts']['missing']} "
        f"mode={'run' if do_run else 'dry-run'} "
        f"action={action}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
