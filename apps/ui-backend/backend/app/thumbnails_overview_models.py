from __future__ import annotations

"""
Thumbnail-overview related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import List, Literal, Optional

from pydantic import BaseModel

from backend.app.thumbnails_project_models import ThumbnailProjectResponse


class ThumbnailChannelVideoResponse(BaseModel):
    video_id: str
    title: str
    url: str
    thumbnail_url: Optional[str] = None
    published_at: Optional[str] = None
    view_count: Optional[int] = None
    duration_seconds: Optional[float] = None
    estimated_ctr: Optional[float] = None
    source: Literal["youtube", "variant"] = "youtube"


class ThumbnailChannelSummaryResponse(BaseModel):
    total: int
    subscriber_count: Optional[int] = None
    view_count: Optional[int] = None
    video_count: Optional[int] = None


class ThumbnailChannelBlockResponse(BaseModel):
    channel: str
    channel_title: Optional[str] = None
    summary: ThumbnailChannelSummaryResponse
    projects: List[ThumbnailProjectResponse]
    videos: List[ThumbnailChannelVideoResponse]
    library_path: Optional[str] = None


class ThumbnailOverviewResponse(BaseModel):
    generated_at: Optional[str] = None
    channels: List[ThumbnailChannelBlockResponse]

