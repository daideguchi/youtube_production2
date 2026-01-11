from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.main import (
    TextUpdateRequest,
    current_timestamp,
    ensure_expected_updated_at,
    load_status,
    normalize_channel_code,
    normalize_video_number,
    save_status,
    video_base_dir,
    write_text_with_lock,
)

router = APIRouter(prefix="/api", tags=["scripts"])


@router.put("/channels/{channel}/videos/{video}/assembled")
def update_assembled(channel: str, video: str, payload: TextUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    base_dir = video_base_dir(channel_code, video_number)
    content_dir = base_dir / "content"
    assembled = content_dir / "assembled.md"
    assembled_human = content_dir / "assembled_human.md"
    if assembled.parent.name != "content":
        raise HTTPException(status_code=400, detail="invalid assembled path")
    if assembled_human.parent.name != "content":
        raise HTTPException(status_code=400, detail="invalid assembled_human path")

    # If assembled_human exists, treat it as authoritative; always keep assembled.md mirrored.
    target = assembled_human if assembled_human.exists() else assembled
    write_text_with_lock(target, payload.content)
    if target != assembled:
        write_text_with_lock(assembled, payload.content)
    timestamp = current_timestamp()
    status["updated_at"] = timestamp
    # 台本リテイクは保存成功時に自動解除（ベストエフォート）
    meta = status.get("metadata") or {}
    meta["redo_script"] = False
    # Any A-text edit implies audio redo; previous audio review becomes stale.
    meta["redo_audio"] = True
    meta["audio_reviewed"] = False
    status["metadata"] = meta
    # Force re-validation to prevent downstream using stale script_validation.
    stages = status.get("stages")
    if not isinstance(stages, dict):
        stages = {}
        status["stages"] = stages
    sv = stages.get("script_validation")
    if not isinstance(sv, dict):
        sv = {"status": "pending", "details": {}}
        stages["script_validation"] = sv
    sv["status"] = "pending"
    details = sv.get("details")
    if not isinstance(details, dict):
        details = {}
    for key in ("error", "error_codes", "issues", "fix_hints", "llm_quality_gate"):
        details.pop(key, None)
    sv["details"] = details
    save_status(channel_code, video_number, status)
    return {"status": "ok", "updated_at": timestamp}

