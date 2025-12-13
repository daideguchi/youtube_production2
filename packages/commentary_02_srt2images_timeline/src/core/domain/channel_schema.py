from typing import Dict, Optional, Any
from pydantic import BaseModel, Field
from core.domain.style_schema import VideoStyle

class BeltConfig(BaseModel):
    enabled: bool = True
    opening_offset: float = Field(0.0, description="Time in seconds to wait before showing belt")
    requires_config: bool = True

class ImageGenConfig(BaseModel):
    base_period: float = Field(30.0, description="Base seconds per image (target duration)")
    min_sections: int = 10

class LayoutConfig(BaseModel):
    beltTopPct: float = Field(82.0, description="Belt top position in percentage")
    beltHeightPct: float = Field(16.0, description="Belt height in percentage")
    subtitleBottomPx: int = Field(120, description="Subtitle bottom margin in pixels")
    subtitleMaxWidthPct: float = Field(80.0, description="Subtitle max width percentage")
    subtitleFontSize: int = Field(34, description="Subtitle font size")

class CapCutSettings(BaseModel):
    # This can be expanded or replaced by a reference to VideoStyle
    # For now, we map the existing structure to schema-friendly fields
    subtitle: Optional[Dict[str, Any]] = None
    timeline: Optional[Dict[str, Any]] = None

class ChannelPosition(BaseModel):
    tx: float = 0.0
    ty: float = 0.0
    scale: float = 1.0

class ChannelConfig(BaseModel):
    name: str
    status: str = "active"
    prompt_template: Optional[str] = None
    # Optional per-channel prompt/tone guidance
    prompt_suffix: Optional[str] = None
    tone_profile: Optional[str] = None
    character_note: Optional[str] = None
    style: Optional[str] = None
    capcut_template: Optional[str] = None
    
    position: ChannelPosition = Field(default_factory=ChannelPosition)
    layout: LayoutConfig = Field(default_factory=LayoutConfig)
    belt: BeltConfig = Field(default_factory=BeltConfig)
    image_generation: ImageGenConfig = Field(default_factory=ImageGenConfig)
    
    # Reference to a style ID in master_styles_v2.json
    video_style_id: Optional[str] = None
    
    # Embed full VideoStyle for strict validation, 
    # or allow partial overrides via capcut_settings for backward compat
    video_style: Optional[VideoStyle] = None
    capcut_settings: Optional[CapCutSettings] = None # Legacy/Specific overrides

    persona_required: bool = False
    image_min_bytes: int = 60000
    notes: str = ""
    # Optional belt labels default (comma separated)
    belt_labels: Optional[str] = None

class ChannelRegistry(BaseModel):
    channels: Dict[str, ChannelConfig]
