from __future__ import annotations

"""
Thumbnail spec models (thumb/text-line/elements) shared across UI backend modules.

created: 2026-01-14
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

THUMBNAIL_TEXT_LINE_SPEC_SCHEMA_V1 = "ytm.thumbnail.text_line_spec.v1"
THUMBNAIL_ELEMENTS_SPEC_SCHEMA_V1 = "ytm.thumbnail.elements_spec.v1"


class ThumbnailThumbSpecUpdateRequest(BaseModel):
    overrides: Dict[str, Any] = Field(default_factory=dict)


class ThumbnailThumbSpecResponse(BaseModel):
    exists: bool
    path: Optional[str] = None
    schema_: Optional[str] = Field(default=None, alias="schema")
    channel: str
    video: str
    overrides: Dict[str, Any] = Field(default_factory=dict)
    updated_at: Optional[str] = None
    normalized_overrides_leaf: Dict[str, Any] = Field(default_factory=dict)


class ThumbnailTextLineSpecLinePayload(BaseModel):
    offset_x: float = 0.0
    offset_y: float = 0.0
    scale: float = 1.0
    rotate_deg: float = 0.0


class ThumbnailTextLineSpecUpdateRequest(BaseModel):
    lines: Dict[str, ThumbnailTextLineSpecLinePayload] = Field(default_factory=dict)


class ThumbnailTextLineSpecResponse(BaseModel):
    exists: bool
    path: Optional[str] = None
    schema_: str = Field(default=THUMBNAIL_TEXT_LINE_SPEC_SCHEMA_V1, alias="schema")
    channel: str
    video: str
    stable: str
    lines: Dict[str, ThumbnailTextLineSpecLinePayload] = Field(default_factory=dict)
    updated_at: Optional[str] = None


class ThumbnailElementStrokePayload(BaseModel):
    color: Optional[str] = None
    width_px: float = 0.0


class ThumbnailElementPayload(BaseModel):
    id: str
    kind: str
    layer: str = "above_portrait"  # above_portrait | below_portrait
    z: int = 0
    x: float = 0.5  # normalized center (0-1), can go out of frame
    y: float = 0.5
    w: float = 0.2  # normalized size (relative to canvas)
    h: float = 0.2
    rotation_deg: float = 0.0
    opacity: float = 1.0
    fill: Optional[str] = None
    stroke: Optional[ThumbnailElementStrokePayload] = None
    src_path: Optional[str] = None  # relative path under workspaces/thumbnails/assets (e.g. CHxx/library/foo.png)


class ThumbnailElementsSpecUpdateRequest(BaseModel):
    elements: List[ThumbnailElementPayload] = Field(default_factory=list)


class ThumbnailElementsSpecResponse(BaseModel):
    exists: bool
    path: Optional[str] = None
    schema_: str = Field(default=THUMBNAIL_ELEMENTS_SPEC_SCHEMA_V1, alias="schema")
    channel: str
    video: str
    stable: str
    elements: List[ThumbnailElementPayload] = Field(default_factory=list)
    updated_at: Optional[str] = None
