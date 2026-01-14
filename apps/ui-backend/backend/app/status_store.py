from __future__ import annotations

from backend.app.status_models import STAGE_ORDER


def default_status_payload(channel_code: str, video_number: str) -> dict:
    return {
        "script_id": f"{channel_code}-{video_number}",
        "channel": channel_code,
        "status": "pending",
        "metadata": {},
        "stages": {stage: {"status": "pending", "details": {}} for stage in STAGE_ORDER},
    }

