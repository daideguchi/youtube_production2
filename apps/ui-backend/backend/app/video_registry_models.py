from __future__ import annotations

"""
Video registry models shared across UI backend modules.

created: 2026-01-14
"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.app.planning_models import PlanningInfoResponse
from backend.app.status_models import MAX_STATUS_LENGTH, STAGE_ORDER
from backend.app.video_progress_models import ThumbnailProgressResponse, VideoImagesProgressResponse


class VideoGenerationInfo(BaseModel):
    mode: Optional[str] = Field(None, description="auto / interactive などのモード表記")
    prompt_version: Optional[str] = None
    logs: Optional[str] = Field(None, description="生成時のログパス")


class VideoFileReferences(BaseModel):
    assembled: Optional[str] = Field(None, description="assembled.md の格納パス")
    tts: Optional[str] = Field(None, description="script_sanitized.txt の格納パス")


class VideoCreateRequest(BaseModel):
    video: str = Field(..., description="動画番号（数字）")
    script_id: Optional[str] = Field(None, description="スクリプトID")
    title: Optional[str] = Field(None, description="タイトル")
    generation: Optional[VideoGenerationInfo] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    initial_stage: Optional[str] = Field(None, description="着手済みステージ")
    status: Optional[str] = Field(None, description="全体ステータス", max_length=MAX_STATUS_LENGTH)
    files: Optional[VideoFileReferences] = None

    @field_validator("video")
    @classmethod
    def validate_video(cls, value: str) -> str:
        raw = value.strip()
        if not raw.isdigit():
            raise HTTPException(status_code=400, detail="video は数字のみ指定してください。")
        return raw

    @field_validator("initial_stage")
    @classmethod
    def validate_initial_stage(cls, value: Optional[str]) -> Optional[str]:
        if value and value not in STAGE_ORDER:
            raise HTTPException(status_code=400, detail=f"未知のステージ: {value}")
        return value

    @field_validator("status")
    @classmethod
    def validate_initial_status(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalized = value.strip()
        if len(normalized) > MAX_STATUS_LENGTH:
            raise HTTPException(status_code=400, detail="status が長すぎます。64文字以内にしてください。")
        if not normalized:
            raise HTTPException(status_code=400, detail="status は空にできません。")
        return normalized


class ArtifactEntryResponse(BaseModel):
    key: str
    label: str
    path: str
    kind: Literal["file", "dir"] = "file"
    exists: bool
    size_bytes: Optional[int] = None
    modified_time: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None


class ArtifactsSummaryResponse(BaseModel):
    project_dir: Optional[str] = None
    items: List[ArtifactEntryResponse] = Field(default_factory=list)


class VideoDetailResponse(BaseModel):
    channel: str
    video: str
    script_id: Optional[str]
    title: Optional[str]
    status: str
    ready_for_audio: bool
    stages: Dict[str, str]
    stage_details: Optional[Dict[str, Any]] = None
    redo_script: bool = True
    redo_audio: bool = True
    redo_note: Optional[str] = None
    alignment_status: Optional[str] = None
    alignment_reason: Optional[str] = None
    assembled_path: Optional[str]
    assembled_content: Optional[str]
    assembled_human_path: Optional[str] = None
    assembled_human_content: Optional[str] = None
    tts_path: Optional[str]
    tts_content: Optional[str]
    tts_plain_content: Optional[str] = None
    tts_tagged_path: Optional[str] = None
    tts_tagged_content: Optional[str] = None
    script_audio_path: Optional[str] = None
    script_audio_content: Optional[str] = None
    script_audio_human_path: Optional[str] = None
    script_audio_human_content: Optional[str] = None
    srt_path: Optional[str]
    srt_content: Optional[str]
    audio_path: Optional[str]
    audio_url: Optional[str]
    audio_duration_seconds: Optional[float] = None
    audio_updated_at: Optional[str] = None
    audio_quality_status: Optional[str] = None
    audio_quality_summary: Optional[str] = None
    audio_quality_report: Optional[str] = None
    audio_metadata: Optional[Dict[str, Any]] = None
    tts_pause_map: Optional[List[Dict[str, Any]]] = None
    audio_reviewed: Optional[bool] = False
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    ui_session_token: Optional[str] = None
    planning: Optional[PlanningInfoResponse] = None
    youtube_description: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)
    artifacts: Optional[ArtifactsSummaryResponse] = None


class VideoSummaryResponse(BaseModel):
    video: str
    script_id: Optional[str]
    title: Optional[str]
    status: str
    ready_for_audio: bool
    published_lock: bool = False
    stages: Dict[str, str]
    updated_at: Optional[str] = None
    character_count: int = 0
    a_text_exists: bool = False
    a_text_character_count: int = 0
    planning_character_count: Optional[int] = None
    planning: Optional[PlanningInfoResponse] = None
    youtube_description: Optional[str] = None
    thumbnail_progress: Optional[ThumbnailProgressResponse] = None
    video_images_progress: Optional[VideoImagesProgressResponse] = None

