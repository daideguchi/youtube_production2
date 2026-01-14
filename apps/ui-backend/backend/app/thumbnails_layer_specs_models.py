from __future__ import annotations

"""
Thumbnail layer-spec related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import Dict, List, Optional

from pydantic import BaseModel


class ThumbnailLayerSpecRefResponse(BaseModel):
    id: str
    kind: str
    version: int
    path: str
    name: Optional[str] = None


class ThumbnailChannelLayerSpecsResponse(BaseModel):
    channel: str
    image_prompts: Optional[ThumbnailLayerSpecRefResponse] = None
    text_layout: Optional[ThumbnailLayerSpecRefResponse] = None


class ThumbnailLayerSpecPlanningSuggestionsResponse(BaseModel):
    thumbnail_prompt: Optional[str] = None
    thumbnail_upper: Optional[str] = None
    thumbnail_title: Optional[str] = None
    thumbnail_lower: Optional[str] = None
    text_design_note: Optional[str] = None


class ThumbnailVideoTextLayoutSpecResponse(BaseModel):
    template_id: Optional[str] = None
    fallbacks: Optional[List[str]] = None
    text: Optional[Dict[str, str]] = None


class ThumbnailVideoLayerSpecsResponse(BaseModel):
    channel: str
    video: str
    video_id: str
    image_prompt: Optional[str] = None
    text_layout: Optional[ThumbnailVideoTextLayoutSpecResponse] = None
    planning_suggestions: Optional[ThumbnailLayerSpecPlanningSuggestionsResponse] = None


class ThumbnailImageModelInfoResponse(BaseModel):
    key: str
    provider: str
    model_name: str
    pricing: Optional[Dict[str, str]] = None
    pricing_updated_at: Optional[str] = None


class ThumbnailParamCatalogEntryResponse(BaseModel):
    path: str
    kind: str
    engine: str
    min_value: Optional[float] = None
    max_value: Optional[float] = None

