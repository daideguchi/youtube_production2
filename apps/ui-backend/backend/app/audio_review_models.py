from __future__ import annotations

"""
Audio-review related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import Optional

from pydantic import BaseModel


class AudioReviewItemResponse(BaseModel):
    channel: str
    video: str
    status: str
    title: Optional[str] = None
    channel_title: Optional[str] = None
    workspace_path: str
    audio_stage: str
    audio_stage_updated_at: Optional[str] = None
    subtitle_stage: str
    subtitle_stage_updated_at: Optional[str] = None
    audio_quality_status: Optional[str] = None
    audio_quality_summary: Optional[str] = None
    audio_updated_at: Optional[str] = None
    audio_duration_seconds: Optional[float] = None
    audio_url: Optional[str] = None
    audio_waveform_image: Optional[str] = None
    audio_waveform_url: Optional[str] = None
    audio_message: Optional[str] = None
    audio_error: Optional[str] = None
    manual_pause_count: Optional[int] = None
    ready_for_audio: bool = False
    tts_input_path: Optional[str] = None
    audio_log_url: Optional[str] = None
    audio_engine: Optional[str] = None
    audio_log_summary: Optional[dict] = None

