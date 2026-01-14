from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ThumbnailOverrideRequest(BaseModel):
    thumbnail_url: str
    thumbnail_path: Optional[str] = None


class ThumbnailOverrideResponse(BaseModel):
    status: str
    thumbnail_url: str
    thumbnail_path: Optional[str] = None
    updated_at: str

