from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.app.datetime_utils import current_timestamp
from backend.app.episode_store import load_status
from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.app.redo_models import RedoUpdateRequest, RedoUpdateResponse
from factory_common.publish_lock import is_episode_published_locked

router = APIRouter(prefix="/api", tags=["redo"])


@router.patch("/channels/{channel}/videos/{video}/redo", response_model=RedoUpdateResponse)
def update_video_redo(channel: str, video: str, payload: RedoUpdateRequest):
    from backend.main import save_status

    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    if is_episode_published_locked(channel_code, video_number):
        raise HTTPException(status_code=423, detail="投稿済みロック中のためリテイク設定は変更できません。")

    status = load_status(channel_code, video_number)
    meta = status.setdefault("metadata", {})

    redo_script: bool = payload.redo_script if payload.redo_script is not None else bool(meta.get("redo_script", True))
    redo_audio: bool = payload.redo_audio if payload.redo_audio is not None else bool(meta.get("redo_audio", True))
    redo_note: Optional[str] = payload.redo_note if payload.redo_note is not None else meta.get("redo_note")

    meta["redo_script"] = bool(redo_script)
    meta["redo_audio"] = bool(redo_audio)
    if redo_note is not None:
        meta["redo_note"] = redo_note
    status["metadata"] = meta
    updated_at = current_timestamp()
    status["updated_at"] = updated_at
    save_status(channel_code, video_number, status)

    return RedoUpdateResponse(
        status="ok",
        redo_script=bool(redo_script),
        redo_audio=bool(redo_audio),
        redo_note=redo_note,
        updated_at=updated_at,
    )
