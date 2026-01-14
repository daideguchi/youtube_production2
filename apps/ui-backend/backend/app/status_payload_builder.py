from __future__ import annotations

from typing import Any, Dict, Optional

from backend.app.audio_metadata_utils import normalize_audio_path_string
from backend.app.datetime_utils import current_timestamp
from backend.app.status_models import STAGE_ORDER
from backend.app.video_registry_models import VideoFileReferences, VideoGenerationInfo


def initialize_stage_payload(initial_stage: Optional[str] = None) -> Dict[str, dict]:
    stages: Dict[str, dict] = {}
    encountered = False
    for stage in STAGE_ORDER:
        status = "pending"
        if initial_stage:
            if stage == initial_stage:
                status = "in_progress"
                encountered = True
            elif not encountered:
                status = "completed"
            else:
                status = "pending"
        stages[stage] = {"status": status}
    return stages


def build_status_payload(
    *,
    channel_code: str,
    video_number: str,
    script_id: Optional[str],
    title: Optional[str],
    initial_stage: Optional[str],
    status_value: Optional[str],
    metadata_patch: Dict[str, Any],
    generation: Optional[VideoGenerationInfo],
    files: Optional[VideoFileReferences],
) -> dict:
    timestamp = current_timestamp()
    payload: Dict[str, Any] = {
        "script_id": script_id or f"{channel_code}-{video_number}",
        "channel": channel_code,
        "status": status_value or "pending",
        "stages": initialize_stage_payload(initial_stage),
        "created_at": timestamp,
        "updated_at": timestamp,
        "metadata": {},
    }
    metadata: Dict[str, Any] = {}
    if title:
        metadata["title"] = title
        metadata.setdefault("sheet_title", title)
    if generation:
        metadata["generation"] = generation.model_dump(exclude_none=True)
    if metadata_patch:
        metadata.update(metadata_patch)
    metadata.setdefault("ready_for_audio", False)

    files_dict = files.model_dump(exclude_none=True) if files else {}
    if files_dict.get("assembled"):
        metadata.setdefault("script", {})
        metadata["script"]["assembled_path"] = normalize_audio_path_string(files_dict["assembled"])
    if files_dict.get("tts"):
        audio_meta = metadata.setdefault("audio", {})
        prepare_meta = audio_meta.setdefault("prepare", {})
        prepare_meta["script_sanitized_path"] = normalize_audio_path_string(files_dict["tts"])

    payload["metadata"] = metadata
    return payload

