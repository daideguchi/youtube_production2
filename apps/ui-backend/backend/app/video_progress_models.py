from __future__ import annotations

"""
Video/workspace progress related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import Optional

from pydantic import BaseModel


class ThumbnailProgressResponse(BaseModel):
    created: bool = False
    created_at: Optional[str] = None
    qc_cleared: bool = False
    qc_cleared_at: Optional[str] = None
    status: Optional[str] = None
    variant_count: int = 0


class VideoImagesProgressResponse(BaseModel):
    run_id: Optional[str] = None
    prompt_ready: bool = False
    prompt_ready_at: Optional[str] = None
    cue_count: Optional[int] = None
    prompt_count: Optional[int] = None
    images_count: int = 0
    images_complete: bool = False
    images_updated_at: Optional[str] = None

