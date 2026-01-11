from __future__ import annotations

"""
Publishing-related Pydantic models shared across UI backend modules.

created: 2026-01-11
"""

from typing import List, Optional

from pydantic import BaseModel, Field


class PublishLockRequest(BaseModel):
    force_complete: bool = True
    published_at: Optional[str] = None


class PublishLockResponse(BaseModel):
    status: str
    channel: str
    video: str
    published_at: str
    updated_at: str


class PublishUnlockResponse(BaseModel):
    status: str
    channel: str
    video: str
    updated_at: str


class PublishingScheduleVideoItem(BaseModel):
    channel: str
    video: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None
    visibility: Optional[str] = None
    scheduled_publish_at: Optional[str] = None
    youtube_video_id: Optional[str] = None


class PublishingScheduleChannelSummary(BaseModel):
    channel: str
    last_published_date: Optional[str] = None
    last_scheduled_date: Optional[str] = None
    schedule_runway_days: int = 0
    upcoming_count: int = 0
    upcoming: List[PublishingScheduleVideoItem] = Field(default_factory=list)


class PublishingScheduleOverviewResponse(BaseModel):
    status: str
    timezone: str
    today: str
    now: str
    sheet_id: Optional[str] = None
    sheet_name: Optional[str] = None
    fetched_at: Optional[str] = None
    channels: List[PublishingScheduleChannelSummary]
    warnings: List[str] = Field(default_factory=list)
