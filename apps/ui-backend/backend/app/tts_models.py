from __future__ import annotations

"""
TTS-related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import List, Optional

from pydantic import BaseModel


class TTSIssue(BaseModel):
    type: str
    line: Optional[int] = None
    detail: Optional[str] = None


class TTSValidateRequest(BaseModel):
    content: str


class TTSValidateResponse(BaseModel):
    sanitized_content: str
    issues: List[TTSIssue]
    valid: bool

