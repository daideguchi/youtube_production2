#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml
from PIL import Image, ImageDraw, ImageFont, ImageOps

from factory_common import paths as fpaths
from script_pipeline.thumbnails.layers.image_layer import BgEnhanceParams, apply_bg_enhancements, apply_pan_zoom
from script_pipeline.tools.optional_fields_registry import OPTIONAL_FIELDS


@dataclass(frozen=True)
class ThumbText:
    upper: str
    title: str
    lower: str


def _channel_num(channel: str) -> int:
    m = re.match(r"^CH(\d+)$", str(channel).strip().upper())
    if not m:
        raise ValueError(f"Invalid channel: {channel}")
    return int(m.group(1))


def _parse_hex_color(value: str) -> Tuple[int, int, int, int]:
    v = value.strip()
    if not v.startswith("#"):
        raise ValueError(f"Not a hex color: {value}")
    v = v[1:]
    if len(v) != 6:
        raise ValueError(f"Expected #RRGGBB: {value}")
    r = int(v[0:2], 16)
    g = int(v[2:4], 16)
    b = int(v[4:6], 16)
    return (r, g, b, 255)


def _parse_rgba(value: str) -> Tuple[int, int, int, int]:
    v = value.strip().lower()
    m = re.match(r"^rgba\((\d+),(\d+),(\d+),([0-9.]+)\)$", v.replace(" ", ""))
    if not m:
        raise ValueError(f"Not an rgba() color: {value}")
    r = int(m.group(1))
    g = int(m.group(2))
    b = int(m.group(3))
    a_raw = float(m.group(4))
    a = int(round(a_raw * 255)) if a_raw <= 1 else int(round(a_raw))
    a = max(0, min(255, a))
    return (r, g, b, a)


def _parse_color(value: str) -> Tuple[int, int, int, int]:
    v = str(value).strip()
    if v.startswith("#"):
        return _parse_hex_color(v)
    if v.lower().startswith("rgba("):
        return _parse_rgba(v)
    raise ValueError(f"Unsupported color format: {value}")


def _resize_cover(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    target_w, target_h = size
    src_w, src_h = img.size
    if src_w == 0 or src_h == 0:
        raise ValueError(f"Invalid image size: {img.size}")
    scale = max(target_w / src_w, target_h / src_h)
    new_w = int(round(src_w * scale))
    new_h = int(round(src_h * scale))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = max(0, (new_w - target_w) // 2)
    top = max(0, (new_h - target_h) // 2)
    return resized.crop((left, top, left + target_w, top + target_h))


def _parse_enhance_params(value: Any) -> Optional[BgEnhanceParams]:
    if not isinstance(value, dict):
        return None
    if value.get("enabled") is False:
        return None
    try:
        return BgEnhanceParams(
            brightness=float(value.get("brightness", 1.0)),
            contrast=float(value.get("contrast", 1.0)),
            color=float(value.get("color", 1.0)),
            gamma=float(value.get("gamma", 1.0)),
        )
    except Exception:
        return None


def _apply_left_band_enhancements(
    img: Image.Image,
    *,
    x0: float,
    x1: float,
    params: BgEnhanceParams,
    power: float = 1.0,
) -> Image.Image:
    """
    Apply enhancements to the LEFT side with a smooth fade-out toward the right.

    The effect is full on the far left, then fades out between x0..x1, and is disabled on the right of x1.
    """
    if params.is_identity():
        return img

    w, h = img.size
    if w <= 0 or h <= 0:
        return img

    x0f = max(0.0, min(1.0, float(x0)))
    x1f = max(0.0, min(1.0, float(x1)))
    if x1f <= x0f + 1e-6:
        return img

    start_px = int(round(x0f * w))
    end_px = int(round(x1f * w))
    if end_px <= start_px:
        return img

    p = float(power)
    if p <= 0:
        p = 1.0

    adjusted = apply_bg_enhancements(img, params=params)

    span = max(1, end_px - start_px)
    values: List[int] = []
    for x in range(w):
        if x <= start_px:
            t = 1.0
        elif x >= end_px:
            t = 0.0
        else:
            t = 1.0 - ((x - start_px) / span)
        if abs(p - 1.0) > 1e-6:
            t = t**p
        values.append(int(round(t * 255)))

    mask_row = Image.new("L", (w, 1), 0)
    mask_row.putdata(values)
    mask = mask_row.resize((w, h))
    return Image.composite(adjusted, img, mask)


def _parse_pan_zoom(value: Any) -> Tuple[float, float, float]:
    if not isinstance(value, dict):
        return (1.0, 0.0, 0.0)
    if value.get("enabled") is False:
        return (1.0, 0.0, 0.0)
    try:
        zoom = float(value.get("zoom", 1.0))
        pan_x = float(value.get("pan_x", 0.0))
        pan_y = float(value.get("pan_y", 0.0))
    except Exception:
        return (1.0, 0.0, 0.0)
    if zoom <= 0:
        zoom = 1.0
    pan_x = max(-1.0, min(1.0, pan_x))
    pan_y = max(-1.0, min(1.0, pan_y))
    return (zoom, pan_x, pan_y)


def _discover_font_path_via_fc_list(prefer: List[str]) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["fc-list"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        return None

    best: Optional[str] = None
    for line in proc.stdout.splitlines():
        # format: /path/to/font: family:style=...
        if ":" not in line:
            continue
        font_path = line.split(":", 1)[0].strip()
        hay = line.lower()
        if any(p.lower() in hay for p in prefer):
            if Path(font_path).exists():
                best = font_path
                break
    return best


def resolve_font_path(cli_font_path: Optional[str]) -> str:
    candidates: List[Optional[str]] = [
        cli_font_path,
        os.getenv("YTM_THUMB_FONT_PATH"),
        os.getenv("BUDDHA_THUMB_FONT"),
    ]
    for c in candidates:
        if not c:
            continue
        p = Path(c).expanduser()
        if p.exists():
            return str(p)

    fc_found = _discover_font_path_via_fc_list(
        prefer=[
            "hiragino sans w7",
            "hiragino sans w6",
            "yu gothic bold",
            "noto sans cjk jp bold",
            "source han sans",
        ]
    )
    if fc_found:
        return fc_found

    raise FileNotFoundError(
        "Font not found. Set one of: --font-path / YTM_THUMB_FONT_PATH / BUDDHA_THUMB_FONT"
    )


def _load_stylepack(channel: str) -> Dict[str, Any]:
    ch = str(channel).upper()
    stylepacks_dir = fpaths.thumbnails_root() / "compiler" / "stylepacks"
    if not stylepacks_dir.exists():
        raise FileNotFoundError(f"Missing stylepacks dir: {stylepacks_dir}")

    candidates = sorted(stylepacks_dir.glob(f"{ch}_*.yaml"))
    if not candidates:
        # fallback: scan all yaml and match `channel:` field
        candidates = sorted(stylepacks_dir.glob("*.yaml"))

    for p in candidates:
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("channel", "")).upper() == ch:
            data["_stylepack_path"] = str(p)
            return data

    raise FileNotFoundError(f"No stylepack found for {channel} in {stylepacks_dir}")


def _read_planning_rows(channel: str) -> List[Dict[str, str]]:
    csv_path = fpaths.channels_csv_path(channel)
    if not csv_path.exists():
        raise FileNotFoundError(f"Missing planning CSV: {csv_path}")
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _pick_text_from_row(row: Dict[str, str]) -> ThumbText:
    upper_col = next((k for k, v in OPTIONAL_FIELDS.items() if v == "thumbnail_upper"), "サムネタイトル上")
    title_col = next((k for k, v in OPTIONAL_FIELDS.items() if v == "thumbnail_title"), "サムネタイトル")
    lower_col = next((k for k, v in OPTIONAL_FIELDS.items() if v == "thumbnail_lower"), "サムネタイトル下")

    upper = (row.get(upper_col) or "").strip()
    title = (row.get(title_col) or "").strip()
    lower = (row.get(lower_col) or "").strip()
    return ThumbText(upper=upper, title=title, lower=lower)


def _pick_video_number(row: Dict[str, str]) -> str:
    for key in ("動画番号", "video", "Video", "No.", "No"):
        v = (row.get(key) or "").strip()
        if v:
            return v
    raise KeyError("Could not find video number column in planning CSV row")


def _scale_int(value: int, scale: float, min_value: int = 1) -> int:
    return max(min_value, int(round(value * scale)))


def _measure_text(font: ImageFont.FreeTypeFont, text: str, stroke_width: int) -> Tuple[int, int]:
    if not text:
        return (0, 0)
    bbox = font.getbbox(text)
    w = (bbox[2] - bbox[0]) + (stroke_width * 2)
    h = (bbox[3] - bbox[1]) + (stroke_width * 2)
    return (max(0, w), max(0, h))


def _max_font_size_for_width(
    text: str,
    font_path: str,
    max_width: int,
    size_min: int,
    size_max: int,
    stroke_base: int,
    size_base: int,
    cache: Dict[int, ImageFont.FreeTypeFont],
) -> int:
    if not text:
        return size_min
    lo, hi = size_min, size_max
    best = size_min
    while lo <= hi:
        mid = (lo + hi) // 2
        font = cache.get(mid)
        if font is None:
            font = ImageFont.truetype(font_path, mid)
            cache[mid] = font
        sw = _scale_int(stroke_base, mid / max(1, size_base), min_value=1)
        w, _ = _measure_text(font, text, stroke_width=sw)
        if w <= max_width:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _fit_three_lines(
    text: ThumbText,
    font_path: str,
    max_width: int,
    max_height: int,
    size_min: int,
    size_base: int,
    size_max: int,
    stroke_base: int,
    line_height: float,
    gap: int,
) -> Tuple[int, int, int]:
    cache: Dict[int, ImageFont.FreeTypeFont] = {}

    s1 = _max_font_size_for_width(
        text.upper, font_path, max_width, size_min, size_max, stroke_base, size_base, cache
    )
    s2 = _max_font_size_for_width(
        text.title, font_path, max_width, size_min, size_max, stroke_base, size_base, cache
    )
    s3 = _max_font_size_for_width(
        text.lower, font_path, max_width, size_min, size_max, stroke_base, size_base, cache
    )

    def total_height(sizes: Tuple[int, int, int]) -> int:
        heights: List[int] = []
        for t, s in zip((text.upper, text.title, text.lower), sizes):
            if not t:
                heights.append(0)
                continue
            font = cache.get(s)
            if font is None:
                font = ImageFont.truetype(font_path, s)
                cache[s] = font
            sw = _scale_int(stroke_base, s / max(1, size_base), min_value=1)
            ascent, descent = font.getmetrics()
            h = int(round((ascent + descent + (sw * 2)) * line_height))
            heights.append(h)
        active_lines = sum(1 for t in (text.upper, text.title, text.lower) if t)
        gaps = max(0, active_lines - 1) * gap
        return sum(heights) + gaps

    sizes = (s1, s2, s3)
    h = total_height(sizes)
    if h <= max_height:
        return sizes

    scale = max_height / max(1, h)
    sizes = (
        max(size_min, int(s1 * scale)),
        max(size_min, int(s2 * scale)),
        max(size_min, int(s3 * scale)),
    )

    for _ in range(200):
        h = total_height(sizes)
        if h <= max_height:
            return sizes
        s1, s2, s3 = sizes
        if s1 <= size_min and s2 <= size_min and s3 <= size_min:
            return sizes
        sizes = (max(size_min, s1 - 1), max(size_min, s2 - 1), max(size_min, s3 - 1))

    return sizes


def compose_buddha_3line(
    base_image_path: Path,
    stylepack: Dict[str, Any],
    text: ThumbText,
    font_path: str,
    flip_base: bool,
    impact: bool = True,
    belt_override: Optional[bool] = None,
) -> Image.Image:
    canvas = stylepack.get("canvas") or {}
    canvas_w = int(canvas.get("width", 1280))
    canvas_h = int(canvas.get("height", 720))

    img = Image.open(base_image_path).convert("RGBA")
    if flip_base:
        img = ImageOps.mirror(img)
    img = _resize_cover(img, (canvas_w, canvas_h)).convert("RGBA")

    render_cfg = stylepack.get("render") or {}
    z, px, py = _parse_pan_zoom(render_cfg.get("base_pan_zoom"))
    if abs(float(z) - 1.0) >= 1e-6:
        img = apply_pan_zoom(img, zoom=z, pan_x=px, pan_y=py)

    layout = stylepack.get("layout") or {}
    safe = layout.get("safe_margin") or {}
    safe_left = int(safe.get("left", 56))
    safe_top = int(safe.get("top", 48))
    safe_right = int(safe.get("right", 56))
    safe_bottom = int(safe.get("bottom", 48))
    split_left_ratio = float(layout.get("split_left_ratio", 0.48))

    text_block = layout.get("text_block") or {}
    pad_left = int(text_block.get("padding_left", 28))
    pad_right = int(text_block.get("padding_right", 28))
    pad_top = int(text_block.get("padding_top", 22))
    pad_bottom = int(text_block.get("padding_bottom", 22))
    gap = int(text_block.get("gap", 10))

    # Impact tuning: make the text block larger and reduce dead space.
    if impact:
        safe_left = max(16, int(round(safe_left * 0.65)))
        safe_top = max(12, int(round(safe_top * 0.45)))
        safe_right = max(10, int(round(safe_right * 0.25)))
        safe_bottom = max(12, int(round(safe_bottom * 0.45)))

        split_left_ratio = max(0.28, split_left_ratio - 0.14)

        pad_left = max(6, int(round(pad_left * 0.25)))
        pad_right = max(6, int(round(pad_right * 0.25)))
        pad_top = max(6, int(round(pad_top * 0.25)))
        pad_bottom = max(6, int(round(pad_bottom * 0.25)))

        gap = max(1, int(round(gap * 0.20)))

    usable_w = canvas_w - safe_left - safe_right
    x_split = safe_left + int(round(usable_w * split_left_ratio))

    subj_params = _parse_enhance_params(render_cfg.get("subject_enhance"))
    if subj_params:
        band = render_cfg.get("subject_band") or {}
        try:
            band_x0 = float(band.get("x0", 0.0))
            band_x1 = float(band.get("x1", 0.0))
            band_power = float(band.get("power", 1.0))
        except Exception:
            band_x0, band_x1, band_power = 0.0, 0.0, 1.0
        if band_x1 > band_x0 + 1e-6:
            img = _apply_left_band_enhancements(
                img, x0=band_x0, x1=band_x1, params=subj_params, power=band_power
            )
        else:
            img = apply_bg_enhancements(img, params=subj_params)

    region_x0 = x_split
    region_x1 = canvas_w - safe_right
    region_y0 = safe_top
    region_y1 = canvas_h - safe_bottom

    inner_x0 = region_x0 + pad_left
    inner_x1 = region_x1 - pad_right
    inner_y0 = region_y0 + pad_top
    inner_y1 = region_y1 - pad_bottom
    inner_w = max(1, inner_x1 - inner_x0)
    inner_h = max(1, inner_y1 - inner_y0)

    typography = stylepack.get("typography") or {}
    font_cfg = typography.get("font") or {}
    size_base = int(font_cfg.get("size_base", 86))
    size_min = int(font_cfg.get("size_min", 54))
    size_max_factor = 0.34 if impact else 0.22
    size_max = max(size_base, int(round(canvas_h * size_max_factor)))

    stroke_cfg = typography.get("stroke") or {}
    stroke_base = int(stroke_cfg.get("width", 8))
    stroke_color = _parse_color(str(stroke_cfg.get("color", "#000000")))

    shadow_cfg = typography.get("shadow") or {}
    shadow_enabled = bool(shadow_cfg.get("enabled", True))
    shadow_offset_x = int(shadow_cfg.get("offset_x", 3))
    shadow_offset_y = int(shadow_cfg.get("offset_y", 3))
    shadow_color = _parse_color(str(shadow_cfg.get("color", "rgba(0,0,0,0.55)")))

    line_height = float(typography.get("line_height", 1.02))
    if impact:
        line_height = max(0.90, min(1.06, line_height * 0.94))
    belt_min_height_ratio = 0.97 if impact else 0.0

    colors = stylepack.get("colors") or {}
    c_upper = _parse_color(str(colors.get("upper", "#ff2d2d")))
    c_title = _parse_color(str(colors.get("title", "#ffd200")))
    c_lower = _parse_color(str(colors.get("lower", "#ffffff")))

    s_upper, s_title, s_lower = _fit_three_lines(
        text=text,
        font_path=font_path,
        max_width=inner_w,
        max_height=inner_h,
        size_min=size_min,
        size_base=size_base,
        size_max=size_max,
        stroke_base=stroke_base,
        line_height=line_height,
        gap=gap,
    )

    font_upper = ImageFont.truetype(font_path, s_upper)
    font_title = ImageFont.truetype(font_path, s_title)
    font_lower = ImageFont.truetype(font_path, s_lower)

    def line_metrics(font: ImageFont.FreeTypeFont, size: int) -> Tuple[int, int]:
        sw = _scale_int(stroke_base, size / max(1, size_base), min_value=1)
        ascent, descent = font.getmetrics()
        h = int(round((ascent + descent + (sw * 2)) * line_height))
        return sw, h

    sw1, h1 = line_metrics(font_upper, s_upper)
    sw2, h2 = line_metrics(font_title, s_title)
    sw3, h3 = line_metrics(font_lower, s_lower)

    active = [(text.upper, h1), (text.title, h2), (text.lower, h3)]
    active_lines = [t for t, _h in active if t]
    gaps_total = max(0, len(active_lines) - 1) * gap
    total_text_h = sum(h for t, h in active if t) + gaps_total
    y = inner_y0 + max(0, (inner_h - total_text_h) // 2)
    x = inner_x0

    belt_cfg = stylepack.get("belt") or {}
    belt_enabled = bool(belt_cfg.get("enabled", False)) if belt_override is None else belt_override
    if belt_enabled and active_lines:
        belt_color = _parse_color(str(belt_cfg.get("color", "#000000")))
        belt_opacity = float(belt_cfg.get("opacity", 0.55))
        belt_radius = int(belt_cfg.get("radius", 24))
        belt_rgba = (belt_color[0], belt_color[1], belt_color[2], int(round(belt_opacity * 255)))

        belt_x0 = region_x0
        belt_x1 = region_x1
        belt_y0 = max(region_y0, y - pad_top)
        belt_y1 = min(region_y1, y + total_text_h + pad_bottom)

        if belt_min_height_ratio > 0:
            region_h = max(1, region_y1 - region_y0)
            min_h = int(round(region_h * belt_min_height_ratio))
            cur_h = belt_y1 - belt_y0
            if cur_h < min_h:
                center = (belt_y0 + belt_y1) / 2
                new_y0 = int(round(center - (min_h / 2)))
                new_y1 = new_y0 + min_h
                if new_y0 < region_y0:
                    new_y0 = region_y0
                    new_y1 = region_y0 + min_h
                if new_y1 > region_y1:
                    new_y1 = region_y1
                    new_y0 = region_y1 - min_h
                belt_y0, belt_y1 = new_y0, new_y1

        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.rounded_rectangle((belt_x0, belt_y0, belt_x1, belt_y1), radius=belt_radius, fill=belt_rgba)
        img = Image.alpha_composite(img, overlay)

    draw = ImageDraw.Draw(img)

    def draw_line(
        line_text: str,
        font: ImageFont.FreeTypeFont,
        size: int,
        y_pos: int,
        fill: Tuple[int, int, int, int],
    ) -> int:
        if not line_text:
            return y_pos
        sw = _scale_int(stroke_base, size / max(1, size_base), min_value=1)
        sx = _scale_int(shadow_offset_x, size / max(1, size_base), min_value=0)
        sy = _scale_int(shadow_offset_y, size / max(1, size_base), min_value=0)
        if shadow_enabled and (sx or sy):
            draw.text((x + sx, y_pos + sy), line_text, font=font, fill=shadow_color)
        draw.text(
            (x, y_pos),
            line_text,
            font=font,
            fill=fill,
            stroke_width=sw,
            stroke_fill=stroke_color,
        )
        ascent, descent = font.getmetrics()
        return y_pos + int(round((ascent + descent + (sw * 2)) * line_height))

    y = draw_line(text.upper, font_upper, s_upper, y, c_upper)
    if text.upper and text.title:
        y += gap
    y = draw_line(text.title, font_title, s_title, y, c_title)
    if text.title and text.lower:
        y += gap
    y = draw_line(text.lower, font_lower, s_lower, y, c_lower)

    return img


def _iter_channels_from(ch_from: str) -> List[str]:
    start = _channel_num(ch_from)
    channels_dir = fpaths.planning_root() / "channels"
    found: List[str] = []
    for p in sorted(channels_dir.glob("CH*.csv")):
        name = p.stem.upper()
        try:
            n = _channel_num(name)
        except ValueError:
            continue
        if n >= start:
            found.append(name)
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch compose Buddha 3-line thumbnails from planning CSV.")
    parser.add_argument("--channels-from", default="CH12", help="Generate for channels >= this (default: CH12)")
    parser.add_argument("--channels", nargs="*", help="Explicit channels list (overrides --channels-from)")
    parser.add_argument("--limit-per-channel", type=int, default=5, help="How many videos per channel (default: 5)")
    parser.add_argument(
        "--base",
        default=str(fpaths.assets_root() / "thumbnails" / "CH12" / "ch12_buddha_bg_1536x1024.png"),
        help="Base background image path (default: asset/thumbnails/CH12/ch12_buddha_bg_1536x1024.png)",
    )
    parser.add_argument("--no-flip-base", action="store_true", help="Do not mirror the base image")
    parser.add_argument("--font-path", help="TTF/OTF/TTC path (recommended)")
    parser.add_argument("--no-impact", action="store_true", help="Disable tight/impact tuning")
    belt_group = parser.add_mutually_exclusive_group()
    belt_group.add_argument("--belt", action="store_true", help="Force enable belt background (stylepack override)")
    belt_group.add_argument("--no-belt", action="store_true", help="Force disable belt background (stylepack override)")
    parser.add_argument("--dry-run", action="store_true", help="Print targets only; do not write files")

    args = parser.parse_args()

    channels = [c.upper() for c in (args.channels or []) if c.strip()]
    if not channels:
        channels = _iter_channels_from(args.channels_from)

    base_path = Path(args.base).expanduser().resolve()
    if not base_path.exists():
        raise FileNotFoundError(f"Base image not found: {base_path}")

    font_path = resolve_font_path(args.font_path)

    belt_override: Optional[bool]
    if args.belt:
        belt_override = True
    elif args.no_belt:
        belt_override = False
    else:
        belt_override = None

    build_id = datetime.now(timezone.utc).strftime("build_%Y%m%dT%H%M%SZ")
    wrote: List[Path] = []

    for ch in channels:
        stylepack = _load_stylepack(ch)
        rows = _read_planning_rows(ch)

        count = 0
        for row in rows:
            text = _pick_text_from_row(row)
            if not (text.upper or text.title or text.lower):
                continue
            video = _pick_video_number(row)
            out_dir = fpaths.thumbnail_assets_dir(ch, video) / "compiler" / build_id
            out_img_path = out_dir / "out_01.png"
            out_meta_path = out_dir / "meta.json"

            if args.dry_run:
                print(f"[DRY] {ch} {str(video).zfill(3)} -> {out_img_path}")
            else:
                out_dir.mkdir(parents=True, exist_ok=True)
                img = compose_buddha_3line(
                    base_image_path=base_path,
                    stylepack=stylepack,
                    text=text,
                    font_path=font_path,
                    flip_base=not args.no_flip_base,
                    impact=not args.no_impact,
                    belt_override=belt_override,
                )
                img.convert("RGB").save(out_img_path, format="PNG", optimize=True)

                sp_belt_cfg = stylepack.get("belt") or {}
                belt_enabled = bool(sp_belt_cfg.get("enabled", False)) if belt_override is None else belt_override
                meta = {
                    "schema": "ytm.thumbnail.compiler.build.v1",
                    "built_at": datetime.now(timezone.utc).isoformat(),
                    "channel": ch,
                    "video": str(video).zfill(3),
                    "stylepack_id": stylepack.get("id"),
                    "stylepack_path": stylepack.get("_stylepack_path"),
                    "base_image": str(base_path),
                    "flip_base": not args.no_flip_base,
                    "impact": not args.no_impact,
                    "belt_enabled": belt_enabled,
                    "text": {
                        "upper": text.upper,
                        "title": text.title,
                        "lower": text.lower,
                    },
                    "output": {
                        "image": str(out_img_path),
                    },
                }
                out_meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
                wrote.append(out_img_path)
                print(f"[OK] {ch} {str(video).zfill(3)} -> {out_img_path}")

            count += 1
            if count >= args.limit_per_channel:
                break

    if not args.dry_run:
        print(f"\nDone. Generated {len(wrote)} images under workspaces/thumbnails/assets/**/compiler/{build_id}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
