from __future__ import annotations

"""
Thumbnail editor/preview/comment-patch Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from backend.app.thumbnails_specs_models import ThumbnailTextLineSpecLinePayload

THUMBNAIL_COMMENT_PATCH_SCHEMA_V1 = "ytm.thumbnail.comment_patch.v1"


class ThumbnailPreviewTextSlotImageResponse(BaseModel):
    image_url: str
    image_path: str


class ThumbnailPreviewTextLayerSlotsResponse(BaseModel):
    status: str
    channel: str
    video: str
    template_id: Optional[str] = None
    images: Dict[str, ThumbnailPreviewTextSlotImageResponse] = Field(default_factory=dict)


class ThumbnailPreviewTextLayerSlotsRequest(BaseModel):
    overrides: Dict[str, Any] = Field(default_factory=dict)
    # Optional Canva-like per-line tuning (currently uses `scale` only; offsets are applied client-side).
    lines: Dict[str, ThumbnailTextLineSpecLinePayload] = Field(default_factory=dict)


class ThumbnailTwoUpBuildResponse(BaseModel):
    status: str
    channel: str
    video: str
    outputs: Dict[str, str] = Field(default_factory=dict)
    paths: Dict[str, str] = Field(default_factory=dict)


class ThumbnailLayerSpecsBuildRequest(BaseModel):
    allow_generate: bool = False
    regen_bg: bool = False
    output_mode: Literal["draft", "final"] = "draft"


class ThumbnailLayerSpecsBuildResponse(BaseModel):
    status: str
    channel: str
    video: str
    build_id: str
    thumb_url: str
    thumb_path: str
    build_meta_path: Optional[str] = None


class ThumbnailPreviewTextLayerResponse(BaseModel):
    status: str
    channel: str
    video: str
    image_url: str
    image_path: str


class ThumbnailTextSlotMetaResponse(BaseModel):
    box: Optional[List[float]] = None
    fill: Optional[str] = None
    base_size_px: Optional[int] = None
    align: Optional[str] = None
    valign: Optional[str] = None


class ThumbnailTextTemplateOptionResponse(BaseModel):
    id: str
    description: Optional[str] = None
    slots: Dict[str, ThumbnailTextSlotMetaResponse] = Field(default_factory=dict)


class ThumbnailEditorContextResponse(BaseModel):
    channel: str
    video: str
    video_id: str
    portrait_available: bool = False
    portrait_dest_box_norm: Optional[List[float]] = None
    portrait_anchor: Optional[str] = None
    template_id_default: Optional[str] = None
    template_options: List[ThumbnailTextTemplateOptionResponse] = Field(default_factory=list)
    text_slots: Dict[str, str] = Field(default_factory=dict)
    defaults_leaf: Dict[str, Any] = Field(default_factory=dict)
    overrides_leaf: Dict[str, Any] = Field(default_factory=dict)
    effective_leaf: Dict[str, Any] = Field(default_factory=dict)


class ThumbnailCommentPatchTargetResponse(BaseModel):
    channel: str
    video: str


class ThumbnailCommentPatchOpResponse(BaseModel):
    op: Literal["set", "unset"] = "set"
    path: str
    value: Optional[Any] = None
    reason: Optional[str] = None


class ThumbnailCommentPatchResponse(BaseModel):
    schema_: str = Field(default=THUMBNAIL_COMMENT_PATCH_SCHEMA_V1, alias="schema")
    target: ThumbnailCommentPatchTargetResponse
    confidence: float = 0.0
    clarifying_questions: List[str] = Field(default_factory=list)
    ops: List[ThumbnailCommentPatchOpResponse] = Field(default_factory=list)
    provider: Optional[str] = None
    model: Optional[str] = None


class ThumbnailCommentPatchRequest(BaseModel):
    comment: str
    include_thumb_caption: bool = False
