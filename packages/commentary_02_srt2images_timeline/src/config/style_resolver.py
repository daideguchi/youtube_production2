import json
from pathlib import Path
from typing import Dict, Optional
from src.core.domain.style_schema import VideoStyle, TextStyle

from factory_common.paths import repo_root, video_pkg_root

# Default path to the master styles SSOT
# Resolved relative to project root (commentary_02)
# src/config/style_resolver.py -> src/config -> src -> commentary_02 -> factory_commentary -> ssot
DEFAULT_STYLE_PATH = repo_root() / "ssot" / "ops" / "master_styles.json"
# Fallback path to local config copy
FALLBACK_STYLE_PATH = video_pkg_root() / "config" / "master_styles.json"

class StyleResolver:
    def __init__(self, config_path: Path = DEFAULT_STYLE_PATH):
        self.config_path = config_path
        self._styles: Dict[str, VideoStyle] = {}
        self._load()

    def _load(self):
        path = self.config_path
        if not path.exists() and FALLBACK_STYLE_PATH.exists():
            path = FALLBACK_STYLE_PATH
        if not path.exists():
            raise FileNotFoundError(f"Style config not found (tried): {self.config_path} | fallback={FALLBACK_STYLE_PATH}")
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        raw_styles = data.get("styles", {})
        for style_id, style_data in raw_styles.items():
            # Validate and instantiate via Pydantic
            self._styles[style_id] = VideoStyle(**style_data)

    def get_style(self, style_id: str) -> Optional[VideoStyle]:
        return self._styles.get(style_id)

    def resolve_from_preset(self, preset_style_id: Optional[str]) -> VideoStyle:
        """
        Resolve style from ID, falling back to a default or empty style if not found.
        Useful to ensure the pipeline never crashes due to missing style, 
        but logs a warning instead (handled by caller).
        """
        if preset_style_id and preset_style_id in self._styles:
            return self._styles[preset_style_id]
        
        # Return a default SAFE style if specific one not found
        # This prevents 'None' errors in adapters
        fallback_sub = TextStyle(font_size_pt=30, text_color="#FFFFFF")
        return VideoStyle(name="Fallback Default", subtitle_style=fallback_sub)
