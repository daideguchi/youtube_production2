import json
import logging
from pathlib import Path
from typing import Dict, Optional
from ..core.domain.style_schema import VideoStyle, TextStyle

from factory_common.paths import repo_root, video_pkg_root

# Default path to the master styles SSOT (repo-level).
DEFAULT_STYLE_PATH = repo_root() / "ssot" / "ops" / "master_styles.json"
# Fallback path to local config copy
FALLBACK_STYLE_PATH = video_pkg_root() / "config" / "master_styles.json"

logger = logging.getLogger(__name__)

# When a channel preset omits `video_style_id`, we must NOT fall back to a center subtitle style.
# Default to the empirically-validated bottom subtitle style.
DEFAULT_FALLBACK_STYLE_ID = "jinsei_standard_v2"


def _as_float(value, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _as_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "y", "on"):
            return True
        if v in ("0", "false", "no", "n", "off"):
            return False
    return default


def _video_style_from_legacy_capcut(*, style_id: str, style_data: dict) -> VideoStyle:
    capcut = style_data.get("capcut_settings") or {}
    subtitle = capcut.get("subtitle") or {}
    bg = subtitle.get("background") or {}
    pos = subtitle.get("position") or {}
    timeline = capcut.get("timeline") or {}

    capcut_font_size = _as_float(subtitle.get("font_size", 5.0), 5.0)
    scale_factor = _as_float(subtitle.get("font_scale_factor", 0.1), 0.1)
    if scale_factor <= 0:
        scale_factor = 0.1

    font_size_pt = capcut_font_size / scale_factor

    capcut_x = _as_float(pos.get("x", 0.0), 0.0)
    capcut_y = _as_float(pos.get("y", 0.0), 0.0)

    text_style = TextStyle(
        font_size_pt=font_size_pt,
        text_color=str(subtitle.get("text_color", "#FFFFFF")),
        stroke_enabled=_as_float(subtitle.get("stroke_width", 0.0), 0.0) > 0.0,
        stroke_color=str(subtitle.get("stroke_color", "#000000")),
        stroke_width_pt=_as_float(subtitle.get("stroke_width", 0.0), 0.0),
        background_enabled=_as_bool(bg.get("enabled", False), False),
        background_color=str(bg.get("color", "#000000")),
        background_opacity=_as_float(bg.get("alpha", 1.0), 1.0),
        background_round_radius_ratio=_as_float(bg.get("round_radius", 0.0), 0.0),
        position_x=capcut_x,
        position_y=-capcut_y,
    )

    overrides = {
        "subtitle": {
            "font_scale_factor": scale_factor,
            "background": {
                "width": _as_float(bg.get("width", 0.28), 0.28),
                "height": _as_float(bg.get("height", 0.28), 0.28),
                "horizontal_offset": _as_float(bg.get("horizontal_offset", -1.0), -1.0),
                "vertical_offset": _as_float(bg.get("vertical_offset", -1.0), -1.0),
            },
        },
        "raw": capcut,
    }

    opening_offset_sec = _as_float(timeline.get("opening_offset_sec", 3.0), 3.0)

    return VideoStyle(
        name=str(style_data.get("name") or style_id),
        description=str(style_data.get("description") or ""),
        subtitle_style=text_style,
        structure={"opening_duration_sec": opening_offset_sec},
        platform_overrides={"capcut": overrides},
    )


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
            
        raw_styles = data.get("styles") or data.get("definitions") or {}
        if not isinstance(raw_styles, dict):
            raw_styles = {}

        for style_id, style_data in raw_styles.items():
            if not isinstance(style_data, dict):
                continue
            try:
                # New format: data matches VideoStyle schema.
                self._styles[style_id] = VideoStyle(**style_data)
                continue
            except Exception:
                pass
            try:
                # Legacy format: `capcut_settings` with empirically-validated knobs.
                self._styles[style_id] = _video_style_from_legacy_capcut(style_id=style_id, style_data=style_data)
            except Exception as exc:
                logger.warning(f"Failed to load style '{style_id}' from {path}: {exc}")

    def get_style(self, style_id: str) -> Optional[VideoStyle]:
        return self._styles.get(style_id)

    def resolve_from_preset(self, preset_style_id: Optional[str]) -> VideoStyle:
        """
        Resolve a video style from a preset's style id.

        IMPORTANT: When missing/invalid, prefer a validated bottom subtitle style
        instead of a center-positioned fallback. This prevents accidental "center-center"
        subtitles when a channel forgot to set `video_style_id`.
        """
        if preset_style_id:
            style = self._styles.get(preset_style_id)
            if style:
                return style
            logger.warning(
                "Unknown video_style_id=%s; falling back to %s",
                preset_style_id,
                DEFAULT_FALLBACK_STYLE_ID,
            )
        else:
            logger.warning(
                "Missing video_style_id; falling back to %s",
                DEFAULT_FALLBACK_STYLE_ID,
            )

        fallback = self._styles.get(DEFAULT_FALLBACK_STYLE_ID)
        if fallback:
            return fallback

        # Last-resort: safe bottom position (CapCut expects y around -0.8 for lower area).
        logger.error(
            "Fallback video style id %s not found; using hardcoded safe subtitle position",
            DEFAULT_FALLBACK_STYLE_ID,
        )
        fallback_sub = TextStyle(font_size_pt=30, text_color="#FFFFFF", position_x=0.0, position_y=0.8)
        return VideoStyle(
            name="Fallback Default",
            description=f"auto-fallback (missing/invalid style id). default={DEFAULT_FALLBACK_STYLE_ID}",
            subtitle_style=fallback_sub,
        )
