from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from backend.app.scripts_models import HumanScriptResponse, HumanScriptUpdateRequest
from backend.app.path_utils import safe_relative_path
from backend.app.status_models import ensure_expected_updated_at

router = APIRouter(prefix="/api", tags=["scripts"])


@router.get("/channels/{channel}/videos/{video}/scripts/human", response_model=HumanScriptResponse)
def get_human_scripts(channel: str, video: str) -> HumanScriptResponse:
    from backend.main import (
        _default_status_payload,
        load_status_optional,
        normalize_channel_code,
        normalize_video_number,
        resolve_text_file,
        video_base_dir,
    )

    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status_optional(channel_code, video_number) or _default_status_payload(channel_code, video_number)
    metadata = status.get("metadata") or {}
    base_dir = video_base_dir(channel_code, video_number)
    content_dir = base_dir / "content"

    assembled = content_dir / "assembled.md"
    assembled_human = content_dir / "assembled_human.md"
    script_audio = content_dir / "script_audio.txt"
    script_audio_human = content_dir / "script_audio_human.txt"
    warnings: List[str] = []
    if not (base_dir / "status.json").exists():
        warnings.append(f"status.json missing for {channel_code}-{video_number}")
    b_with_pauses = base_dir / "audio_prep" / "b_text_with_pauses.txt"
    if not b_with_pauses.exists():
        warnings.append(f"b_text_with_pauses.txt missing for {channel_code}-{video_number}")

    tts_plain_path = base_dir / "audio_prep" / "script_sanitized.txt"
    if not tts_plain_path.exists():
        warnings.append(f"script_sanitized.txt missing for {channel_code}-{video_number}")
    plain_tts = resolve_text_file(tts_plain_path) or ""

    assembled_content = resolve_text_file(assembled) or ""
    if not assembled_content:
        assembled_content = plain_tts
    assembled_human_content = resolve_text_file(assembled_human) or ""
    if not assembled_human_content:
        assembled_human_content = assembled_content

    return HumanScriptResponse(
        assembled_path=safe_relative_path(assembled) if assembled.exists() else None,
        assembled_content=assembled_content,
        assembled_human_path=safe_relative_path(assembled_human) if assembled_human.exists() else None,
        assembled_human_content=assembled_human_content,
        script_audio_path=safe_relative_path(script_audio) if script_audio.exists() else None,
        script_audio_content=resolve_text_file(script_audio),
        script_audio_human_path=(
            safe_relative_path(script_audio_human)
            if script_audio_human.exists()
            else (safe_relative_path(tts_plain_path) if tts_plain_path.exists() else None)
        ),
        # Bテキストは「ttsが読み上げる文章」（script_sanitized）を返す。人手の上書きがあればそちらを優先。
        script_audio_human_content=resolve_text_file(script_audio_human) or plain_tts,
        audio_reviewed=bool(metadata.get("audio_reviewed", False)),
        updated_at=status.get("updated_at"),
        warnings=warnings,
    )


@router.put("/channels/{channel}/videos/{video}/scripts/human")
def update_human_scripts(channel: str, video: str, payload: HumanScriptUpdateRequest) -> Dict[str, Any]:
    from backend.main import (
        current_timestamp,
        load_or_init_status,
        normalize_channel_code,
        normalize_video_number,
        save_status,
        video_base_dir,
        write_text_with_lock,
    )

    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_or_init_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    base_dir = video_base_dir(channel_code, video_number)
    content_dir = base_dir / "content"
    audio_prep_dir = base_dir / "audio_prep"

    timestamp = current_timestamp()
    touched_a_text = False
    touched_b_text = False

    if payload.assembled_human is not None:
        target = content_dir / "assembled_human.md"
        if target.parent.name != "content":
            raise HTTPException(status_code=400, detail="invalid assembled_human path")
        write_text_with_lock(target, payload.assembled_human)
        # Keep the canonical A-text mirrors consistent (SSOT: assembled_human.md is authoritative).
        mirror = content_dir / "assembled.md"
        if mirror.parent.name != "content":
            raise HTTPException(status_code=400, detail="invalid assembled mirror path")
        write_text_with_lock(mirror, payload.assembled_human)
        touched_a_text = True
    if payload.script_audio_human is not None:
        target = content_dir / "script_audio_human.txt"
        if target.parent.name != "content":
            raise HTTPException(status_code=400, detail="invalid script_audio_human path")
        write_text_with_lock(target, payload.script_audio_human)
        # Mirror B-text into audio_prep/script_sanitized.txt so regeneration + UI preview use the same source of truth.
        audio_prep_dir.mkdir(parents=True, exist_ok=True)
        prep_plain = audio_prep_dir / "script_sanitized.txt"
        if prep_plain.parent.name != "audio_prep":
            raise HTTPException(status_code=400, detail="invalid script_sanitized path")
        write_text_with_lock(prep_plain, payload.script_audio_human)
        touched_b_text = True
    if payload.audio_reviewed is not None:
        metadata = status.setdefault("metadata", {})
        metadata["audio_reviewed"] = bool(payload.audio_reviewed)

    # If the human-edited script changes, downstream must be revalidated/regenerated.
    if touched_a_text or touched_b_text:
        meta = status.setdefault("metadata", {})
        if touched_a_text:
            # Script has been edited by a human; treat script redo as completed.
            meta["redo_script"] = False
            # Any script edit implies audio redo.
            meta["redo_audio"] = True
            meta["audio_reviewed"] = False
            # Force re-validation for safety (prevents TTS using stale script_validation).
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
        else:
            # B-text edit still requires audio redo.
            meta["redo_audio"] = True
            meta["audio_reviewed"] = False
        status["metadata"] = meta

    status["updated_at"] = timestamp
    save_status(channel_code, video_number, status)
    return {
        "status": "ok",
        "updated_at": timestamp,
        "audio_reviewed": status.get("metadata", {}).get("audio_reviewed", False),
    }
