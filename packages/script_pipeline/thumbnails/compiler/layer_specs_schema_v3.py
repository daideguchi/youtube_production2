#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union


class LayerSpecValidationError(ValueError):
    def __init__(self, message: str, *, pointer: str, spec_path: Optional[str] = None):
        suffix = f" @ {pointer}"
        if spec_path:
            suffix += f" ({spec_path})"
        super().__init__(f"{message}{suffix}")
        self.pointer = pointer
        self.spec_path = spec_path


_PathPart = Union[str, int]


def _ptr(parts: Sequence[_PathPart]) -> str:
    out = "$"
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
            continue
        out += f".{part}"
    return out


def _err(spec_path: Optional[str], parts: Sequence[_PathPart], message: str) -> LayerSpecValidationError:
    return LayerSpecValidationError(message, pointer=_ptr(parts), spec_path=spec_path)


def _ensure_keys(
    spec_path: Optional[str],
    parts: Sequence[_PathPart],
    mapping: Mapping[str, Any],
    *,
    required: Set[str],
    optional: Set[str],
) -> None:
    keys = set(mapping.keys())
    missing = [k for k in sorted(required) if k not in keys]
    if missing:
        raise _err(spec_path, parts, f"missing keys: {missing}")
    unknown = sorted(keys - required - optional)
    if unknown:
        raise _err(spec_path, parts, f"unknown keys: {unknown}")


def _as_mapping(spec_path: Optional[str], parts: Sequence[_PathPart], value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    raise _err(spec_path, parts, f"expected mapping, got {type(value).__name__}")


def _as_list(spec_path: Optional[str], parts: Sequence[_PathPart], value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    raise _err(spec_path, parts, f"expected list, got {type(value).__name__}")


def _as_str(spec_path: Optional[str], parts: Sequence[_PathPart], value: Any, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise _err(spec_path, parts, f"expected string, got {type(value).__name__}")
    v = value.strip()
    if not allow_empty and not v:
        raise _err(spec_path, parts, "expected non-empty string")
    return v


def _as_int(spec_path: Optional[str], parts: Sequence[_PathPart], value: Any, *, min_value: Optional[int] = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _err(spec_path, parts, f"expected int, got {type(value).__name__}")
    if min_value is not None and value < min_value:
        raise _err(spec_path, parts, f"expected int >= {min_value}")
    return int(value)


def _as_float(spec_path: Optional[str], parts: Sequence[_PathPart], value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _err(spec_path, parts, f"expected number, got {type(value).__name__}")
    return float(value)


def _as_bool(spec_path: Optional[str], parts: Sequence[_PathPart], value: Any) -> bool:
    if not isinstance(value, bool):
        raise _err(spec_path, parts, f"expected bool, got {type(value).__name__}")
    return bool(value)


def _as_list_of_str(spec_path: Optional[str], parts: Sequence[_PathPart], value: Any) -> List[str]:
    items = _as_list(spec_path, parts, value)
    out: List[str] = []
    for i, it in enumerate(items):
        out.append(_as_str(spec_path, [*parts, i], it))
    return out


def _validate_aspect(spec_path: Optional[str], parts: Sequence[_PathPart], *, w: int, h: int, aspect: str) -> None:
    a = aspect.strip()
    if a != "16:9":
        raise _err(spec_path, parts, f"unsupported aspect: {aspect} (expected '16:9')")
    ratio = w / float(h)
    exp = 16.0 / 9.0
    if abs(ratio - exp) > 0.01:
        raise _err(spec_path, parts, f"canvas ratio mismatch for aspect=16:9: w={w} h={h}")


@dataclass(frozen=True)
class CanvasSpec:
    w: int
    h: int
    aspect: str

    @property
    def size(self) -> Tuple[int, int]:
        return (self.w, self.h)


def parse_canvas_spec(value: Any, *, spec_path: Optional[str]) -> CanvasSpec:
    parts: List[_PathPart] = ["canvas"]
    m = _as_mapping(spec_path, parts, value)
    _ensure_keys(spec_path, parts, m, required={"w", "h", "aspect"}, optional=set())
    w = _as_int(spec_path, [*parts, "w"], m.get("w"), min_value=1)
    h = _as_int(spec_path, [*parts, "h"], m.get("h"), min_value=1)
    aspect = _as_str(spec_path, [*parts, "aspect"], m.get("aspect"))
    _validate_aspect(spec_path, parts, w=w, h=h, aspect=aspect)
    return CanvasSpec(w=w, h=h, aspect=aspect)


@dataclass(frozen=True)
class ImageAnchorsSpec:
    stage: str
    prop: str
    light: str


@dataclass(frozen=True)
class ImagePromptItemV3:
    video_id: str
    title: str
    person_key: str
    anchors: Optional[ImageAnchorsSpec]
    prompt_ja: str


@dataclass(frozen=True)
class ImagePromptsSpecV3:
    version: int
    name: str
    canvas: CanvasSpec
    policy: Dict[str, Any]
    items: List[ImagePromptItemV3]


def parse_image_prompts_v3(doc: Any, *, spec_path: Optional[str]) -> ImagePromptsSpecV3:
    parts: List[_PathPart] = []
    m = _as_mapping(spec_path, parts, doc)
    _ensure_keys(spec_path, parts, m, required={"version", "name", "canvas", "policy", "items"}, optional=set())
    version = _as_int(spec_path, ["version"], m.get("version"))
    if version != 3:
        raise _err(spec_path, ["version"], f"unsupported version: {version} (expected 3)")
    name = _as_str(spec_path, ["name"], m.get("name"))
    canvas = parse_canvas_spec(m.get("canvas"), spec_path=spec_path)
    policy_raw = m.get("policy")
    policy = dict(_as_mapping(spec_path, ["policy"], policy_raw))

    items_raw = _as_list(spec_path, ["items"], m.get("items"))
    items: List[ImagePromptItemV3] = []
    seen_vids: Set[str] = set()
    for i, it in enumerate(items_raw):
        ip = ["items", i]
        item = _as_mapping(spec_path, ip, it)
        _ensure_keys(
            spec_path,
            ip,
            item,
            required={"video_id", "title", "person_key", "prompt_ja"},
            optional={"anchors"},
        )
        video_id = _as_str(spec_path, [*ip, "video_id"], item.get("video_id"))
        if video_id in seen_vids:
            raise _err(spec_path, [*ip, "video_id"], f"duplicate video_id: {video_id}")
        seen_vids.add(video_id)
        title = _as_str(spec_path, [*ip, "title"], item.get("title"))
        person_key = _as_str(spec_path, [*ip, "person_key"], item.get("person_key"))
        prompt_ja = _as_str(spec_path, [*ip, "prompt_ja"], item.get("prompt_ja"))
        anchors_val = item.get("anchors")
        anchors: Optional[ImageAnchorsSpec] = None
        if anchors_val is not None:
            am = _as_mapping(spec_path, [*ip, "anchors"], anchors_val)
            _ensure_keys(spec_path, [*ip, "anchors"], am, required=set(), optional={"stage", "prop", "light"})
            stage = _as_str(spec_path, [*ip, "anchors", "stage"], am.get("stage"), allow_empty=True) if "stage" in am else ""
            prop = _as_str(spec_path, [*ip, "anchors", "prop"], am.get("prop"), allow_empty=True) if "prop" in am else ""
            light = _as_str(spec_path, [*ip, "anchors", "light"], am.get("light"), allow_empty=True) if "light" in am else ""
            anchors = ImageAnchorsSpec(stage=stage, prop=prop, light=light)
        items.append(
            ImagePromptItemV3(
                video_id=video_id,
                title=title,
                person_key=person_key,
                anchors=anchors,
                prompt_ja=prompt_ja,
            )
        )

    return ImagePromptsSpecV3(version=version, name=name, canvas=canvas, policy=policy, items=items)


@dataclass(frozen=True)
class StrokeSpec:
    color: str
    width_px: int
    join: str


@dataclass(frozen=True)
class ShadowSpec:
    color: str
    alpha: float
    offset_px: Tuple[int, int]
    blur_px: int


@dataclass(frozen=True)
class GlowSpec:
    color: str
    alpha: float
    blur_px: int


@dataclass(frozen=True)
class FillSolidSpec:
    color: str


@dataclass(frozen=True)
class FillLinearGradientSpec:
    stops: List[Tuple[str, float]]


FillSpec = Union[FillSolidSpec, FillLinearGradientSpec]


@dataclass(frozen=True)
class EffectsDefaultsSpec:
    stroke: StrokeSpec
    shadow: ShadowSpec
    glow: GlowSpec
    fills: Dict[str, FillSpec]  # e.g. gold_fill / white_fill


@dataclass(frozen=True)
class OverlayLeftTSZSpec:
    enabled: bool
    color: str
    x0: float
    x1: float
    alpha_left: float
    alpha_right: float


@dataclass(frozen=True)
class SafeZoneXSpec:
    x0: float
    x1: float


@dataclass(frozen=True)
class SafeZoneYSpec:
    y0: float
    y1: float


@dataclass(frozen=True)
class TextLayoutGlobalSpec:
    safe_left_tsz: Optional[SafeZoneXSpec]
    safe_bottom_band: Optional[SafeZoneYSpec]
    overlay_left_tsz: Optional[OverlayLeftTSZSpec]
    fonts: Dict[str, List[str]]
    effects: EffectsDefaultsSpec
    fit_rules: List[str]


@dataclass(frozen=True)
class TextSlotSpec:
    box: Tuple[float, float, float, float]
    font: str
    fill: str
    base_size_px: int
    align: str
    tracking: int
    max_lines: int
    stroke: bool
    shadow: bool
    glow: bool


@dataclass(frozen=True)
class TextTemplateSpec:
    description: str
    slots: Dict[str, TextSlotSpec]
    fallbacks: List[str]


@dataclass(frozen=True)
class TextLayoutItemV3:
    video_id: str
    title: str
    template_id: str
    fallbacks: List[str]
    text: Dict[str, str]


@dataclass(frozen=True)
class TextLayoutSpecV3:
    version: int
    name: str
    canvas: CanvasSpec
    coordinate_system: str
    global_cfg: TextLayoutGlobalSpec
    templates: Dict[str, TextTemplateSpec]
    items: List[TextLayoutItemV3]


def _parse_stroke(spec_path: Optional[str], effects: Mapping[str, Any]) -> StrokeSpec:
    parts: List[_PathPart] = ["global", "effects_defaults", "stroke"]
    m = _as_mapping(spec_path, parts, effects.get("stroke"))
    _ensure_keys(spec_path, parts, m, required={"color", "width_px"}, optional={"join"})
    color = _as_str(spec_path, [*parts, "color"], m.get("color"))
    width_px = _as_int(spec_path, [*parts, "width_px"], m.get("width_px"), min_value=0)
    join = _as_str(spec_path, [*parts, "join"], m.get("join") or "round", allow_empty=True)
    return StrokeSpec(color=color, width_px=width_px, join=join)


def _parse_shadow(spec_path: Optional[str], effects: Mapping[str, Any]) -> ShadowSpec:
    parts: List[_PathPart] = ["global", "effects_defaults", "shadow"]
    m = _as_mapping(spec_path, parts, effects.get("shadow"))
    _ensure_keys(spec_path, parts, m, required={"color", "alpha"}, optional={"offset_px", "blur_px"})
    color = _as_str(spec_path, [*parts, "color"], m.get("color"))
    alpha = _as_float(spec_path, [*parts, "alpha"], m.get("alpha"))
    alpha = max(0.0, min(1.0, alpha))
    offset = m.get("offset_px", [6, 6])
    offset_list = _as_list(spec_path, [*parts, "offset_px"], offset)
    if len(offset_list) != 2:
        raise _err(spec_path, [*parts, "offset_px"], "expected offset_px as [x,y]")
    off_x = _as_int(spec_path, [*parts, "offset_px", 0], offset_list[0])
    off_y = _as_int(spec_path, [*parts, "offset_px", 1], offset_list[1])
    blur_px = _as_int(spec_path, [*parts, "blur_px"], m.get("blur_px", 10), min_value=0)
    return ShadowSpec(color=color, alpha=alpha, offset_px=(off_x, off_y), blur_px=blur_px)


def _parse_glow(spec_path: Optional[str], effects: Mapping[str, Any]) -> GlowSpec:
    parts: List[_PathPart] = ["global", "effects_defaults", "glow"]
    m = _as_mapping(spec_path, parts, effects.get("glow"))
    _ensure_keys(spec_path, parts, m, required={"color", "alpha"}, optional={"blur_px"})
    color = _as_str(spec_path, [*parts, "color"], m.get("color"))
    alpha = _as_float(spec_path, [*parts, "alpha"], m.get("alpha"))
    alpha = max(0.0, min(1.0, alpha))
    blur_px = _as_int(spec_path, [*parts, "blur_px"], m.get("blur_px", 0), min_value=0)
    return GlowSpec(color=color, alpha=alpha, blur_px=blur_px)


def _parse_fill(spec_path: Optional[str], key: str, value: Any) -> FillSpec:
    parts: List[_PathPart] = ["global", "effects_defaults", key]
    m = _as_mapping(spec_path, parts, value)
    if "mode" not in m:
        raise _err(spec_path, parts, "fill must include 'mode'")
    mode = _as_str(spec_path, [*parts, "mode"], m.get("mode")).lower()
    if mode == "solid":
        _ensure_keys(spec_path, parts, m, required={"mode", "color"}, optional=set())
        color = _as_str(spec_path, [*parts, "color"], m.get("color"))
        return FillSolidSpec(color=color)
    if mode == "linear_gradient":
        _ensure_keys(spec_path, parts, m, required={"mode", "stops"}, optional=set())
        stops_raw = _as_list(spec_path, [*parts, "stops"], m.get("stops"))
        stops: List[Tuple[str, float]] = []
        for i, stop in enumerate(stops_raw):
            sp = [*parts, "stops", i]
            if not isinstance(stop, (list, tuple)) or len(stop) != 2:
                raise _err(spec_path, sp, "gradient stop must be [color, pos]")
            color = _as_str(spec_path, [*sp, 0], stop[0])
            pos = _as_float(spec_path, [*sp, 1], stop[1])
            if pos < 0.0 or pos > 1.0:
                raise _err(spec_path, [*sp, 1], "gradient stop pos must be 0..1")
            stops.append((color, pos))
        if not stops:
            raise _err(spec_path, [*parts, "stops"], "gradient stops cannot be empty")
        return FillLinearGradientSpec(stops=stops)
    raise _err(spec_path, [*parts, "mode"], f"unsupported fill mode: {mode}")


def _parse_effects_defaults(spec_path: Optional[str], effects_val: Any) -> EffectsDefaultsSpec:
    effects = _as_mapping(spec_path, ["global", "effects_defaults"], effects_val)
    if "stroke" not in effects or "shadow" not in effects or "glow" not in effects:
        raise _err(spec_path, ["global", "effects_defaults"], "effects_defaults must include stroke/shadow/glow")
    stroke = _parse_stroke(spec_path, effects)
    shadow = _parse_shadow(spec_path, effects)
    glow = _parse_glow(spec_path, effects)

    fills: Dict[str, FillSpec] = {}
    for k, v in effects.items():
        if k in {"stroke", "shadow", "glow"}:
            continue
        fills[k] = _parse_fill(spec_path, k, v)
    if not fills:
        raise _err(spec_path, ["global", "effects_defaults"], "effects_defaults must include at least one fill")
    return EffectsDefaultsSpec(stroke=stroke, shadow=shadow, glow=glow, fills=fills)


def _parse_safe_zones(spec_path: Optional[str], safe_val: Any) -> Tuple[Optional[SafeZoneXSpec], Optional[SafeZoneYSpec]]:
    safe = _as_mapping(spec_path, ["global", "safe_zones"], safe_val)
    allowed = {"left_TSZ", "bottom_band_safe"}
    unknown = sorted(set(safe.keys()) - allowed)
    if unknown:
        raise _err(spec_path, ["global", "safe_zones"], f"unknown keys: {unknown}")

    left = safe.get("left_TSZ")
    bottom = safe.get("bottom_band_safe")
    left_spec: Optional[SafeZoneXSpec] = None
    bottom_spec: Optional[SafeZoneYSpec] = None
    if left is not None:
        m = _as_mapping(spec_path, ["global", "safe_zones", "left_TSZ"], left)
        _ensure_keys(spec_path, ["global", "safe_zones", "left_TSZ"], m, required={"x0", "x1"}, optional=set())
        left_spec = SafeZoneXSpec(
            x0=_as_float(spec_path, ["global", "safe_zones", "left_TSZ", "x0"], m.get("x0")),
            x1=_as_float(spec_path, ["global", "safe_zones", "left_TSZ", "x1"], m.get("x1")),
        )
    if bottom is not None:
        m = _as_mapping(spec_path, ["global", "safe_zones", "bottom_band_safe"], bottom)
        _ensure_keys(spec_path, ["global", "safe_zones", "bottom_band_safe"], m, required={"y0", "y1"}, optional=set())
        bottom_spec = SafeZoneYSpec(
            y0=_as_float(spec_path, ["global", "safe_zones", "bottom_band_safe", "y0"], m.get("y0")),
            y1=_as_float(spec_path, ["global", "safe_zones", "bottom_band_safe", "y1"], m.get("y1")),
        )
    return (left_spec, bottom_spec)


def _parse_overlay_left_tsz(spec_path: Optional[str], overlays_val: Any) -> Optional[OverlayLeftTSZSpec]:
    overlays = _as_mapping(spec_path, ["global", "overlays"], overlays_val)
    left = overlays.get("left_tsz")
    if left is None:
        return None
    m = _as_mapping(spec_path, ["global", "overlays", "left_tsz"], left)
    _ensure_keys(
        spec_path,
        ["global", "overlays", "left_tsz"],
        m,
        required={"color", "x0", "x1", "alpha_left", "alpha_right"},
        optional={"enabled"},
    )
    enabled = _as_bool(spec_path, ["global", "overlays", "left_tsz", "enabled"], m.get("enabled", True))
    color = _as_str(spec_path, ["global", "overlays", "left_tsz", "color"], m.get("color"))
    x0 = _as_float(spec_path, ["global", "overlays", "left_tsz", "x0"], m.get("x0"))
    x1 = _as_float(spec_path, ["global", "overlays", "left_tsz", "x1"], m.get("x1"))
    alpha_left = _as_float(spec_path, ["global", "overlays", "left_tsz", "alpha_left"], m.get("alpha_left"))
    alpha_right = _as_float(spec_path, ["global", "overlays", "left_tsz", "alpha_right"], m.get("alpha_right"))
    return OverlayLeftTSZSpec(
        enabled=enabled,
        color=color,
        x0=x0,
        x1=x1,
        alpha_left=alpha_left,
        alpha_right=alpha_right,
    )


def _parse_global(spec_path: Optional[str], global_val: Any) -> TextLayoutGlobalSpec:
    parts: List[_PathPart] = ["global"]
    g = _as_mapping(spec_path, parts, global_val)
    _ensure_keys(
        spec_path,
        parts,
        g,
        required={"fonts", "effects_defaults"},
        optional={"safe_zones", "overlays", "fit_rules"},
    )

    fonts_val = _as_mapping(spec_path, ["global", "fonts"], g.get("fonts"))
    fonts: Dict[str, List[str]] = {}
    for k, v in fonts_val.items():
        fonts[str(k)] = _as_list_of_str(spec_path, ["global", "fonts", str(k)], v)
    if not fonts:
        raise _err(spec_path, ["global", "fonts"], "fonts cannot be empty")

    effects = _parse_effects_defaults(spec_path, g.get("effects_defaults"))
    fit_rules = _as_list_of_str(spec_path, ["global", "fit_rules"], g.get("fit_rules", [])) if "fit_rules" in g else []

    safe_left: Optional[SafeZoneXSpec] = None
    safe_bottom: Optional[SafeZoneYSpec] = None
    if "safe_zones" in g:
        safe_left, safe_bottom = _parse_safe_zones(spec_path, g.get("safe_zones"))

    overlay_left: Optional[OverlayLeftTSZSpec] = None
    if "overlays" in g:
        overlay_left = _parse_overlay_left_tsz(spec_path, g.get("overlays"))

    return TextLayoutGlobalSpec(
        safe_left_tsz=safe_left,
        safe_bottom_band=safe_bottom,
        overlay_left_tsz=overlay_left,
        fonts=fonts,
        effects=effects,
        fit_rules=fit_rules,
    )


def _parse_slot(
    spec_path: Optional[str],
    template_id: str,
    slot_name: str,
    value: Any,
    *,
    font_keys: Set[str],
    fill_keys: Set[str],
) -> TextSlotSpec:
    parts: List[_PathPart] = ["templates", template_id, "slots", slot_name]
    m = _as_mapping(spec_path, parts, value)
    _ensure_keys(
        spec_path,
        parts,
        m,
        required={"box", "font", "fill", "base_size_px", "align", "max_lines"},
        optional={"tracking", "stroke", "stroke_width_px", "shadow", "shadow_override", "glow", "valign"},
    )
    box_raw = _as_list(spec_path, [*parts, "box"], m.get("box"))
    if len(box_raw) != 4:
        raise _err(spec_path, [*parts, "box"], "box must be [x,y,w,h]")
    box = (
        _as_float(spec_path, [*parts, "box", 0], box_raw[0]),
        _as_float(spec_path, [*parts, "box", 1], box_raw[1]),
        _as_float(spec_path, [*parts, "box", 2], box_raw[2]),
        _as_float(spec_path, [*parts, "box", 3], box_raw[3]),
    )
    for j, f in enumerate(box):
        if f < -0.01 or f > 1.01:
            raise _err(spec_path, [*parts, "box", j], "box values should be within 0..1 (normalized)")

    font = _as_str(spec_path, [*parts, "font"], m.get("font"))
    if font not in font_keys:
        raise _err(spec_path, [*parts, "font"], f"unknown font key: {font}")

    fill = _as_str(spec_path, [*parts, "fill"], m.get("fill"))
    if fill not in fill_keys:
        raise _err(spec_path, [*parts, "fill"], f"unknown fill key: {fill}")

    base_size_px = _as_int(spec_path, [*parts, "base_size_px"], m.get("base_size_px"), min_value=1)
    align = _as_str(spec_path, [*parts, "align"], m.get("align")).lower()
    if align not in {"left", "center", "right"}:
        raise _err(spec_path, [*parts, "align"], f"invalid align: {align}")
    tracking = _as_int(spec_path, [*parts, "tracking"], m.get("tracking", 0))
    max_lines = _as_int(spec_path, [*parts, "max_lines"], m.get("max_lines"), min_value=1)
    stroke = _as_bool(spec_path, [*parts, "stroke"], m.get("stroke", True))
    shadow = _as_bool(spec_path, [*parts, "shadow"], m.get("shadow", True))
    glow = _as_bool(spec_path, [*parts, "glow"], m.get("glow", False))

    return TextSlotSpec(
        box=box,
        font=font,
        fill=fill,
        base_size_px=base_size_px,
        align=align,
        tracking=tracking,
        max_lines=max_lines,
        stroke=stroke,
        shadow=shadow,
        glow=glow,
    )


def parse_text_layout_v3(doc: Any, *, spec_path: Optional[str]) -> TextLayoutSpecV3:
    m = _as_mapping(spec_path, [], doc)
    _ensure_keys(
        spec_path,
        [],
        m,
        required={"version", "name", "canvas", "coordinate_system", "global", "templates", "items"},
        optional=set(),
    )
    version = _as_int(spec_path, ["version"], m.get("version"))
    if version != 3:
        raise _err(spec_path, ["version"], f"unsupported version: {version} (expected 3)")
    name = _as_str(spec_path, ["name"], m.get("name"))
    canvas = parse_canvas_spec(m.get("canvas"), spec_path=spec_path)
    coordinate_system = _as_str(spec_path, ["coordinate_system"], m.get("coordinate_system"))
    if coordinate_system != "normalized_0_to_1":
        raise _err(spec_path, ["coordinate_system"], f"unsupported coordinate_system: {coordinate_system}")
    global_cfg = _parse_global(spec_path, m.get("global"))

    templates_val = _as_mapping(spec_path, ["templates"], m.get("templates"))
    templates: Dict[str, TextTemplateSpec] = {}

    font_keys = set(global_cfg.fonts.keys())
    fill_keys = set(global_cfg.effects.fills.keys())

    for template_id_raw, tpl_val in templates_val.items():
        template_id = str(template_id_raw).strip()
        if not template_id:
            raise _err(spec_path, ["templates"], "template_id cannot be empty")
        tp = ["templates", template_id]
        tpl = _as_mapping(spec_path, tp, tpl_val)
        _ensure_keys(spec_path, tp, tpl, required={"slots"}, optional={"description", "fallbacks"})
        desc = _as_str(spec_path, [*tp, "description"], tpl.get("description") or "", allow_empty=True)
        slots_val = _as_mapping(spec_path, [*tp, "slots"], tpl.get("slots"))
        slots: Dict[str, TextSlotSpec] = {}
        for slot_name_raw, slot_val in slots_val.items():
            slot_name = str(slot_name_raw).strip()
            if not slot_name:
                raise _err(spec_path, [*tp, "slots"], "slot name cannot be empty")
            slots[slot_name] = _parse_slot(
                spec_path,
                template_id,
                slot_name,
                slot_val,
                font_keys=font_keys,
                fill_keys=fill_keys,
            )
        if not slots:
            raise _err(spec_path, [*tp, "slots"], "template slots cannot be empty")
        fallbacks = _as_list_of_str(spec_path, [*tp, "fallbacks"], tpl.get("fallbacks", []))
        templates[template_id] = TextTemplateSpec(description=desc, slots=slots, fallbacks=fallbacks)

    if not templates:
        raise _err(spec_path, ["templates"], "templates cannot be empty")

    template_ids = set(templates.keys())
    for tpl_id, tpl in templates.items():
        for fb in tpl.fallbacks:
            if fb not in template_ids:
                raise _err(spec_path, ["templates", tpl_id, "fallbacks"], f"unknown template fallback: {fb}")

    items_raw = _as_list(spec_path, ["items"], m.get("items"))
    items: List[TextLayoutItemV3] = []
    seen_vids: Set[str] = set()
    for i, it in enumerate(items_raw):
        ip = ["items", i]
        item = _as_mapping(spec_path, ip, it)
        _ensure_keys(
            spec_path,
            ip,
            item,
            required={"video_id", "title", "template_id", "text"},
            optional={"fallbacks"},
        )
        video_id = _as_str(spec_path, [*ip, "video_id"], item.get("video_id"))
        if video_id in seen_vids:
            raise _err(spec_path, [*ip, "video_id"], f"duplicate video_id: {video_id}")
        seen_vids.add(video_id)
        title = _as_str(spec_path, [*ip, "title"], item.get("title"))
        template_id = _as_str(spec_path, [*ip, "template_id"], item.get("template_id"))
        if template_id not in template_ids:
            raise _err(spec_path, [*ip, "template_id"], f"unknown template_id: {template_id}")
        fallbacks = _as_list_of_str(spec_path, [*ip, "fallbacks"], item.get("fallbacks", []))
        for fb in fallbacks:
            if fb not in template_ids:
                raise _err(spec_path, [*ip, "fallbacks"], f"unknown template fallback: {fb}")

        text_val = _as_mapping(spec_path, [*ip, "text"], item.get("text"))
        text: Dict[str, str] = {}
        for k, v in text_val.items():
            text[str(k)] = _as_str(spec_path, [*ip, "text", str(k)], v, allow_empty=True)
        if not text:
            raise _err(spec_path, [*ip, "text"], "text cannot be empty")

        items.append(
            TextLayoutItemV3(
                video_id=video_id,
                title=title,
                template_id=template_id,
                fallbacks=fallbacks,
                text=text,
            )
        )

    return TextLayoutSpecV3(
        version=version,
        name=name,
        canvas=canvas,
        coordinate_system=coordinate_system,
        global_cfg=global_cfg,
        templates=templates,
        items=items,
    )
