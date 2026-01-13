from __future__ import annotations

from pathlib import Path
from typing import Dict

from fastapi import APIRouter, HTTPException

from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.main import (
    PROJECT_ROOT,
    VideoCreateRequest,
    VideoFileReferences,
    build_status_payload,
    safe_relative_path,
    save_status,
    video_base_dir,
)

router = APIRouter(prefix="/api/channels", tags=["channels"])


@router.post("/{channel}/videos", status_code=201)
def register_video(channel: str, payload: VideoCreateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(payload.video)
    base_dir = video_base_dir(channel_code, video_number)
    status_path = base_dir / "status.json"
    if status_path.exists():
        raise HTTPException(status_code=409, detail="既に status.json が存在します。")

    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "content").mkdir(parents=True, exist_ok=True)
    (base_dir / "audio_prep").mkdir(parents=True, exist_ok=True)

    files_dict: Dict[str, str] = {}
    if payload.files:
        files_dict = payload.files.model_dump(exclude_none=True)

    # デフォルトのファイルを用意
    assembled_path = files_dict.get("assembled")
    if not assembled_path:
        default_assembled = base_dir / "content" / "assembled.md"
        if not default_assembled.exists():
            default_assembled.write_text("", encoding="utf-8")
        files_dict["assembled"] = str(default_assembled.relative_to(PROJECT_ROOT))
    else:
        assembled_file = Path(assembled_path)
        if not assembled_file.is_absolute():
            assembled_file = (PROJECT_ROOT / assembled_path).resolve()
        assembled_file.parent.mkdir(parents=True, exist_ok=True)
        if not assembled_file.exists():
            assembled_file.write_text("", encoding="utf-8")
        files_dict["assembled"] = str(safe_relative_path(assembled_file) or assembled_file)

    tts_path = files_dict.get("tts")
    if not tts_path:
        default_tts = base_dir / "audio_prep" / "script_sanitized.txt"
        if not default_tts.exists():
            default_tts.write_text("", encoding="utf-8")
        files_dict["tts"] = str(default_tts.relative_to(PROJECT_ROOT))
    else:
        tts_file = Path(tts_path)
        if not tts_file.is_absolute():
            tts_file = (PROJECT_ROOT / tts_path).resolve()
        tts_file.parent.mkdir(parents=True, exist_ok=True)
        if not tts_file.exists():
            tts_file.write_text("", encoding="utf-8")
        files_dict["tts"] = str(safe_relative_path(tts_file) or tts_file)

    file_refs = VideoFileReferences.model_validate(files_dict)

    status_payload = build_status_payload(
        channel_code=channel_code,
        video_number=video_number,
        script_id=payload.script_id,
        title=payload.title,
        initial_stage=payload.initial_stage,
        status_value=payload.status,
        metadata_patch=payload.metadata,
        generation=payload.generation,
        files=file_refs,
    )

    save_status(channel_code, video_number, status_payload)

    return {
        "status": "ok",
        "channel": channel_code,
        "video": video_number,
        "updated_at": status_payload["updated_at"],
    }

