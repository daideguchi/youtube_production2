from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query

from backend.app.datetime_utils import current_timestamp, parse_iso_datetime
from backend.app.dashboard_models import DashboardAlert, DashboardChannelSummary, DashboardOverviewResponse
from backend.app.channel_catalog import list_known_channel_codes, list_planning_video_numbers, list_video_dirs
from backend.app.episode_store import load_status_optional, resolve_audio_path, resolve_srt_path, video_base_dir
from backend.app.path_utils import safe_exists
from backend.app.stage_status_utils import _stage_status_value
from backend.app.status_models import STAGE_ORDER, VALID_STAGE_STATUSES
from backend.app.status_store import default_status_payload
from backend.app.video_effective_status import _derive_effective_stages

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _ensure_stage_bucket(
    matrix: Dict[str, Dict[str, Dict[str, int]]], channel_code: str, stage_key: str
) -> Dict[str, int]:
    channel_bucket = matrix.setdefault(channel_code, {})
    stage_bucket = channel_bucket.get(stage_key)
    if not stage_bucket:
        stage_bucket = {status: 0 for status in VALID_STAGE_STATUSES}
        stage_bucket["unknown"] = 0
        channel_bucket[stage_key] = stage_bucket
    else:
        for status in VALID_STAGE_STATUSES:
            stage_bucket.setdefault(status, 0)
        stage_bucket.setdefault("unknown", 0)
    return stage_bucket


def _increment_stage_matrix(
    matrix: Dict[str, Dict[str, Dict[str, int]]],
    channel_code: str,
    stages: Dict[str, Any],
) -> None:
    for stage_key in STAGE_ORDER:
        stage_bucket = _ensure_stage_bucket(matrix, channel_code, stage_key)
        stage_entry = stages.get(stage_key)
        status = _stage_status_value(stage_entry)
        stage_bucket[status] = stage_bucket.get(status, 0) + 1


def _collect_alerts(
    *,
    channel_code: str,
    video_number: str,
    stages: Dict[str, Any],
    metadata: Dict[str, Any],
    status_value: str,
    alerts: List[DashboardAlert],
) -> None:
    if status_value == "blocked" or any(
        _stage_status_value(stages.get(stage_key)) == "blocked" for stage_key in STAGE_ORDER
    ):
        alerts.append(
            DashboardAlert(
                type="blocked_stage",
                channel=channel_code,
                video=video_number,
                message="ステージが要対応状態です",
            )
        )

    audio_quality = metadata.get("audio", {}).get("quality", {})
    quality_status = None
    if isinstance(audio_quality, dict):
        quality_status = audio_quality.get("status") or audio_quality.get("label")
    elif isinstance(audio_quality, str):
        quality_status = audio_quality
    if quality_status:
        ok_statuses = {"completed", "ok", "良好", "問題なし", "完了"}
        if all(token.lower() not in ok_statuses for token in [quality_status.lower()]):
            alerts.append(
                DashboardAlert(
                    type="audio_quality",
                    channel=channel_code,
                    video=video_number,
                    message=f"音声品質ステータス: {quality_status}",
                )
            )

    sheets_meta = metadata.get("sheets")
    if isinstance(sheets_meta, dict):
        state = sheets_meta.get("state")
        if state and state.lower() == "failed":
            alerts.append(
                DashboardAlert(
                    type="sheet_sync",
                    channel=channel_code,
                    video=video_number,
                    message="スプレッドシート同期に失敗しました",
                )
            )


@router.get("/overview", response_model=DashboardOverviewResponse)
def dashboard_overview(
    channels: Optional[str] = Query(None, description="カンマ区切りのチャンネルコード"),
    status: Optional[str] = Query(None, description="カンマ区切りの案件ステータス"),
    from_param: Optional[str] = Query(None, alias="from", description="更新日時の下限 ISO8601"),
    to_param: Optional[str] = Query(None, alias="to", description="更新日時の上限 ISO8601"),
):
    channel_filter = {code.strip().upper() for code in channels.split(",")} if channels else None
    status_filter = {value.strip() for value in status.split(",") if value.strip()} if status else None
    from_dt = parse_iso_datetime(from_param)
    to_dt = parse_iso_datetime(to_param)

    overview_channels: List[DashboardChannelSummary] = []
    stage_matrix: Dict[str, Dict[str, Dict[str, int]]] = {}
    alerts: List[DashboardAlert] = []

    for channel_code in list_known_channel_codes():
        if channel_filter and channel_code not in channel_filter:
            continue

        summary = DashboardChannelSummary(code=channel_code)
        planned_video_numbers = list_planning_video_numbers(channel_code)
        video_numbers = (
            planned_video_numbers if planned_video_numbers else [video_dir.name for video_dir in list_video_dirs(channel_code)]
        )

        for video_number in video_numbers:
            status_payload = load_status_optional(channel_code, video_number)
            if status_payload is None:
                status_payload = default_status_payload(channel_code, video_number)

            base_dir = video_base_dir(channel_code, video_number)
            status_value = status_payload.get("status", "unknown")
            if status_filter and status_value not in status_filter:
                continue

            updated_at_raw = status_payload.get("updated_at")
            updated_at_dt = parse_iso_datetime(updated_at_raw)
            if from_dt and (not updated_at_dt or updated_at_dt < from_dt):
                continue
            if to_dt and (not updated_at_dt or updated_at_dt > to_dt):
                continue

            summary.total += 1
            metadata = status_payload.get("metadata", {}) if isinstance(status_payload.get("metadata", {}), dict) else {}
            stages_raw = status_payload.get("stages", {})
            stages, a_text_ok, audio_exists, srt_exists = _derive_effective_stages(
                channel_code=channel_code,
                video_number=video_number,
                stages=stages_raw if isinstance(stages_raw, dict) else {},
                metadata=metadata,
            )

            # 台本完成: script_polish_ai があれば優先、なければ script_review/script_validation を代用
            if (
                a_text_ok
                or _stage_status_value(stages.get("script_polish_ai")) == "completed"
                or _stage_status_value(stages.get("script_review")) == "completed"
                or _stage_status_value(stages.get("script_validation")) == "completed"
            ):
                summary.script_completed += 1

            # 音声完了: audio_synthesis があればそれ、無ければ最終WAVの存在で代用
            audio_done = audio_exists or _stage_status_value(stages.get("audio_synthesis")) == "completed"
            if not audio_done:  # legacy fallback (status.json metadata paths etc)
                audio_path = resolve_audio_path(status_payload, base_dir)
                audio_done = bool(audio_path and safe_exists(audio_path))
            if audio_done:
                summary.audio_completed += 1

            # 字幕完了: srt_generation があればそれ、無ければ最終SRTの存在で代用
            srt_done = srt_exists or _stage_status_value(stages.get("srt_generation")) == "completed"
            if not srt_done:  # legacy fallback (status.json metadata paths etc)
                srt_path = resolve_srt_path(status_payload, base_dir)
                srt_done = bool(srt_path and safe_exists(srt_path))
            if srt_done:
                summary.srt_completed += 1

            if status_value == "blocked" or any(
                _stage_status_value(stages.get(stage_key)) == "blocked" for stage_key in STAGE_ORDER
            ):
                summary.blocked += 1

            if (
                bool(metadata.get("ready_for_audio"))
                or _stage_status_value(stages.get("script_validation")) == "completed"
                or str(status_value or "").strip().lower() == "script_validated"
            ):
                summary.ready_for_audio += 1

            sheets_meta = metadata.get("sheets") if isinstance(metadata.get("sheets"), dict) else None
            if sheets_meta:
                state = sheets_meta.get("state")
                if state and state.lower() != "synced":
                    summary.pending_sync += 1

            _increment_stage_matrix(stage_matrix, channel_code, stages)
            _collect_alerts(
                channel_code=channel_code,
                video_number=video_number,
                stages=stages,
                metadata=metadata,
                status_value=status_value,
                alerts=alerts,
            )

        include_zeros = not (status_filter or from_dt or to_dt) or bool(channel_filter)
        if summary.total > 0 or include_zeros:
            overview_channels.append(summary)

    return DashboardOverviewResponse(
        generated_at=current_timestamp(),
        channels=overview_channels,
        stage_matrix=stage_matrix,
        alerts=alerts,
    )
