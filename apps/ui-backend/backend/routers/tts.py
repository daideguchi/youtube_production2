from __future__ import annotations

import difflib
import logging
from typing import Any, Dict, List, Optional, Sequence

from fastapi import APIRouter, HTTPException

from backend.app.datetime_utils import current_timestamp
from backend.app.episode_store import load_status, resolve_text_file, video_base_dir
from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.app.path_utils import PROJECT_ROOT
from backend.app.tts_models import (
    TTSValidateRequest,
    TTSValidateResponse,
    TtsReplaceRequest,
    TtsReplaceResponse,
    TtsUpdateRequest,
)
from backend.app.tts_content_analyzer import analyze_tts_content
from backend.app.status_models import ensure_expected_updated_at
from backend.app.status_store import save_status
from backend.app.tts_tagged_text import _compose_tagged_tts, _parse_tagged_tts
from backend.core.tools.audio_manager import AudioManager

router = APIRouter(prefix="/api", tags=["tts"])

logger = logging.getLogger(__name__)


# Late-binding helpers defined in backend.main (avoid module-level import/circular deps).
def replace_text(*args: Any, **kwargs: Any):
    from backend.main import replace_text as impl

    return impl(*args, **kwargs)


def _persist_tts_variants(*args: Any, **kwargs: Any):
    from backend.main import _persist_tts_variants as impl

    return impl(*args, **kwargs)


def append_audio_history_entry(*args: Any, **kwargs: Any):
    from backend.main import append_audio_history_entry as impl

    return impl(*args, **kwargs)


@router.post("/channels/{channel}/videos/{video}/tts/replace", response_model=TtsReplaceResponse)
def replace_tts_segment(channel: str, video: str, payload: TtsReplaceRequest) -> TtsReplaceResponse:
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)

    base_dir = video_base_dir(channel_code, video_number)
    # 正規パスのみ許可（フォールバック禁止）
    plain_path = base_dir / "audio_prep" / "script_sanitized.txt"
    tagged_path = base_dir / "audio_prep" / "script_sanitized_with_pauses.txt"

    if not plain_path.exists():
        raise HTTPException(status_code=404, detail="script_sanitized.txt not found")

    old_plain = resolve_text_file(plain_path) or ""
    old_tagged = resolve_text_file(tagged_path)

    new_plain, replaced = replace_text(old_plain, payload.original, payload.replacement, payload.scope)
    if replaced == 0:
        raise HTTPException(status_code=400, detail="指定した文字列は音声用テキスト内に見つかりません。")

    timestamp = current_timestamp()
    status["updated_at"] = timestamp

    metadata = status.setdefault("metadata", {})
    audio_meta_raw = metadata.get("audio")
    if not isinstance(audio_meta_raw, dict):
        audio_meta_raw = {}
        metadata["audio"] = audio_meta_raw
    audio_meta = audio_meta_raw
    pause_map_meta = audio_meta.get("pause_map") if isinstance(audio_meta.get("pause_map"), list) else []
    derived_pause_map: List[Dict[str, Any]] = []
    if old_tagged:
        _, derived_pause_map, _ = _parse_tagged_tts(old_tagged)
    effective_pause_map: List[Dict[str, Any]] = list(pause_map_meta) if pause_map_meta else list(derived_pause_map)

    silence_plan: Optional[Sequence[float]] = None
    synthesis_meta = audio_meta.get("synthesis")
    if isinstance(synthesis_meta, dict):
        plan_candidate = synthesis_meta.get("silence_plan")
        if isinstance(plan_candidate, list):
            silence_plan = plan_candidate

    tagged_source: Optional[str] = None
    if old_tagged:
        updated_tagged, tagged_replaced = replace_text(old_tagged, payload.original, payload.replacement, payload.scope)
        if tagged_replaced > 0:
            tagged_source = updated_tagged

    if tagged_source is None:
        tagged_source = _compose_tagged_tts(new_plain, silence_plan, effective_pause_map)

    plain_content, pause_map = _persist_tts_variants(
        base_dir,
        status,
        tagged_source,
        timestamp=timestamp,
        update_assembled=payload.update_assembled,
    )

    save_status(channel_code, video_number, status)

    diff_lines = list(
        difflib.unified_diff(
            old_plain.splitlines(),
            plain_content.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    if len(diff_lines) > 300:
        diff_lines = diff_lines[:300] + ["... (diff truncated)"]

    append_audio_history_entry(
        channel_code,
        video_number,
        {
            "event": "tts_replace",
            "status": "saved",
            "message": "テキスト置換を実行",
            "diff_preview": diff_lines[:20],
        },
    )

    audio_regenerated = False
    message = None
    if payload.regenerate_audio:
        try:
            manager = AudioManager(project_root=PROJECT_ROOT)
            manager.synthesize(channel_code=channel_code, video_number=video_number)
            audio_regenerated = True
            append_audio_history_entry(
                channel_code,
                video_number,
                {
                    "event": "audio_regenerated",
                    "status": "completed",
                    "message": "置換後に音声と字幕を再生成",
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Audio regeneration failed for %s/%s", channel_code, video_number)
            message = f"音声の再生成に失敗しました: {exc}"
            append_audio_history_entry(
                channel_code,
                video_number,
                {
                    "event": "audio_regeneration_failed",
                    "status": "failed",
                    "message": str(exc),
                },
            )
    else:
        append_audio_history_entry(
            channel_code,
            video_number,
            {
                "event": "tts_replace",
                "status": "skipped",
                "message": "置換のみ (再生成なし)",
            },
        )

    return TtsReplaceResponse(
        replaced=replaced,
        content=plain_content,
        plain_content=plain_content,
        tagged_content=resolve_text_file(tagged_path),
        pause_map=pause_map or None,
        audio_regenerated=audio_regenerated,
        message=message,
    )


@router.put("/channels/{channel}/videos/{video}/tts")
def update_tts(channel: str, video: str, payload: TtsUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    base_dir = video_base_dir(channel_code, video_number)
    plain_path = base_dir / "audio_prep" / "script_sanitized.txt"
    tagged_path = base_dir / "audio_prep" / "script_sanitized_with_pauses.txt"

    old_plain = resolve_text_file(plain_path) or ""
    old_tagged = resolve_text_file(tagged_path)

    metadata = status.setdefault("metadata", {})
    audio_meta_raw = metadata.get("audio")
    if not isinstance(audio_meta_raw, dict):
        audio_meta_raw = {}
        metadata["audio"] = audio_meta_raw
    audio_meta = audio_meta_raw
    pause_map_meta = audio_meta.get("pause_map") if isinstance(audio_meta.get("pause_map"), list) else []
    derived_pause_map: List[Dict[str, Any]] = []
    if old_tagged:
        _, derived_pause_map, _ = _parse_tagged_tts(old_tagged)
    effective_pause_map: List[Dict[str, Any]] = list(pause_map_meta) if pause_map_meta else list(derived_pause_map)

    silence_plan: Optional[Sequence[float]] = None
    synthesis_meta = audio_meta.get("synthesis")
    if isinstance(synthesis_meta, dict):
        plan_candidate = synthesis_meta.get("silence_plan")
        if isinstance(plan_candidate, list):
            silence_plan = plan_candidate

    if payload.tagged_content is not None:
        tagged_source = payload.tagged_content
    else:
        if payload.content is None:
            raise HTTPException(status_code=400, detail="content または tagged_content を指定してください。")
        tagged_source = _compose_tagged_tts(payload.content, silence_plan, effective_pause_map)

    timestamp = current_timestamp()
    status["updated_at"] = timestamp

    plain_content, pause_map = _persist_tts_variants(
        base_dir,
        status,
        tagged_source,
        timestamp=timestamp,
        update_assembled=payload.update_assembled or False,
    )

    save_status(channel_code, video_number, status)

    diff_lines = list(
        difflib.unified_diff(
            old_plain.splitlines(),
            plain_content.splitlines(),
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )
    if len(diff_lines) > 300:
        diff_lines = diff_lines[:300] + ["... (diff truncated)"]

    initial_message = "音声用テキストを保存" + (" (再生成あり)" if payload.regenerate_audio else "")
    append_audio_history_entry(
        channel_code,
        video_number,
        {
            "event": "tts_saved",
            "status": "saved",
            "message": initial_message,
            "diff_preview": diff_lines[:20],
        },
    )

    audio_regenerated = False
    message = None
    if payload.regenerate_audio:
        manager = AudioManager(project_root=PROJECT_ROOT)
        try:
            manager.synthesize(channel_code=channel_code, video_number=video_number)
            audio_regenerated = True
            message = "音声と字幕を再生成しました。"
            append_audio_history_entry(
                channel_code,
                video_number,
                {
                    "event": "audio_regenerated",
                    "status": "completed",
                    "message": message,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Audio regeneration failed for %s/%s", channel_code, video_number)
            message = f"音声再生成に失敗しました: {exc}"
            append_audio_history_entry(
                channel_code,
                video_number,
                {
                    "event": "audio_regeneration_failed",
                    "status": "failed",
                    "message": str(exc),
                },
            )

    response: Dict[str, Any] = {
        "status": "ok",
        "updated_at": timestamp,
        "diff": diff_lines,
        "audio_regenerated": audio_regenerated,
        "plain_content": plain_content,
        "tagged_content": resolve_text_file(tagged_path),
        "pause_map": pause_map or None,
    }
    if message:
        response["message"] = message
    return response


@router.post(
    "/channels/{channel}/videos/{video}/tts/validate",
    response_model=TTSValidateResponse,
)
def validate_tts(channel: str, video: str, payload: TTSValidateRequest) -> TTSValidateResponse:
    sanitized, issues = analyze_tts_content(payload.content)
    return TTSValidateResponse(
        sanitized_content=sanitized,
        issues=issues,
        valid=len(issues) == 0,
    )
