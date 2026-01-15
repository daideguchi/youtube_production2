from __future__ import annotations

from fastapi import APIRouter

from backend.app.datetime_utils import current_timestamp
from backend.app.episode_store import load_status
from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.app.status_store import save_status
from backend.app.thumbnails_models import ThumbnailOverrideRequest, ThumbnailOverrideResponse

router = APIRouter(prefix="/api", tags=["thumbnails"])


@router.patch("/channels/{channel}/videos/{video}/thumbnail", response_model=ThumbnailOverrideResponse)
def update_video_thumbnail_override(channel: str, video: str, payload: ThumbnailOverrideRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    meta = status.setdefault("metadata", {})

    meta["thumbnail_url_override"] = payload.thumbnail_url
    if payload.thumbnail_path is not None:
        meta["thumbnail_path_override"] = payload.thumbnail_path

    status["metadata"] = meta
    updated_at = current_timestamp()
    status["updated_at"] = updated_at
    save_status(channel_code, video_number, status)

    return ThumbnailOverrideResponse(
        status="ok",
        thumbnail_url=payload.thumbnail_url,
        thumbnail_path=payload.thumbnail_path,
        updated_at=updated_at,
    )
