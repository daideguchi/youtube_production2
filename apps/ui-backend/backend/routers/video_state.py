from __future__ import annotations

from fastapi import APIRouter

from backend.app.datetime_utils import current_timestamp, current_timestamp_compact
from backend.app.episode_store import load_status
from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.app.status_models import ReadyUpdateRequest, StageUpdateRequest, StatusUpdateRequest
from backend.main import ensure_expected_updated_at, save_status

router = APIRouter(prefix="/api", tags=["status"])


@router.put("/channels/{channel}/videos/{video}/status")
def update_status(channel: str, video: str, payload: StatusUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    timestamp = current_timestamp()
    status["status"] = payload.status
    status["updated_at"] = timestamp
    if payload.status.lower() == "completed":
        status.setdefault("completed_at", timestamp)
    else:
        status.pop("completed_at", None)
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": timestamp}


@router.put("/channels/{channel}/videos/{video}/stages")
def update_stages(channel: str, video: str, payload: StageUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    timestamp = current_timestamp()
    stages = status.setdefault("stages", {})
    for key, value in payload.stages.items():
        stage_entry = stages.setdefault(key, {})
        stage_entry["status"] = value.status
        stage_entry["updated_at"] = timestamp
    status["updated_at"] = timestamp
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": timestamp}


@router.put("/channels/{channel}/videos/{video}/ready")
def update_ready(channel: str, video: str, payload: ReadyUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    now_iso = current_timestamp()
    metadata = status.setdefault("metadata", {})
    metadata["ready_for_audio"] = payload.ready
    if payload.ready:
        metadata["ready_for_audio_at"] = current_timestamp_compact()
    else:
        metadata.pop("ready_for_audio_at", None)
    status["updated_at"] = now_iso
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": now_iso}
