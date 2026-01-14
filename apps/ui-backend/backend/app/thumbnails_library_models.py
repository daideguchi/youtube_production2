from __future__ import annotations

"""
Thumbnail library related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from backend.app.thumbnails_constants import THUMBNAIL_SUPPORTED_EXTENSIONS


class ThumbnailLibraryAssetResponse(BaseModel):
    id: str
    file_name: str
    size_bytes: int
    updated_at: str
    public_url: str
    relative_path: str


class ThumbnailQuickHistoryEntry(BaseModel):
    channel: str
    video: str
    label: Optional[str] = None
    asset_name: str
    image_path: Optional[str] = None
    public_url: str
    timestamp: str


class ThumbnailLibraryRenameRequest(BaseModel):
    new_name: str

    @field_validator("new_name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("invalid name")
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("empty name")
        if "/" in trimmed or "\\" in trimmed:
            raise ValueError("name must not contain path separators")
        suffix = Path(trimmed).suffix.lower()
        if suffix not in THUMBNAIL_SUPPORTED_EXTENSIONS:
            raise ValueError(
                f"拡張子は {', '.join(sorted(THUMBNAIL_SUPPORTED_EXTENSIONS))} のいずれかにしてください。"
            )
        return trimmed


class ThumbnailLibraryAssignRequest(BaseModel):
    video: str
    label: Optional[str] = None
    make_selected: Optional[bool] = None

    @field_validator("video")
    @classmethod
    def _validate_video(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("invalid video identifier")
        text = value.strip()
        if not text:
            raise ValueError("video identifier is required")
        if not text.isdigit():
            raise ValueError("動画番号は数字で入力してください。")
        return text


class ThumbnailLibraryAssignResponse(BaseModel):
    file_name: str
    image_path: str
    public_url: str


class ThumbnailLibraryImportRequest(BaseModel):
    url: str = Field(..., min_length=1)
    file_name: Optional[str] = None

