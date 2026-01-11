from typing import Any, Dict, Optional, Tuple
import logging
from ...core.domain.style_schema import VideoStyle, TextStyle

logger = logging.getLogger(__name__)

class CapCutStyleAdapter:
    """Adapts logical VideoStyle to CapCut specific parameters."""

    def __init__(self, style: VideoStyle):
        self.style = style
        self.overrides = style.platform_overrides.get("capcut", {})

    def get_subtitle_config(self) -> Dict[str, Any]:
        """Returns CapCut specific Text_style, Background, Border configuration."""
        s = self.style.subtitle_style
        o = self.overrides.get("subtitle", {})

        # Font size conversion
        # Logical pt -> CapCut size (Empirical factor approx 0.1)
        scale_factor = o.get("font_scale_factor", 0.1)
        cc_size = s.font_size_pt * scale_factor

        # Position conversion
        # Logical Y (0.0 center, 1.0 bottom) -> CapCut Y (0.0 center, -1.0 top ? No, CapCut coords are tricky)
        # In previous logic: y=-0.8 was bottom area. 
        # Let's map Logical Y=0.8 (Bottom) -> CapCut Y=-0.8 (Bottom)
        # Wait, in CapCut Y=-0.8 is actually LOWER part? 
        # Analysis showed: transform_y=-0.8. 
        # Let's trust the override or map direct.
        cc_pos_y = -s.position_y # Invert Y for CapCut? Need to verify coordinate system.
        # Actually, in our manual fix: y=-0.8 placed it at bottom.
        # If Logical 0.8 is bottom, then -0.8 matches.
        cc_pos_y = -s.position_y

        # Background
        bg_config = None
        if s.background_enabled:
            bg_o = o.get("background", {})
            bg_config = {
                "color": s.background_color,
                "alpha": s.background_opacity,
                "round_radius": s.background_round_radius_ratio,
                "style": 1,
                "width": bg_o.get("width", 0.28), # Default fallback
                "height": bg_o.get("height", 0.28),
                "horizontal_offset": bg_o.get("horizontal_offset", -1.0),
                "vertical_offset": bg_o.get("vertical_offset", -1.0)
            }

        return {
            "style": {
                "size": cc_size,
                "color": self._hex_to_rgb_tuple(s.text_color),
                "alpha": 1.0, # Text opacity usually 1
                "line_spacing": 0.02 # Default
            },
            "background": bg_config,
            "border": {
                "width": 0.08, # Fixed for now
                "alpha": 1.0,
                "color": self._hex_to_rgb_tuple(s.stroke_color)
            } if s.stroke_enabled else None,
            "position": {
                "x": s.position_x,
                "y": -s.position_y # Invert Logical Y (0.8) to CapCut Y (-0.8)
            }
        }

    def _hex_to_rgb_tuple(self, hex_str: str) -> Tuple[float, float, float]:
        """#FFFFFF -> (1.0, 1.0, 1.0)"""
        hex_str = hex_str.lstrip('#')
        return tuple(int(hex_str[i:i+2], 16)/255.0 for i in (0, 2, 4))
    
    def get_timeline_settings(self) -> Dict[str, Any]:
        return {
            "opening_offset_sec": self.style.structure.opening_duration_sec
        }
