"""Workflow precheck helpers for the UI backend.

This module is intentionally lightweight and dependency-free (stdlib + factory_common)
so that it can be imported safely in a variety of environments.

It provides:
- `gather_pending`: summary of pending rows in planning CSVs
- `collect_ready_for_audio`: scripts that are safe to start audio synthesis for
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from factory_common.alignment import ALIGNMENT_SCHEMA, planning_hash_from_row, sha1_file
from factory_common.paths import audio_final_dir, planning_root, script_data_root


_VIDEO_DIR_RE = re.compile(r"^\d{1,3}$")


@dataclass(frozen=True)
class PendingSummary:
    channel: str
    count: int
    items: List[Dict[str, Any]]


@dataclass(frozen=True)
class ReadyEntry:
    channel: str
    video_number: str
    script_id: str
    audio_status: str


def _read_planning_rows(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            return [row for row in reader if row]
    except Exception:
        # Fail-soft: guard endpoint should not crash due to a single broken CSV.
        return []


def _row_video_number(row: Dict[str, Any]) -> Optional[str]:
    for key in ("動画番号", "video_number", "No.", "No"):
        raw = row.get(key)
        if raw not in (None, ""):
            return str(raw).strip().zfill(3)
    return None


def _row_channel_code(row: Dict[str, Any], fallback: str) -> str:
    for key in ("チャンネル", "channel", "channel_code"):
        raw = row.get(key)
        if raw not in (None, ""):
            return str(raw).strip().upper()
    return fallback.upper()


def _is_pending_row(row: Dict[str, Any]) -> bool:
    progress = str(row.get("進捗") or row.get("progress") or "").strip()
    if progress:
        return "pending" in progress.lower()
    flag = str(row.get("作成フラグ") or row.get("creation_flag") or row.get("flag") or "").strip()
    return bool(flag)


def gather_pending(channel_codes: Optional[List[Optional[str]]] = None, limit: int = 5) -> List[PendingSummary]:
    """Collect 'pending' rows from workspaces/planning/channels/*.csv."""
    limit = max(int(limit), 0)

    channels_dir = planning_root() / "channels"
    if channel_codes:
        normalized = sorted({str(ch).upper() for ch in channel_codes if ch})
        csv_paths = [(ch, channels_dir / f"{ch}.csv") for ch in normalized]
    else:
        csv_paths = []
        if channels_dir.is_dir():
            for path in sorted(channels_dir.glob("CH*.csv")):
                csv_paths.append((path.stem.upper(), path))

    summaries: List[PendingSummary] = []
    for channel_code, csv_path in csv_paths:
        rows = _read_planning_rows(csv_path)
        pending_rows = [row for row in rows if _is_pending_row(row)]
        count = len(pending_rows)
        summaries.append(
            PendingSummary(
                channel=channel_code,
                count=count,
                items=pending_rows[:limit] if limit else [],
            )
        )
    return summaries


def _iter_video_dirs(channel_dir: Path) -> Iterable[Path]:
    if not channel_dir.is_dir():
        return []
    for cand in sorted(channel_dir.iterdir()):
        if not cand.is_dir():
            continue
        if not _VIDEO_DIR_RE.match(cand.name):
            continue
        yield cand


def _choose_script_path(video_dir: Path) -> Optional[Path]:
    candidates = [
        video_dir / "content" / "assembled_human.md",
        video_dir / "content" / "assembled.md",
        # Legacy path (kept for backward-compat)
        video_dir / "content" / "final" / "assembled.md",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    return None


def _audio_outputs_exist(channel: str, video: str) -> bool:
    final_dir = audio_final_dir(channel, video)
    wav_path = final_dir / f"{channel}-{video}.wav"
    srt_path = final_dir / f"{channel}-{video}.srt"
    try:
        return wav_path.is_file() and wav_path.stat().st_size > 0 and srt_path.is_file() and srt_path.stat().st_size > 0
    except Exception:
        return False


def _load_status_payload(path: Path) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _stage_status(payload: Dict[str, Any], name: str) -> str:
    stages = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
    stage = stages.get(name) if isinstance(stages.get(name), dict) else {}
    status = stage.get("status")
    return str(status) if status not in (None, "") else "pending"


def _alignment_stamp(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    stamp = metadata.get("alignment")
    if not isinstance(stamp, dict):
        return None
    if stamp.get("schema") != ALIGNMENT_SCHEMA:
        return None
    return stamp


def _planning_row_by_video(channel: str) -> Dict[str, Dict[str, Any]]:
    path = planning_root() / "channels" / f"{channel}.csv"
    rows = _read_planning_rows(path)
    mapping: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        video = _row_video_number(row)
        if not video:
            continue
        mapping[video] = row
    return mapping


def _alignment_matches(
    *,
    stamp: Dict[str, Any],
    planning_row: Dict[str, Any],
    script_path: Path,
) -> bool:
    try:
        expected_planning_hash = str(stamp.get("planning_hash") or "")
        expected_script_hash = str(stamp.get("script_hash") or "")
        current_planning_hash = planning_hash_from_row(planning_row)
        current_script_hash = sha1_file(script_path)
        return current_planning_hash == expected_planning_hash and current_script_hash == expected_script_hash
    except Exception:
        return False


def collect_ready_for_audio(channel_code: Optional[str] = None) -> List[ReadyEntry]:
    """Return videos with validated + aligned scripts and no completed audio output yet."""
    root = script_data_root()
    if channel_code:
        channels = [str(channel_code).upper()]
    else:
        channels = sorted([p.name.upper() for p in root.glob("CH*") if p.is_dir()])

    ready: List[ReadyEntry] = []
    for ch in channels:
        channel_dir = root / ch
        planning_rows = _planning_row_by_video(ch)
        for video_dir in _iter_video_dirs(channel_dir):
            video = video_dir.name.zfill(3)
            status_path = video_dir / "status.json"
            payload = _load_status_payload(status_path)
            if not payload:
                continue

            script_ok = _stage_status(payload, "script_validation") == "completed" or payload.get("status") == "script_validated"
            if not script_ok:
                continue

            audio_stage = _stage_status(payload, "audio_synthesis")
            if audio_stage == "completed" or _audio_outputs_exist(ch, video):
                continue

            stamp = _alignment_stamp(payload)
            if not stamp:
                continue
            planning_row = planning_rows.get(video)
            if not planning_row:
                continue
            script_path = _choose_script_path(video_dir)
            if not script_path:
                continue
            if not _alignment_matches(stamp=stamp, planning_row=planning_row, script_path=script_path):
                continue

            script_id = str(payload.get("script_id") or f"{ch}-{video}")
            ready.append(
                ReadyEntry(
                    channel=ch,
                    video_number=video,
                    script_id=script_id,
                    audio_status=audio_stage,
                )
            )

    ready.sort(key=lambda e: (e.channel, e.video_number))
    return ready


def check() -> dict:
    """Back-compat noop health check."""
    return {"status": "ok", "issues": []}
