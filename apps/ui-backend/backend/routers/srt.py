from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.app.datetime_utils import current_timestamp
from backend.app.episode_store import load_status, resolve_audio_path, resolve_srt_path, video_base_dir
from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.app.scripts_models import TextUpdateRequest
from backend.main import SRTVerifyResponse, ensure_expected_updated_at, safe_relative_path, save_status, verify_srt_file
from backend.main import write_text_with_lock

router = APIRouter(prefix="/api", tags=["srt"])


@router.put("/channels/{channel}/videos/{video}/srt")
def update_srt(channel: str, video: str, payload: TextUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    base_dir = video_base_dir(channel_code, video_number)
    srt_path = resolve_srt_path(status, base_dir)
    if not srt_path:
        raise HTTPException(status_code=404, detail="SRT file not found")
    write_text_with_lock(srt_path, payload.content)
    timestamp = current_timestamp()
    status["updated_at"] = timestamp
    metadata = status.setdefault("metadata", {})
    audio_meta = metadata.setdefault("audio", {})
    synthesis_meta = audio_meta.setdefault("synthesis", {})
    synthesis_meta["final_srt"] = safe_relative_path(srt_path) or str(srt_path)
    synthesis_meta["updated_at"] = timestamp
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": timestamp}


@router.post(
    "/channels/{channel}/videos/{video}/srt/verify",
    response_model=SRTVerifyResponse,
)
def verify_srt(
    channel: str,
    video: str,
    tolerance_ms: int = Query(50, ge=0, le=2000),
):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    base_dir = video_base_dir(channel_code, video_number)
    wav_path = resolve_audio_path(status, base_dir)
    if not wav_path:
        raise HTTPException(
            status_code=404,
            detail="音声ファイルが見つかりません。Stage10 を完了してください。",
        )
    srt_path = resolve_srt_path(status, base_dir)
    if not srt_path:
        raise HTTPException(
            status_code=404,
            detail="SRT ファイルが見つかりません。Stage11 を確認してください。",
        )
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail=f"WAV not found: {wav_path}")
    if not srt_path.exists():
        raise HTTPException(status_code=404, detail=f"SRT not found: {srt_path}")
    return verify_srt_file(wav_path, srt_path, tolerance_ms=tolerance_ms)
