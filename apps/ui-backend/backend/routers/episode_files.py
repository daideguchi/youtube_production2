from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from backend.app.episode_store import (
    load_status,
    resolve_audio_path,
    resolve_log_path,
    resolve_srt_path,
    video_base_dir,
)
from backend.app.normalize import normalize_channel_code, normalize_video_number

router = APIRouter(prefix="/api", tags=["episode-files"])


@router.get("/channels/{channel}/videos/{video}/audio")
def get_audio(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)

    status = load_status(channel_code, video_number)
    audio_path = resolve_audio_path(status, video_base_dir(channel_code, video_number))
    if not audio_path:
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(audio_path, media_type="audio/wav", filename=audio_path.name)


@router.get("/channels/{channel}/videos/{video}/srt")
def get_srt(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)

    status = load_status(channel_code, video_number)
    srt_path = resolve_srt_path(status, video_base_dir(channel_code, video_number))
    if not srt_path:
        raise HTTPException(status_code=404, detail="SRT not found")
    return FileResponse(srt_path, media_type="text/plain", filename=srt_path.name)


@router.get("/channels/{channel}/videos/{video}/log")
def get_audio_log(channel: str, video: str):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)

    status = load_status(channel_code, video_number)
    log_path = resolve_log_path(status, video_base_dir(channel_code, video_number))
    if not log_path:
        raise HTTPException(status_code=404, detail="Log not found")
    return FileResponse(log_path, media_type="application/json", filename=log_path.name)
