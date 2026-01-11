from __future__ import annotations

"""
Redo-related Pydantic models shared across UI backend modules.

created: 2026-01-11
"""

from typing import Optional

from pydantic import BaseModel


class RedoItemResponse(BaseModel):
    channel: str
    video: str
    redo_script: bool
    redo_audio: bool
    redo_note: Optional[str] = None
    title: Optional[str] = None
    status: Optional[str] = None


class RedoSummaryItem(BaseModel):
    channel: str
    redo_script: int
    redo_audio: int
    redo_both: int

