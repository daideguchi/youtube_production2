from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Literal, Optional, Tuple


EngineKey = Literal["all", "layer_specs_v3"]


@dataclass(frozen=True)
class ParamSpec:
    kind: Literal["string", "float", "int", "bool", "int2", "color"]
    engine: EngineKey = "all"
    min_value: Optional[float] = None
    max_value: Optional[float] = None


_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_RGBA_RE = re.compile(r"^rgba\\((?P<r>\\d{1,3}),(?P<g>\\d{1,3}),(?P<b>\\d{1,3}),(?P<a>\\d*\\.?\\d+)\\)$", re.IGNORECASE)


PARAM_CATALOG_V1: Dict[str, ParamSpec] = {
    # planning.* (CSV fields) - thumb_spec does not write these; used by comment patch pipeline.
    "planning.thumbnail_upper": ParamSpec(kind="string", engine="all"),
    "planning.thumbnail_title": ParamSpec(kind="string", engine="all"),
    "planning.thumbnail_lower": ParamSpec(kind="string", engine="all"),
    "planning.thumbnail_prompt": ParamSpec(kind="string", engine="all"),

    # overrides.copy_override.* (per-video exception)
    "overrides.copy_override.upper": ParamSpec(kind="string", engine="all"),
    "overrides.copy_override.title": ParamSpec(kind="string", engine="all"),
    "overrides.copy_override.lower": ParamSpec(kind="string", engine="all"),

    # overrides.bg_pan_zoom.*
    "overrides.bg_pan_zoom.zoom": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1.0),
    # NOTE: Canva-like editor allows pushing the background outside the canvas.
    # -1..1 stays within the "cover" range; wider values intentionally reveal the base fill.
    "overrides.bg_pan_zoom.pan_x": ParamSpec(kind="float", engine="layer_specs_v3", min_value=-5.0, max_value=5.0),
    "overrides.bg_pan_zoom.pan_y": ParamSpec(kind="float", engine="layer_specs_v3", min_value=-5.0, max_value=5.0),

    # overrides.bg_enhance.*
    "overrides.bg_enhance.brightness": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9),
    "overrides.bg_enhance.contrast": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9),
    "overrides.bg_enhance.color": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9),
    "overrides.bg_enhance.gamma": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9),

    # overrides.bg_enhance_band.*
    "overrides.bg_enhance_band.x0": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.bg_enhance_band.x1": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.bg_enhance_band.power": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9),
    "overrides.bg_enhance_band.brightness": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9),
    "overrides.bg_enhance_band.contrast": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9),
    "overrides.bg_enhance_band.color": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9),
    "overrides.bg_enhance_band.gamma": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9),

    # overrides.text_effects.*
    "overrides.text_effects.stroke.width_px": ParamSpec(kind="int", engine="layer_specs_v3", min_value=0, max_value=64),
    "overrides.text_effects.stroke.color": ParamSpec(kind="color", engine="layer_specs_v3"),
    "overrides.text_effects.shadow.alpha": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.text_effects.shadow.offset_px": ParamSpec(kind="int2", engine="layer_specs_v3"),
    "overrides.text_effects.shadow.blur_px": ParamSpec(kind="int", engine="layer_specs_v3", min_value=0, max_value=128),
    "overrides.text_effects.shadow.color": ParamSpec(kind="color", engine="layer_specs_v3"),
    "overrides.text_effects.glow.alpha": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.text_effects.glow.blur_px": ParamSpec(kind="int", engine="layer_specs_v3", min_value=0, max_value=128),
    "overrides.text_effects.glow.color": ParamSpec(kind="color", engine="layer_specs_v3"),

    # overrides.overlays.*
    "overrides.overlays.left_tsz.enabled": ParamSpec(kind="bool", engine="layer_specs_v3"),
    "overrides.overlays.left_tsz.color": ParamSpec(kind="color", engine="layer_specs_v3"),
    "overrides.overlays.left_tsz.alpha_left": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.overlays.left_tsz.alpha_right": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.overlays.left_tsz.x0": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.overlays.left_tsz.x1": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),

    "overrides.overlays.top_band.enabled": ParamSpec(kind="bool", engine="layer_specs_v3"),
    "overrides.overlays.top_band.color": ParamSpec(kind="color", engine="layer_specs_v3"),
    "overrides.overlays.top_band.alpha_top": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.overlays.top_band.alpha_bottom": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.overlays.top_band.y0": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.overlays.top_band.y1": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),

    "overrides.overlays.bottom_band.enabled": ParamSpec(kind="bool", engine="layer_specs_v3"),
    "overrides.overlays.bottom_band.color": ParamSpec(kind="color", engine="layer_specs_v3"),
    "overrides.overlays.bottom_band.alpha_top": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.overlays.bottom_band.alpha_bottom": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.overlays.bottom_band.y0": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),
    "overrides.overlays.bottom_band.y1": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.0, max_value=1.0),

    "overrides.text_template_id": ParamSpec(kind="string", engine="layer_specs_v3"),

    # overrides.text_* (text rendering tuning)
    "overrides.text_scale": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.5, max_value=2.0),
    # NOTE: Canva-like editor allows moving text out of frame; keep wide bounds.
    "overrides.text_offset_x": ParamSpec(kind="float", engine="layer_specs_v3", min_value=-5.0, max_value=5.0),
    "overrides.text_offset_y": ParamSpec(kind="float", engine="layer_specs_v3", min_value=-5.0, max_value=5.0),

    # overrides.text_fills.* (solid fill color overrides; keys must exist in authored specs)
    "overrides.text_fills.white_fill.color": ParamSpec(kind="color", engine="layer_specs_v3"),
    "overrides.text_fills.red_fill.color": ParamSpec(kind="color", engine="layer_specs_v3"),
    "overrides.text_fills.yellow_fill.color": ParamSpec(kind="color", engine="layer_specs_v3"),
    "overrides.text_fills.hot_red_fill.color": ParamSpec(kind="color", engine="layer_specs_v3"),
    "overrides.text_fills.purple_fill.color": ParamSpec(kind="color", engine="layer_specs_v3"),

    # overrides.portrait.* (per-video portrait composite tuning, CH26 benchmark)
    "overrides.portrait.enabled": ParamSpec(kind="bool", engine="layer_specs_v3"),
    "overrides.portrait.suppress_bg": ParamSpec(kind="bool", engine="layer_specs_v3"),
    "overrides.portrait.zoom": ParamSpec(kind="float", engine="layer_specs_v3", min_value=0.5, max_value=2.0),
    # NOTE: Canva-like editor allows moving portrait out of frame; keep wide bounds.
    "overrides.portrait.offset_x": ParamSpec(kind="float", engine="layer_specs_v3", min_value=-5.0, max_value=5.0),
    "overrides.portrait.offset_y": ParamSpec(kind="float", engine="layer_specs_v3", min_value=-5.0, max_value=5.0),
    "overrides.portrait.trim_transparent": ParamSpec(kind="bool", engine="layer_specs_v3"),
    "overrides.portrait.fg_brightness": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9, max_value=3.0),
    "overrides.portrait.fg_contrast": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9, max_value=3.0),
    "overrides.portrait.fg_color": ParamSpec(kind="float", engine="layer_specs_v3", min_value=1e-9, max_value=3.0),
}


def iter_allowed_leaf_paths_v1() -> Iterable[str]:
    return PARAM_CATALOG_V1.keys()


def validate_color(value: str) -> None:
    raw = str(value).strip()
    if _HEX_COLOR_RE.fullmatch(raw):
        return
    compact = raw.replace(" ", "")
    m = _RGBA_RE.fullmatch(compact)
    if not m:
        raise ValueError(f"invalid color (expected #RRGGBB or rgba(r,g,b,a)): {value!r}")
    r = int(m.group("r"))
    g = int(m.group("g"))
    b = int(m.group("b"))
    a = float(m.group("a"))
    if not (0 <= r <= 255 and 0 <= g <= 255 and 0 <= b <= 255):
        raise ValueError(f"rgba() rgb out of range: {value!r}")
    if not (0.0 <= a <= 1.0):
        raise ValueError(f"rgba() alpha out of range (0..1): {value!r}")


def _as_float(value: Any) -> float:
    if isinstance(value, bool):
        raise TypeError("bool is not a float")
    if isinstance(value, (int, float)):
        return float(value)
    raise TypeError(f"expected float, got {type(value).__name__}")


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        raise TypeError("bool is not an int")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise TypeError(f"expected int, got {type(value).__name__}")


def _as_int2(value: Any) -> Tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise TypeError("expected [int,int]")
    return (_as_int(value[0]), _as_int(value[1]))


def validate_param_value_v1(path: str, value: Any) -> Any:
    """
    Validate + normalize a leaf value for Parameter Catalog v1.

    Returns:
      normalized value (e.g. float/int tuple)
    """
    spec = PARAM_CATALOG_V1.get(path)
    if not spec:
        raise KeyError(f"unknown parameter path: {path}")

    if spec.kind == "string":
        if not isinstance(value, str):
            raise TypeError("expected string")
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("empty string is not allowed (use unset)")
        return cleaned

    if spec.kind == "bool":
        if not isinstance(value, bool):
            raise TypeError("expected bool")
        return bool(value)

    if spec.kind == "color":
        if not isinstance(value, str):
            raise TypeError("expected color string")
        cleaned = value.strip()
        validate_color(cleaned)
        return cleaned

    if spec.kind == "int":
        n = _as_int(value)
        if spec.min_value is not None and n < int(spec.min_value):
            raise ValueError(f"value too small: {n} < {int(spec.min_value)}")
        if spec.max_value is not None and n > int(spec.max_value):
            raise ValueError(f"value too large: {n} > {int(spec.max_value)}")
        return n

    if spec.kind == "int2":
        a, b = _as_int2(value)
        return (a, b)

    if spec.kind == "float":
        f = _as_float(value)
        if spec.min_value is not None and f < float(spec.min_value):
            raise ValueError(f"value too small: {f} < {float(spec.min_value)}")
        if spec.max_value is not None and f > float(spec.max_value):
            raise ValueError(f"value too large: {f} > {float(spec.max_value)}")
        return f

    raise ValueError(f"unsupported kind: {spec.kind}")
