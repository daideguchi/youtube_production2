from enum import Enum
from typing import Optional, List, Tuple
from pydantic import BaseModel, Field

# --- Enums ---

class Alignment(str, Enum):
    CENTER = "center"
    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"

class AssetFitMode(str, Enum):
    COVER = "cover"   # Fill the screen, crop if necessary
    CONTAIN = "contain" # Fit within screen, letterbox if necessary
    STRETCH = "stretch" # Distort to fill

class TransitionType(str, Enum):
    NONE = "none"
    DISSOLVE = "dissolve"
    FADE_BLACK = "fade_black"
    FADE_WHITE = "fade_white"
    SLIDE_LEFT = "slide_left"
    ZOOM_IN = "zoom_in"

# --- Typography ---

class TextStyle(BaseModel):
    font_family: str = Field("System", description="Logical font family name")
    font_size_pt: float = Field(..., description="Font size in points (logical unit)")
    text_color: str = Field("#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$", description="Text hex color")
    
    stroke_enabled: bool = False
    stroke_color: str = Field("#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    stroke_width_pt: float = 0.0
    
    background_enabled: bool = False
    background_color: str = Field("#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    background_opacity: float = Field(1.0, ge=0.0, le=1.0)
    background_padding_ratio: float = Field(0.2, description="Padding relative to font size")
    background_round_radius_ratio: float = Field(0.0, description="Corner radius relative to height")

    # Logical position (0.0 = center, -1.0 = top/left, 1.0 = bottom/right)
    position_x: float = 0.0
    position_y: float = 0.0
    alignment: Alignment = Alignment.CENTER

# --- Audio ---

class AudioMixing(BaseModel):
    bgm_volume: float = Field(1.0, ge=0.0, le=1.0, description="Master BGM volume")
    voice_volume: float = Field(1.0, ge=0.0, le=1.0, description="Master Voice volume")
    se_volume: float = Field(1.0, ge=0.0, le=1.0, description="Master SE volume")
    bgm_ducking_enabled: bool = True
    bgm_ducking_ratio: float = Field(0.2, description="BGM volume ratio when voice is active")

# --- Visuals ---

class VisualStyle(BaseModel):
    fit_mode: AssetFitMode = AssetFitMode.COVER
    zoom_animation_enabled: bool = False
    zoom_speed: float = 1.05
    default_transition: TransitionType = TransitionType.DISSOLVE
    transition_duration_sec: float = 0.5

# --- Structure ---

class TimelineStructure(BaseModel):
    opening_duration_sec: float = 3.0
    ending_duration_sec: float = 5.0
    min_scene_duration_sec: float = 2.0

# --- Master Video Style ---

class VideoStyle(BaseModel):
    name: str = Field(..., description="Style name (e.g., 'Jinsei Standard')")
    description: str = ""
    
    # Screen dimensions (Logical reference, e.g., 1920x1080)
    canvas_width: int = 1920
    canvas_height: int = 1080
    
    subtitle_style: TextStyle
    belt_style: Optional[TextStyle] = None
    
    audio: AudioMixing = Field(default_factory=AudioMixing)
    visual: VisualStyle = Field(default_factory=VisualStyle)
    structure: TimelineStructure = Field(default_factory=TimelineStructure)

    # Platform specific raw overrides (Escape hatch)
    platform_overrides: dict = Field(default_factory=dict)

