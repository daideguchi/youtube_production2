from __future__ import annotations

from fastapi import APIRouter

from backend.app.normalize import normalize_channel_code, normalize_optional_text, normalize_video_number
from backend.app.planning_models import PlanningUpdateRequest, PlanningUpdateResponse
from backend.app.status_models import ensure_expected_updated_at
from backend.main import (
    build_planning_payload,
    current_timestamp,
    get_planning_section,
    load_status,
    run_ssot_sync_for_channel,
    save_status,
)

router = APIRouter(prefix="/api", tags=["planning"])


@router.put(
    "/channels/{channel}/videos/{video}/planning",
    response_model=PlanningUpdateResponse,
)
def update_planning(channel: str, video: str, payload: PlanningUpdateRequest):
    channel_code = normalize_channel_code(channel)
    video_number = normalize_video_number(video)
    status = load_status(channel_code, video_number)
    ensure_expected_updated_at(status, payload.expected_updated_at)
    metadata = status.setdefault("metadata", {})
    planning_section = get_planning_section(metadata)

    changed = False
    for key, raw_value in payload.fields.items():
        normalized_value = normalize_optional_text(raw_value)
        current_value = normalize_optional_text(planning_section.get(key))
        if normalized_value == current_value:
            continue
        changed = True
        if normalized_value is None:
            planning_section.pop(key, None)
        else:
            planning_section[key] = normalized_value

    if payload.creation_flag is not None:
        normalized_flag = normalize_optional_text(payload.creation_flag)
        existing_flag = normalize_optional_text(metadata.get("sheet_flag"))
        if normalized_flag != existing_flag:
            changed = True
            if normalized_flag is None:
                metadata.pop("sheet_flag", None)
                metadata.pop("blocked_by_sheet", None)
            else:
                metadata["sheet_flag"] = normalized_flag
                if normalized_flag in {"2", "9"}:
                    metadata["blocked_by_sheet"] = True
                else:
                    metadata.pop("blocked_by_sheet", None)

    planning_payload = build_planning_payload(metadata)

    if not changed:
        return PlanningUpdateResponse(
            status="noop",
            updated_at=status.get("updated_at") or "",
            planning=planning_payload,
        )

    timestamp = current_timestamp()
    status["updated_at"] = timestamp
    save_status(channel_code, video_number, status)
    run_ssot_sync_for_channel(channel_code, video_number)
    planning_payload = build_planning_payload(metadata)
    return PlanningUpdateResponse(status="ok", updated_at=timestamp, planning=planning_payload)
