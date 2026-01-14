from __future__ import annotations

"""
Thumbnail-variant related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class ThumbnailVariantResponse(BaseModel):
    id: str
    label: Optional[str] = None
    status: Optional[str] = None
    image_url: Optional[str] = None
    image_path: Optional[str] = None
    preview_url: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    model_key: Optional[str] = None
    openrouter_generation_id: Optional[str] = None
    cost_usd: Optional[float] = None
    usage: Optional[Dict[str, Any]] = None
    is_selected: Optional[bool] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

