from __future__ import annotations

"""
Thumbnail templates related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class ThumbnailTemplatePayload(BaseModel):
    id: Optional[str] = None
    name: str = Field(..., min_length=1, max_length=160)
    image_model_key: str = Field(..., min_length=1, max_length=160)
    prompt_template: str = Field(..., min_length=1)
    negative_prompt: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("id")
    @classmethod
    def _normalize_id(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("invalid template id")
        trimmed = value.strip()
        return trimmed or None

    @field_validator("image_model_key")
    @classmethod
    def _normalize_model_key(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("invalid model key")
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("model key is required")
        return trimmed


class ThumbnailTemplateResponse(BaseModel):
    id: str
    name: str
    image_model_key: str
    prompt_template: str
    negative_prompt: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ThumbnailChannelStyleResponse(BaseModel):
    name: Optional[str] = None
    benchmark_path: Optional[str] = None
    preview_upper: Optional[str] = None
    preview_title: Optional[str] = None
    preview_lower: Optional[str] = None
    rules: Optional[List[str]] = None


class ThumbnailChannelTemplatesResponse(BaseModel):
    channel: str
    default_template_id: Optional[str] = None
    templates: List[ThumbnailTemplateResponse]
    channel_style: Optional[ThumbnailChannelStyleResponse] = None


class ThumbnailChannelTemplatesUpdateRequest(BaseModel):
    default_template_id: Optional[str] = None
    templates: List[ThumbnailTemplatePayload] = Field(default_factory=list)

