from typing import List, Optional
from pydantic import BaseModel, Field

class ImageCue(BaseModel):
    start_sec: float
    end_sec: float
    duration_sec: float
    content: str = Field(..., description="Subtitle text or scene description")
    image_prompt: str
    # Path relative to project root or absolute path
    image_path: Optional[str] = None 

class ImageCuesData(BaseModel):
    fps: int = 30
    size: dict = Field(default_factory=lambda: {"width": 1920, "height": 1080})
    crossfade: float = 0.0
    cues: List[ImageCue]

    # For legacy compatibility where cues might be dicts in JSON
    class Config:
        extra = "ignore"
