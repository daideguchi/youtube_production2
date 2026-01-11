from __future__ import annotations

"""
Dashboard-related Pydantic models shared across UI backend modules.

created: 2026-01-11
"""

from typing import Dict, List, Optional

from pydantic import BaseModel


class DashboardChannelSummary(BaseModel):
    code: str
    total: int = 0
    script_completed: int = 0
    audio_completed: int = 0
    srt_completed: int = 0
    blocked: int = 0
    ready_for_audio: int = 0
    pending_sync: int = 0


class DashboardAlert(BaseModel):
    type: str
    channel: str
    video: str
    message: str
    updated_at: Optional[str] = None


class DashboardOverviewResponse(BaseModel):
    generated_at: str
    channels: List[DashboardChannelSummary]
    stage_matrix: Dict[str, Dict[str, Dict[str, int]]]
    alerts: List[DashboardAlert]

