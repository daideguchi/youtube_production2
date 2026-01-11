from __future__ import annotations

"""
Workflow-precheck models shared across UI backend modules.

created: 2026-01-11
"""

from typing import List, Optional

from pydantic import BaseModel


class WorkflowPrecheckItem(BaseModel):
    script_id: str
    video_number: str
    progress: Optional[str] = None
    title: Optional[str] = None
    flag: Optional[str] = None


class WorkflowPrecheckPendingSummary(BaseModel):
    channel: str
    count: int
    items: List[WorkflowPrecheckItem]


class WorkflowPrecheckReadyEntry(BaseModel):
    channel: str
    video_number: str
    script_id: str
    audio_status: Optional[str] = None


class WorkflowPrecheckResponse(BaseModel):
    generated_at: str
    pending: List[WorkflowPrecheckPendingSummary]
    ready: List[WorkflowPrecheckReadyEntry]

