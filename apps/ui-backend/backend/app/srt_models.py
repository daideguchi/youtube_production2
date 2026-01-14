from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class SRTIssue(BaseModel):
    type: str
    detail: str
    block: Optional[int] = None
    start: Optional[float] = None
    end: Optional[float] = None


class SRTVerifyResponse(BaseModel):
    valid: bool
    audio_duration_seconds: Optional[float]
    srt_duration_seconds: Optional[float]
    diff_ms: Optional[float]
    issues: List[SRTIssue]

