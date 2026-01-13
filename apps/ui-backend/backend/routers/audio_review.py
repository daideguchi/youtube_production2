from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.app.episode_store import get_audio_duration_seconds
from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.main import (
    AudioReviewItemResponse,
    DATA_ROOT,
    list_channel_dirs,
    list_video_dirs,
    load_status,
    refresh_channel_info,
    resolve_audio_path,
    resolve_log_path,
    resolve_srt_path,
    summarize_log,
    video_base_dir,
)

router = APIRouter(prefix="/api/workspaces/audio-review", tags=["audio-review"])


def _count_manual_pauses(history: Any) -> int:
    if not isinstance(history, list):
        return 0
    count = 0
    for entry in history:
        if not isinstance(entry, dict):
            continue
        event = str(entry.get("event", "")).lower()
        message = str(entry.get("message", "") or "").lower()
        if "manual" in event or "manual" in message:
            count += 1
    return count


@router.get("", response_model=List[AudioReviewItemResponse])
def list_audio_review_items(
    channel: Optional[str] = Query(default=None, description="フィルタ対象のチャンネルコード"),
    status: Optional[str] = Query(default=None, description="フィルタ対象の案件ステータス"),
    video: Optional[str] = Query(default=None, description="フィルタ対象の動画番号（3桁）"),
):
    channel_filter: Optional[str]
    if isinstance(channel, str) and channel.strip() and channel.strip().lower() != "all":
        channel_filter = normalize_channel_code(channel)
    else:
        channel_filter = None

    video_filter: Optional[str]
    if isinstance(video, str) and video.strip():
        try:
            video_filter = normalize_video_number(video)
        except HTTPException:
            video_filter = None
    else:
        video_filter = None

    if isinstance(status, str) and status.strip() and status.strip().lower() != "all":
        status_filter = status.strip()
    else:
        status_filter = None

    channel_info_map = refresh_channel_info()
    if channel_filter:
        channel_dirs = [DATA_ROOT / channel_filter]
    else:
        channel_dirs = list_channel_dirs()

    items: List[AudioReviewItemResponse] = []

    for channel_dir in channel_dirs:
        if not channel_dir.is_dir():
            continue
        channel_code = channel_dir.name.upper()
        # Avoid circular import during app.include_router(): helper is defined after router wiring in backend.main.
        from backend import main as backend_main

        channel_title = backend_main._resolve_channel_title(channel_code, channel_info_map)

        for video_dir in list_video_dirs(channel_code):
            video_number = video_dir.name
            if video_filter and video_number != video_filter:
                continue
            try:
                status_payload = load_status(channel_code, video_number)
            except HTTPException as exc:
                if exc.status_code == 404:
                    continue
                raise

            top_level_status = status_payload.get("status", "unknown")
            if status_filter and top_level_status != status_filter:
                continue

            metadata = status_payload.get("metadata", {}) or {}
            title = metadata.get("sheet_title") or metadata.get("title") or status_payload.get("script_id")

            stages = status_payload.get("stages", {}) or {}
            audio_stage_meta = stages.get("audio_synthesis", {}) or {}
            subtitle_stage_meta = stages.get("srt_generation", {}) or {}

            audio_stage = str(audio_stage_meta.get("status", "pending"))
            audio_stage_updated_at = audio_stage_meta.get("updated_at")
            subtitle_stage = str(subtitle_stage_meta.get("status", "pending"))
            subtitle_stage_updated_at = subtitle_stage_meta.get("updated_at")

            base_dir = video_base_dir(channel_code, video_number)
            audio_path = resolve_audio_path(status_payload, base_dir)
            audio_duration = get_audio_duration_seconds(audio_path) if audio_path else None
            audio_updated_at: Optional[str] = None
            if audio_path:
                try:
                    audio_updated_at = (
                        datetime.fromtimestamp(audio_path.stat().st_mtime, timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                except OSError:
                    audio_updated_at = None

            audio_url = f"/api/channels/{channel_code}/videos/{video_number}/audio" if audio_path else None

            # SRT path (for UI download/display)
            srt_path = resolve_srt_path(status_payload, base_dir)
            srt_url = f"/api/channels/{channel_code}/videos/{video_number}/srt" if srt_path else None
            log_path = resolve_log_path(status_payload, base_dir)
            log_url = f"/api/channels/{channel_code}/videos/{video_number}/log" if log_path else None

            audio_meta = (
                metadata.get("audio") if isinstance(metadata.get("audio"), dict) else metadata.get("audio", {})
            )
            if not isinstance(audio_meta, dict):
                audio_meta = {}
            engine_meta = audio_meta.get("engine") if isinstance(audio_meta, dict) else None
            log_summary = summarize_log(log_path) if log_path else None

            quality_meta = audio_meta.get("quality")
            audio_quality_status = None
            audio_quality_summary = None
            if isinstance(quality_meta, dict):
                audio_quality_status = quality_meta.get("status") or quality_meta.get("label")
                audio_quality_summary = quality_meta.get("summary") or quality_meta.get("note")
            elif isinstance(quality_meta, str):
                audio_quality_status = quality_meta

            waveform_meta = audio_meta.get("waveform")
            audio_waveform_image = None
            audio_waveform_url = None
            if isinstance(waveform_meta, dict):
                audio_waveform_image = waveform_meta.get("image")
                audio_waveform_url = waveform_meta.get("url")
            elif isinstance(waveform_meta, str):
                audio_waveform_url = waveform_meta

            audio_message = audio_meta.get("message") if isinstance(audio_meta.get("message"), str) else None
            audio_error = audio_meta.get("error") if isinstance(audio_meta.get("error"), str) else None
            manual_pause_count = _count_manual_pauses(audio_meta.get("history"))

            # 再生成用の input_path を明示。script_audio_path → tts_path → assembled_path の優先度で拾う。
            input_path = None
            for cand in (
                status_payload.get("script_audio_path"),
                status_payload.get("tts_path"),
                status_payload.get("assembled_path"),
            ):
                if cand:
                    input_path = str(base_dir / cand)
                    break
            # メタに明示されていればそれを優先
            if isinstance(audio_meta.get("input_path"), str):
                input_path = str(base_dir / audio_meta.get("input_path"))

            ready_for_audio = bool(metadata.get("ready_for_audio", False))

            items.append(
                AudioReviewItemResponse(
                    channel=channel_code,
                    video=video_number,
                    status=top_level_status,
                    title=title,
                    channel_title=channel_title,
                    workspace_path=f"/channels/{channel_code}/videos/{video_number}?tab=audio",
                    audio_stage=audio_stage,
                    audio_stage_updated_at=audio_stage_updated_at,
                    subtitle_stage=subtitle_stage,
                    subtitle_stage_updated_at=subtitle_stage_updated_at,
                    audio_quality_status=audio_quality_status,
                    audio_quality_summary=audio_quality_summary,
                    audio_updated_at=audio_updated_at,
                    audio_duration_seconds=audio_duration,
                    audio_url=audio_url,
                    srt_url=srt_url,
                    audio_waveform_image=audio_waveform_image,
                    audio_waveform_url=audio_waveform_url,
                    audio_message=audio_message,
                    audio_error=audio_error,
                    manual_pause_count=manual_pause_count or None,
                    ready_for_audio=ready_for_audio,
                    tts_input_path=input_path,
                    audio_log_url=log_url,
                    audio_engine=engine_meta,
                    audio_log_summary=log_summary,
                )
            )

    items.sort(key=lambda item: item.audio_updated_at or "", reverse=True)
    return items
