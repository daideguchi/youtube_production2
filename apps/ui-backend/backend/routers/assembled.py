from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse

from backend.app.datetime_utils import current_timestamp
from backend.app.episode_store import load_status, video_base_dir
from backend.app.lock_store import write_text_with_lock
from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.app.script_text_utils import _resolve_a_text_display_path
from backend.app.scripts_models import TextUpdateRequest
from backend.app.status_store import save_status
from backend.app.status_models import ensure_expected_updated_at

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


@router.get("/channels/{channel}/videos/{video}/a-text", response_class=PlainTextResponse)
def get_a_text(channel: str, video: str):
    """
    Aテキスト（表示用原稿）を返す。優先順位:
    content/assembled_human.md -> content/assembled.md
    """
    channel_code = normalize_channel_code(channel)
    video_no = normalize_video_number(video)
    path = _resolve_a_text_display_path(channel_code, video_no)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="A-text not found") from exc
