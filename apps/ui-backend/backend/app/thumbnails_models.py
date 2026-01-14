from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ThumbnailOverrideRequest(BaseModel):
    thumbnail_url: str
    thumbnail_path: Optional[str] = None


class ThumbnailOverrideResponse(BaseModel):
    status: str
    thumbnail_url: str
    thumbnail_path: Optional[str] = None
    updated_at: str


class ThumbnailQcNoteUpdateRequest(BaseModel):
    relative_path: str = Field(..., min_length=1)
    note: Optional[str] = None
