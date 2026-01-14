from __future__ import annotations

"""
Thumbnail-project related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import List, Optional

from pydantic import BaseModel, Field

from backend.app.thumbnails_variant_models import ThumbnailVariantResponse


class ThumbnailProjectResponse(BaseModel):
    channel: str
    video: str
    script_id: Optional[str] = None
    title: Optional[str] = None
    sheet_title: Optional[str] = None
    status: Optional[str] = None
    owner: Optional[str] = None
    summary: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    variants: List[ThumbnailVariantResponse]
    ready_for_publish: Optional[bool] = None
    updated_at: Optional[str] = None
    status_updated_at: Optional[str] = None
    due_at: Optional[str] = None
    selected_variant_id: Optional[str] = None
    audio_stage: Optional[str] = None
    script_stage: Optional[str] = None


class ThumbnailProjectUpdateRequest(BaseModel):
    owner: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    tags: Optional[List[str]] = Field(default=None)
    due_at: Optional[str] = Field(default=None)
    status: Optional[str] = Field(default=None)
    selected_variant_id: Optional[str] = Field(default=None)
