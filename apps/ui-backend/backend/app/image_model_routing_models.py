from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

IMAGE_MODEL_ROUTING_SCHEMA_V1 = "ytm.settings.image_model_routing.v1"


class ImageModelKeyInfo(BaseModel):
    key: str
    provider: str
    model_name: str


class ImageModelCatalogOption(BaseModel):
    id: str
    label: str
    provider_group: str
    variant: str
    model_key: Optional[str] = None
    enabled: bool = True
    note: Optional[str] = None


class ImageModelRoutingCatalog(BaseModel):
    thumbnail: List[ImageModelCatalogOption] = Field(default_factory=list)
    video_image: List[ImageModelCatalogOption] = Field(default_factory=list)


class ImageModelRoutingSelection(BaseModel):
    model_key: Optional[str] = None
    provider: Optional[str] = None
    model_name: Optional[str] = None
    source: str
    missing: bool = False
    blocked: bool = False
    note: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


class ChannelImageModelRouting(BaseModel):
    channel: str
    thumbnail: ImageModelRoutingSelection
    video_image: ImageModelRoutingSelection


class ImageModelRoutingResponse(BaseModel):
    schema_: str = Field(default=IMAGE_MODEL_ROUTING_SCHEMA_V1, alias="schema")
    generated_at: str
    blocked_model_keys: List[str] = Field(default_factory=list)
    models: List[ImageModelKeyInfo] = Field(default_factory=list)
    catalog: ImageModelRoutingCatalog = Field(default_factory=ImageModelRoutingCatalog)
    channels: List[ChannelImageModelRouting] = Field(default_factory=list)


class ImageModelRoutingUpdate(BaseModel):
    thumbnail_model_key: Optional[str] = None
    video_image_model_key: Optional[str] = None
