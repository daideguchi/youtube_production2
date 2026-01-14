from __future__ import annotations

"""
Thumbnail video-operation related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class ThumbnailAssetReplaceResponse(BaseModel):
    status: str
    channel: str
    video: str
    slot: str
    file_name: str
    image_path: str
    public_url: str


class ThumbnailVariantCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=160)
    status: Optional[str] = Field(default="draft")
    image_url: Optional[str] = None
    image_path: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    prompt: Optional[str] = None
    make_selected: Optional[bool] = False

    @model_validator(mode="after")
    def _ensure_source(self):
        if not (self.image_url or self.image_path):
            raise ValueError("画像URLまたは画像パスを指定してください。")
        return self


class ThumbnailVariantGenerateRequest(BaseModel):
    template_id: Optional[str] = None
    image_model_key: Optional[str] = None
    prompt: Optional[str] = None
    count: int = Field(default=1, ge=1, le=4)
    label: Optional[str] = None
    status: Optional[str] = Field(default="draft")
    make_selected: Optional[bool] = False
    notes: Optional[str] = None
    tags: Optional[List[str]] = None


class ThumbnailVariantComposeRequest(BaseModel):
    """
    Local composition (no AI): put 3-line text on the fixed Buddha template.
    """

    copy_upper: Optional[str] = None
    copy_title: Optional[str] = None
    copy_lower: Optional[str] = None
    label: Optional[str] = None
    status: Optional[str] = Field(default="draft")
    make_selected: Optional[bool] = False
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    impact: Optional[bool] = True
    flip_base: Optional[bool] = True


class ThumbnailVariantPatchRequest(BaseModel):
    label: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    make_selected: Optional[bool] = None

