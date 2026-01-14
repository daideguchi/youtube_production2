from __future__ import annotations

"""
Thumbnail-description related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import Literal, Optional

from pydantic import BaseModel


class ThumbnailDescriptionResponse(BaseModel):
    description: str
    model: Optional[str] = None
    source: Literal["openai", "openrouter", "heuristic"]

