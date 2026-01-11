from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from backend.main import (
    ScriptTextResponse,
    audio_final_dir,
    normalize_channel_code,
    normalize_video_number,
    resolve_text_file,
    safe_relative_path,
    video_base_dir,
)

router = APIRouter(prefix="/api", tags=["tts"])


@router.get("/channels/{channel}/videos/{video}/tts/plain", response_model=ScriptTextResponse)
def get_tts_plain_text(channel: str, video: str) -> ScriptTextResponse:
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    base_dir = video_base_dir(channel_code, video_number)
    tts_path = base_dir / "audio_prep" / "script_sanitized.txt"
    final_snapshot = audio_final_dir(channel_code, video_number) / "a_text.txt"
    if not tts_path.exists() and final_snapshot.exists():
        tts_path = final_snapshot
    if not tts_path.exists():
        raise HTTPException(status_code=404, detail="TTS input text not found (script_sanitized.txt / a_text.txt)")
    content = resolve_text_file(tts_path) or ""
    updated_at = None
    try:
        updated_at = datetime.fromtimestamp(tts_path.stat().st_mtime, timezone.utc).isoformat().replace("+00:00", "Z")
    except OSError:
        updated_at = None
    return ScriptTextResponse(
        path=safe_relative_path(tts_path),
        content=content,
        updated_at=updated_at,
    )

