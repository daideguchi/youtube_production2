from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from factory_common.paths import (
    audio_final_dir,
    channels_csv_path,
    script_data_root,
    status_path,
    video_runs_root,
)
from factory_common.timeline_manifest import parse_episode_id


EPISODE_PROGRESS_VIEW_SCHEMA = "ytm.episode_progress_view.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _norm_channel(value: str) -> str:
    ch = (value or "").strip().upper()
    if not ch:
        raise ValueError("channel is required")
    return ch


def _norm_video(value: str) -> str:
    token = (value or "").strip()
    digits = "".join(ch for ch in token if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {value}")
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


def _is_published_progress(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip()
    if not text:
        return False
    if "投稿済み" in text:
        return True
    if "公開済み" in text:
        return True
    if text.lower() in {"published", "posted"}:
        return True
    return False


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _planning_video_token(row: dict[str, str], *, channel: str) -> Optional[str]:
    for key in ("動画番号", "No.", "VideoNumber", "video_number", "video", "Video"):
        raw = (row.get(key) or "").strip()
        if raw:
            try:
                return _norm_video(raw)
            except Exception:
                return None
    for key in ("動画ID", "台本番号", "ScriptID", "script_id"):
        raw = (row.get(key) or "").strip()
        ep = parse_episode_id(raw)
        if ep and ep.channel == channel:
            return ep.video
    return None


def _read_planning_rows(channel: str) -> list[dict[str, str]]:
    path = channels_csv_path(channel)
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return list(reader)
    except Exception:
        return []


def _index_planning_rows(channel: str) -> tuple[dict[str, dict[str, str]], list[str]]:
    rows = _read_planning_rows(channel)
    by_video: dict[str, dict[str, str]] = {}
    dupes: list[str] = []
    for row in rows:
        token = _planning_video_token(row, channel=channel)
        if not token:
            continue
        if token in by_video:
            dupes.append(token)
            continue
        by_video[token] = row
    return by_video, sorted(set(dupes))


def _iter_script_videos(channel: str) -> list[str]:
    root = script_data_root() / channel
    if not root.exists():
        return []
    out: list[str] = []
    for p in sorted(root.iterdir()):
        if not p.is_dir():
            continue
        if not p.name.isdigit():
            continue
        out.append(p.name.zfill(3))
    return sorted(set(out))


def _derive_script_status(payload: dict[str, Any]) -> str:
    if not payload:
        return "missing"
    stages = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
    stage_statuses: list[str] = []
    if isinstance(stages, dict):
        for info in stages.values():
            if isinstance(info, dict):
                stage_statuses.append(str(info.get("status") or "").strip())

    if any(s == "failed" for s in stage_statuses):
        return "failed"
    if any(s == "processing" for s in stage_statuses):
        return "processing"

    overall = str(payload.get("status") or "").strip()
    if overall:
        return overall

    if stage_statuses and all(s == "completed" for s in stage_statuses):
        return "completed"
    if any(s == "completed" for s in stage_statuses):
        return "in_progress"
    if stage_statuses and all(s == "pending" for s in stage_statuses):
        return "pending"
    return "unknown"


def _summarize_key_stages(payload: dict[str, Any]) -> dict[str, str]:
    stages = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
    out: dict[str, str] = {}
    for k in ("topic_research", "outline", "draft", "script_validation", "audio_synthesis"):
        info = stages.get(k) if isinstance(stages, dict) else None
        if isinstance(info, dict):
            out[k] = str(info.get("status") or "pending")
    return out


def _capcut_draft_status(capcut_path: Path) -> tuple[str, Optional[str], bool]:
    """
    Return (status, link_target, target_exists).
    status: missing | broken | ok
    """
    if capcut_path.is_symlink():
        try:
            target = os.readlink(capcut_path)
        except OSError:
            target = None
        target_exists = False
        if target:
            tp = Path(target).expanduser()
            if not tp.is_absolute():
                tp = (capcut_path.parent / tp).resolve()
            target_exists = tp.exists()
        return ("ok" if target_exists else "broken"), target, target_exists

    if capcut_path.exists():
        return "ok", None, True

    return "missing", None, False


@dataclass(frozen=True)
class RunCandidate:
    run_id: str
    run_dir: Path
    has_timeline_manifest: bool
    capcut_draft_status: str  # missing | broken | ok
    capcut_draft_target: Optional[str]
    capcut_draft_target_exists: bool
    capcut_draft_info_mtime: float
    mtime: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "has_timeline_manifest": self.has_timeline_manifest,
            "capcut_draft_status": self.capcut_draft_status,
            "capcut_draft_target": self.capcut_draft_target,
            "capcut_draft_target_exists": self.capcut_draft_target_exists,
            "capcut_draft_info_mtime": self.capcut_draft_info_mtime,
            "mtime": self.mtime,
        }


def _resolve_episode_from_timeline_manifest(run_dir: Path) -> Optional[tuple[str, str]]:
    tm = run_dir / "timeline_manifest.json"
    if not tm.exists():
        return None
    data = _safe_read_json(tm)
    ep_raw = data.get("episode") if isinstance(data.get("episode"), dict) else {}
    if isinstance(ep_raw, dict):
        ep_id = str(ep_raw.get("id") or "").strip()
        ep = parse_episode_id(ep_id)
        if ep:
            return ep.channel, ep.video
        ep2 = parse_episode_id(f"{ep_raw.get('channel','')}-{ep_raw.get('video','')}")
        if ep2:
            return ep2.channel, ep2.video
    return None


def index_run_candidates(
    channel: str,
    *,
    videos: Optional[Iterable[str]] = None,
    include_hidden_runs: bool = False,
) -> dict[str, list[RunCandidate]]:
    ch = _norm_channel(channel)
    wanted = set(_parse_videos(videos))
    out: dict[str, list[RunCandidate]] = {}
    root = video_runs_root()
    if not root.exists():
        return out

    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        if not include_hidden_runs and run_dir.name.startswith(("_", ".")):
            continue

        ep = parse_episode_id(run_dir.name)
        if ep:
            ch2, vid2 = ep.channel, ep.video
        else:
            resolved = _resolve_episode_from_timeline_manifest(run_dir)
            if not resolved:
                continue
            ch2, vid2 = resolved

        if ch2 != ch:
            continue
        if wanted and vid2 not in wanted:
            continue

        tm = run_dir / "timeline_manifest.json"
        capcut_path = run_dir / "capcut_draft"
        capcut_status, capcut_target, capcut_exists = _capcut_draft_status(capcut_path)
        draft_info = run_dir / "capcut_draft_info.json"
        out.setdefault(vid2, []).append(
            RunCandidate(
                run_id=run_dir.name,
                run_dir=run_dir,
                has_timeline_manifest=tm.exists(),
                capcut_draft_status=capcut_status,
                capcut_draft_target=capcut_target,
                capcut_draft_target_exists=capcut_exists,
                capcut_draft_info_mtime=draft_info.stat().st_mtime if draft_info.exists() else 0.0,
                mtime=run_dir.stat().st_mtime,
            )
        )

    for vid, items in out.items():
        items.sort(
            key=lambda c: (
                c.capcut_draft_status == "ok",
                c.capcut_draft_status != "missing",
                c.capcut_draft_info_mtime,
                c.mtime,
                c.run_id,
            ),
            reverse=True,
        )
    return out


def build_episode_progress_view(
    channel: str,
    *,
    videos: Optional[Iterable[str]] = None,
    include_unplanned: bool = False,
    include_hidden_runs: bool = False,
) -> dict[str, Any]:
    """
    Build a derived, read-only progress view per episode.

    This does NOT modify any SoT. It only aggregates from:
      - Planning CSV
      - status.json
      - audio final
      - video runs
    """
    ch = _norm_channel(channel)
    requested = _parse_videos(videos)

    planning_by_video, planning_dupes = _index_planning_rows(ch)
    planning_videos = sorted(planning_by_video.keys())
    script_videos = _iter_script_videos(ch)

    if requested:
        target_videos = requested
    else:
        if include_unplanned:
            target_videos = sorted(set(planning_videos) | set(script_videos))
        else:
            target_videos = planning_videos or script_videos

    runs_by_video = index_run_candidates(ch, videos=target_videos, include_hidden_runs=include_hidden_runs)

    episodes: list[dict[str, Any]] = []
    for vid in target_videos:
        episode_id = f"{ch}-{vid}"
        row = planning_by_video.get(vid)
        planning_progress = str((row or {}).get("進捗") or "").strip()

        status_p = status_path(ch, vid)
        script_dir_exists = status_p.parent.exists()
        status_exists = status_p.exists()
        status_payload = _safe_read_json(status_p) if status_exists else {}
        meta = status_payload.get("metadata") if isinstance(status_payload.get("metadata"), dict) else {}
        if not isinstance(meta, dict):
            meta = {}

        script_status = _derive_script_status(status_payload) if status_exists else "missing"
        key_stages = _summarize_key_stages(status_payload) if status_exists else {}

        audio_dir = audio_final_dir(ch, vid)
        wav = audio_dir / f"{episode_id}.wav"
        srt = audio_dir / f"{episode_id}.srt"
        audio_ready = wav.exists() and srt.exists()

        published_locked = bool(meta.get("published_lock")) or _is_published_progress(planning_progress)

        selected_run_id = meta.get("video_run_id")
        if selected_run_id is not None:
            selected_run_id = str(selected_run_id).strip() or None

        run_candidates = runs_by_video.get(vid) or []
        selected_run_exists = None
        selected_run_capcut_status = None
        selected_run_capcut_target = None
        selected_run_capcut_target_exists = None

        if selected_run_id:
            selected_run_dir = video_runs_root() / selected_run_id
            selected_run_exists = selected_run_dir.exists()
            capcut_status, capcut_target, capcut_target_exists = _capcut_draft_status(selected_run_dir / "capcut_draft")
            selected_run_capcut_status = capcut_status
            selected_run_capcut_target = capcut_target
            selected_run_capcut_target_exists = capcut_target_exists

        best_candidate = run_candidates[0] if run_candidates else None

        issues: list[str] = []
        if planning_dupes and vid in planning_dupes:
            issues.append("planning_duplicate_video_rows")
        if status_exists and planning_progress and "pending" in planning_progress and script_status in {"completed", "processing", "in_progress"}:
            issues.append("planning_stale_vs_status")
        if not status_exists and (script_dir_exists or audio_ready or bool(run_candidates)):
            issues.append("status_json_missing")
        if status_exists and not selected_run_id and run_candidates:
            issues.append("video_run_unselected")
        if selected_run_id and selected_run_exists is False:
            issues.append("video_run_missing")
        if selected_run_capcut_status == "broken":
            issues.append("capcut_draft_broken")
        if selected_run_id and selected_run_capcut_status == "missing":
            issues.append("capcut_draft_missing")

        # UI-friendly view: if run is not selected, still expose best candidate.
        capcut_effective_status = None
        capcut_effective_target = None
        capcut_effective_target_exists = None
        capcut_effective_run_id = None
        if selected_run_id:
            capcut_effective_run_id = selected_run_id
            capcut_effective_status = selected_run_capcut_status
            capcut_effective_target = selected_run_capcut_target
            capcut_effective_target_exists = selected_run_capcut_target_exists
        elif best_candidate:
            capcut_effective_run_id = best_candidate.run_id
            capcut_effective_status = best_candidate.capcut_draft_status
            capcut_effective_target = best_candidate.capcut_draft_target
            capcut_effective_target_exists = best_candidate.capcut_draft_target_exists
        else:
            capcut_effective_status = "missing"

        episodes.append(
            {
                "video": vid,
                "episode_id": episode_id,
                "published_locked": published_locked,
                "planning_progress": planning_progress,
                "script_status": script_status,
                "script_key_stages": key_stages,
                "audio_ready": audio_ready,
                "audio_wav_path": str(wav) if wav.exists() else None,
                "audio_srt_path": str(srt) if srt.exists() else None,
                "video_run_id": selected_run_id,
                "video_run_exists": selected_run_exists,
                "video_run_candidates": [c.run_id for c in run_candidates],
                "capcut_draft_status": capcut_effective_status,
                "capcut_draft_target": capcut_effective_target,
                "capcut_draft_target_exists": capcut_effective_target_exists,
                "capcut_draft_run_id": capcut_effective_run_id,
                "issues": issues,
            }
        )

    issues_summary: dict[str, int] = {}
    episodes_with_issues = 0
    episodes_published = 0
    for ep in episodes:
        if ep.get("published_locked"):
            episodes_published += 1
        tokens = ep.get("issues") or []
        if tokens:
            episodes_with_issues += 1
        for issue in tokens:
            token = str(issue or "").strip()
            if not token:
                continue
            issues_summary[token] = issues_summary.get(token, 0) + 1

    return {
        "schema": EPISODE_PROGRESS_VIEW_SCHEMA,
        "generated_at": _utc_now_iso(),
        "channel": ch,
        "planning_csv_path": str(channels_csv_path(ch)),
        "planning_duplicate_videos": planning_dupes,
        "episodes_total": len(episodes),
        "episodes_published": episodes_published,
        "episodes_with_issues": episodes_with_issues,
        "issues_summary": dict(sorted(issues_summary.items(), key=lambda kv: (-kv[1], kv[0]))),
        "episodes": episodes,
    }
