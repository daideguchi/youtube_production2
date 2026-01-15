from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from backend.app.datetime_utils import current_timestamp
from backend.app.episode_store import load_status_optional
from backend.app.normalize import normalize_video_number
from backend.app.status_models import STAGE_ORDER
from backend.app.json_store import load_json, write_json
from factory_common.paths import script_data_root as ssot_script_data_root

DATA_ROOT = ssot_script_data_root()
PROGRESS_STATUS_PATH = DATA_ROOT / "_progress" / "processing_status.json"


def default_status_payload(channel_code: str, video_number: str) -> dict:
    return {
        "script_id": f"{channel_code}-{video_number}",
        "channel": channel_code,
        "status": "pending",
        "metadata": {},
        "stages": {stage: {"status": "pending", "details": {}} for stage in STAGE_ORDER},
    }


def save_status(
    channel_code: str,
    video_number: str,
    payload: dict,
    *,
    data_root: Path | None = None,
    progress_status_path: Path | None = None,
) -> None:
    """
    Save status.json and best-effort sync processing_status.json (same script_id).

    NOTE:
    - `data_root` / `progress_status_path` are injectable for tests.
    """
    root = DATA_ROOT if data_root is None else data_root
    progress_path = PROGRESS_STATUS_PATH if progress_status_path is None else progress_status_path

    status_path = root / channel_code / video_number / "status.json"
    write_json(status_path, payload)

    # 同じ script_id の processing_status.json も同期する
    if not progress_path.exists():
        return
    progress = load_json(progress_path)
    status_script_id = payload.get("script_id")
    if progress.get("script_id") != status_script_id:
        return

    # 特定のフィールドのみ更新
    progress.update(
        {
            "status": payload.get("status"),
            "stages": payload.get("stages", {}),
            "metadata": payload.get("metadata", {}),
            "updated_at": payload.get("updated_at"),
            "completed_at": payload.get("completed_at"),
        }
    )
    write_json(progress_path, progress)


def load_or_init_status(channel_code: str, video_number: str) -> dict:
    status = load_status_optional(channel_code, video_number)
    if status is not None:
        return status

    payload = default_status_payload(channel_code, video_number)

    # Best-effort: bootstrap title from planning CSV (if available).
    try:
        from script_pipeline.tools import planning_store

        for row in planning_store.get_rows(channel_code, force_refresh=True):
            if not row.video_number:
                continue
            if normalize_video_number(row.video_number) != video_number:
                continue
            title = row.raw.get("タイトル") if isinstance(row.raw, dict) else None
            if isinstance(title, str) and title.strip():
                meta = payload.setdefault("metadata", {})
                meta.setdefault("sheet_title", title.strip())
                meta.setdefault("title", title.strip())
                meta.setdefault("expected_title", title.strip())
            break
    except Exception:
        pass

    save_status(channel_code, video_number, payload)
    return payload


def append_audio_history_entry(channel_code: str, video_number: str, entry: dict) -> None:
    """
    Append a short history entry under `metadata.audio.history` in status.json.

    This is best-effort and never creates a new status.json.
    """
    status_path = DATA_ROOT / channel_code / video_number / "status.json"
    if not status_path.exists():
        return
    try:
        payload = load_json(status_path)
    except HTTPException:
        return

    timestamp = entry.get("timestamp") or current_timestamp()
    history_entry = dict(entry)
    history_entry["timestamp"] = timestamp

    metadata = payload.setdefault("metadata", {})
    audio_meta = metadata.setdefault("audio", {})
    history = audio_meta.setdefault("history", [])
    history.append(history_entry)
    if len(history) > 50:
        del history[:-50]

    write_json(status_path, payload)
