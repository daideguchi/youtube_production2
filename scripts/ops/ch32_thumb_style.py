#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ch32_thumb_style.py — CH32 thumbnail live-edit style (SSOT for iterative tuning).

Design goal
-----------
Make thumbnail iteration "AI対話 → 数値変更 → 即プレビュー" possible without touching code.

This module provides:
- A JSON style file (created if missing) under:
    workspaces/thumbnails/assets/<CH>/library/style/live.json
- Helpers to apply:
  - background texture (left darkening + grain)
  - text texture (edge grain / roughness)

Notes
-----
- Deterministic: noise uses a seed and can be stabilized per-video.
- Safe defaults: if the JSON is missing/invalid, fall back to defaults.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from factory_common import paths as fpaths


STYLE_SCHEMA = "ytm.thumbnails.ch32.style.v1"


def _clampf(x: float, lo: float, hi: float) -> float:
    try:
        v = float(x)
    except Exception:
        return float(lo)
    return float(lo) if v < float(lo) else float(hi) if v > float(hi) else float(v)


def _clampi(x: int, lo: int, hi: int) -> int:
    try:
        v = int(x)
    except Exception:
        return int(lo)
    return int(lo) if v < int(lo) else int(hi) if v > int(hi) else int(v)


def _deep_update(dst: dict[str, Any], src: dict[str, Any]) -> dict[str, Any]:
    for k, v in (src or {}).items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v
    return dst


def _rng_for(seed: int) -> np.random.Generator:
    return np.random.default_rng(int(seed) & 0xFFFFFFFF)


def _noise_field(*, w: int, h: int, scale: float, seed: int) -> np.ndarray:
    """
    Return noise in [-1, +1] as float32.
    scale: >1 => larger grain (generated at lower resolution then upsampled).
    """
    w = int(max(1, w))
    h = int(max(1, h))
    scale = float(max(0.05, float(scale)))
    sw = int(max(1, round(float(w) / scale)))
    sh = int(max(1, round(float(h) / scale)))
    rng = _rng_for(int(seed))
    z = rng.standard_normal((sh, sw), dtype=np.float32)
    z = np.tanh(z / 1.25)  # stabilize outliers
    noise_small = ((z + 1.0) * 127.5).astype(np.uint8)
    noise_img = Image.fromarray(noise_small, mode="L").resize((w, h), Image.Resampling.BILINEAR)
    noise = np.array(noise_img).astype(np.float32) / 127.5 - 1.0
    return noise


def _left_fade_mask(*, w: int, left_ratio: float, fade_ratio: float) -> np.ndarray:
    """
    Return mask in [0..1] (float32), strong on the left, fading to the right.
    left_ratio: x where fading starts (0..1).
    fade_ratio: width of fade band (0..1).
    """
    w = int(max(1, w))
    left_ratio = _clampf(left_ratio, 0.0, 1.0)
    fade_ratio = _clampf(fade_ratio, 0.001, 1.0)
    x = np.linspace(0.0, 1.0, w, dtype=np.float32)
    mask = np.ones_like(x, dtype=np.float32)
    start = float(left_ratio)
    end = float(min(1.0, start + float(fade_ratio)))
    if end <= start + 1e-6:
        return mask[None, :]
    t = (x - start) / (end - start)
    mask = 1.0 - np.clip(t, 0.0, 1.0)
    return mask[None, :]


@dataclass(frozen=True)
class ShadowStyle:
    dx_ratio: float = 0.04
    blur_ratio: float = 0.02
    alpha: int = 235
    min_dx: int = 4
    min_blur: int = 2


@dataclass(frozen=True)
class LayoutStyle:
    pad_x: int = 64
    text_area_w_ratio: float = 0.62
    start_y_one_block: float = 0.30
    start_y_two_blocks: float = 0.40
    start_y_three_blocks: float = 0.20
    start_y_min: int = 70
    start_y_bias_px: int = 0
    line_gap_ratio: float = 0.08
    line_gap_min_px: int = 12
    block_gap_ratio: float = 0.14
    block_gap_min_px: int = 16


@dataclass(frozen=True)
class TypographyBlockStyle:
    start_size: int
    min_size: int
    step: int
    stroke_ratio: float
    stroke_min: int
    stroke_max: int


@dataclass(frozen=True)
class TypographyStyle:
    upper: TypographyBlockStyle = field(
        default_factory=lambda: TypographyBlockStyle(
            start_size=140, min_size=64, step=2, stroke_ratio=0.070, stroke_min=8, stroke_max=18
        )
    )
    main: TypographyBlockStyle = field(
        default_factory=lambda: TypographyBlockStyle(
            start_size=560, min_size=120, step=4, stroke_ratio=0.105, stroke_min=20, stroke_max=48
        )
    )
    lower: TypographyBlockStyle = field(
        default_factory=lambda: TypographyBlockStyle(
            start_size=520, min_size=120, step=4, stroke_ratio=0.105, stroke_min=20, stroke_max=48
        )
    )
    main_boost_short_len: int = 2
    main_boost: float = 1.35


@dataclass(frozen=True)
class BackgroundDarkenStyle:
    enabled: bool = True
    max_alpha: int = 170  # 0..255
    left_ratio: float = 0.66
    fade_ratio: float = 0.18


@dataclass(frozen=True)
class BackgroundGrainStyle:
    enabled: bool = True
    strength: float = 0.18  # 0..1 (mapped to pixel amplitude)
    scale: float = 1.6  # >1 => larger grains
    monochrome: bool = True
    left_ratio: float = 0.80
    fade_ratio: float = 0.20
    seed: int = 1337


@dataclass(frozen=True)
class TextEdgeGrainStyle:
    enabled: bool = True
    strength: float = 0.14  # 0..1 (alpha modulation)
    scale: float = 1.25
    edge_only: bool = True
    edge_kernel: int = 5  # odd, >=3
    seed: int = 7331


@dataclass(frozen=True)
class TextFillGrainStyle:
    """
    Fill texture (grunge) applied to non-black glyph pixels (RGB modulation).
    """

    enabled: bool = False
    strength: float = 0.22  # 0..1 (mapped to pixel amplitude)
    scale: float = 1.35
    seed: int = 4242
    min_brightness: int = 90  # ignore shadow/stroke
    min_chroma: int = 18  # avoid flat gray boxes


@dataclass(frozen=True)
class BoxStyle:
    """
    Optional rounded box behind text (like many reference Buddha thumbnails).
    Colors are [R,G,B,A] lists in JSON (parsed into tuples here).
    """

    enabled: bool = False
    apply_upper: bool = False
    apply_main: bool = False
    apply_lower: bool = True
    fill_rgba: tuple[int, int, int, int] = (232, 232, 232, 255)
    pad_x: int = 52
    pad_y: int = 26
    radius: int = 18
    text_rgba: tuple[int, int, int, int] = (16, 16, 16, 255)
    stroke_width: int = 0


@dataclass(frozen=True)
class PreviewStyle:
    sample_videos: tuple[str, ...] = ("001", "002", "003", "004")
    qc_cols: int = 2
    qc_rows: int = 2


@dataclass(frozen=True)
class Ch32ThumbStyle:
    schema: str = STYLE_SCHEMA
    shadow: ShadowStyle = field(default_factory=ShadowStyle)
    layout: LayoutStyle = field(default_factory=LayoutStyle)
    typography: TypographyStyle = field(default_factory=TypographyStyle)
    bg_darken: BackgroundDarkenStyle = field(default_factory=BackgroundDarkenStyle)
    bg_grain: BackgroundGrainStyle = field(default_factory=BackgroundGrainStyle)
    text_edge_grain: TextEdgeGrainStyle = field(default_factory=TextEdgeGrainStyle)
    text_fill_grain: TextFillGrainStyle = field(default_factory=TextFillGrainStyle)
    box: BoxStyle = field(default_factory=BoxStyle)
    preview: PreviewStyle = field(default_factory=PreviewStyle)


def default_style_path(channel: str) -> Path:
    ch = str(channel or "").strip().upper()
    return fpaths.thumbnails_root() / "assets" / ch / "library" / "style" / "live.json"


def _style_defaults_dict() -> dict[str, Any]:
    return asdict(Ch32ThumbStyle())


def load_style(*, channel: str, style_path: Optional[str]) -> tuple[Ch32ThumbStyle, Path]:
    """
    Load style JSON (deep-merged onto defaults). If missing, create it.
    Returns (style, resolved_path).
    """
    resolved = Path(style_path).expanduser() if str(style_path or "").strip() else default_style_path(channel)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    base = _style_defaults_dict()
    if resolved.exists():
        try:
            loaded = json.loads(resolved.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                _deep_update(base, loaded)
        except Exception:
            # keep defaults (but don't crash live tooling)
            pass
    else:
        resolved.write_text(json.dumps(base, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # Coerce + sanitize important numeric fields.
    d = base
    sh = d.get("shadow") or {}
    layout = d.get("layout") or {}
    typo = d.get("typography") or {}
    bg_darken = d.get("bg_darken") or {}
    bg_grain = d.get("bg_grain") or {}
    text_grain = d.get("text_edge_grain") or {}
    text_fill = d.get("text_fill_grain") or {}
    box = d.get("box") or {}
    preview = d.get("preview") or {}

    def _tb(v: Any, fallback: TypographyBlockStyle) -> TypographyBlockStyle:
        if not isinstance(v, dict):
            return fallback
        return TypographyBlockStyle(
            start_size=_clampi(v.get("start_size", fallback.start_size), 16, 4000),
            min_size=_clampi(v.get("min_size", fallback.min_size), 8, 4000),
            step=_clampi(v.get("step", fallback.step), 1, 64),
            stroke_ratio=_clampf(v.get("stroke_ratio", fallback.stroke_ratio), 0.01, 0.30),
            stroke_min=_clampi(v.get("stroke_min", fallback.stroke_min), 0, 256),
            stroke_max=_clampi(v.get("stroke_max", fallback.stroke_max), 0, 512),
        )

    defaults = Ch32ThumbStyle()

    def _rgba(v: Any, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        if isinstance(v, str):
            s = v.strip().lstrip("#")
            if len(s) in (6, 8):
                try:
                    r = int(s[0:2], 16)
                    g = int(s[2:4], 16)
                    b = int(s[4:6], 16)
                    a = int(s[6:8], 16) if len(s) == 8 else 255
                    return (_clampi(r, 0, 255), _clampi(g, 0, 255), _clampi(b, 0, 255), _clampi(a, 0, 255))
                except Exception:
                    return fallback
        if isinstance(v, (list, tuple)) and len(v) == 4:
            try:
                r, g, b, a = v
                return (_clampi(r, 0, 255), _clampi(g, 0, 255), _clampi(b, 0, 255), _clampi(a, 0, 255))
            except Exception:
                return fallback
        return fallback

    style = Ch32ThumbStyle(
        schema=str(d.get("schema") or STYLE_SCHEMA),
        shadow=ShadowStyle(
            dx_ratio=_clampf(sh.get("dx_ratio", defaults.shadow.dx_ratio), 0.0, 0.20),
            blur_ratio=_clampf(sh.get("blur_ratio", defaults.shadow.blur_ratio), 0.0, 0.20),
            alpha=_clampi(sh.get("alpha", defaults.shadow.alpha), 0, 255),
            min_dx=_clampi(sh.get("min_dx", defaults.shadow.min_dx), 0, 128),
            min_blur=_clampi(sh.get("min_blur", defaults.shadow.min_blur), 0, 128),
        ),
        layout=LayoutStyle(
            pad_x=_clampi(layout.get("pad_x", defaults.layout.pad_x), 0, 600),
            text_area_w_ratio=_clampf(layout.get("text_area_w_ratio", defaults.layout.text_area_w_ratio), 0.20, 0.95),
            start_y_one_block=_clampf(layout.get("start_y_one_block", defaults.layout.start_y_one_block), 0.0, 1.0),
            start_y_two_blocks=_clampf(layout.get("start_y_two_blocks", defaults.layout.start_y_two_blocks), 0.0, 1.0),
            start_y_three_blocks=_clampf(layout.get("start_y_three_blocks", defaults.layout.start_y_three_blocks), 0.0, 1.0),
            start_y_min=_clampi(layout.get("start_y_min", defaults.layout.start_y_min), 0, 400),
            start_y_bias_px=_clampi(layout.get("start_y_bias_px", defaults.layout.start_y_bias_px), -400, 400),
            line_gap_ratio=_clampf(layout.get("line_gap_ratio", defaults.layout.line_gap_ratio), 0.0, 0.40),
            line_gap_min_px=_clampi(layout.get("line_gap_min_px", defaults.layout.line_gap_min_px), 0, 200),
            block_gap_ratio=_clampf(layout.get("block_gap_ratio", defaults.layout.block_gap_ratio), 0.0, 0.60),
            block_gap_min_px=_clampi(layout.get("block_gap_min_px", defaults.layout.block_gap_min_px), 0, 400),
        ),
        typography=TypographyStyle(
            upper=_tb(typo.get("upper"), defaults.typography.upper),
            main=_tb(typo.get("main"), defaults.typography.main),
            lower=_tb(typo.get("lower"), defaults.typography.lower),
            main_boost_short_len=_clampi(typo.get("main_boost_short_len", defaults.typography.main_boost_short_len), 0, 10),
            main_boost=_clampf(typo.get("main_boost", defaults.typography.main_boost), 1.0, 2.5),
        ),
        bg_darken=BackgroundDarkenStyle(
            enabled=bool(bg_darken.get("enabled", defaults.bg_darken.enabled)),
            max_alpha=_clampi(bg_darken.get("max_alpha", defaults.bg_darken.max_alpha), 0, 255),
            left_ratio=_clampf(bg_darken.get("left_ratio", defaults.bg_darken.left_ratio), 0.0, 1.0),
            fade_ratio=_clampf(bg_darken.get("fade_ratio", defaults.bg_darken.fade_ratio), 0.001, 1.0),
        ),
        bg_grain=BackgroundGrainStyle(
            enabled=bool(bg_grain.get("enabled", defaults.bg_grain.enabled)),
            strength=_clampf(bg_grain.get("strength", defaults.bg_grain.strength), 0.0, 1.0),
            scale=_clampf(bg_grain.get("scale", defaults.bg_grain.scale), 0.2, 10.0),
            monochrome=bool(bg_grain.get("monochrome", defaults.bg_grain.monochrome)),
            left_ratio=_clampf(bg_grain.get("left_ratio", defaults.bg_grain.left_ratio), 0.0, 1.0),
            fade_ratio=_clampf(bg_grain.get("fade_ratio", defaults.bg_grain.fade_ratio), 0.001, 1.0),
            seed=_clampi(bg_grain.get("seed", defaults.bg_grain.seed), 0, 2**31 - 1),
        ),
        text_edge_grain=TextEdgeGrainStyle(
            enabled=bool(text_grain.get("enabled", defaults.text_edge_grain.enabled)),
            strength=_clampf(text_grain.get("strength", defaults.text_edge_grain.strength), 0.0, 1.0),
            scale=_clampf(text_grain.get("scale", defaults.text_edge_grain.scale), 0.2, 10.0),
            edge_only=bool(text_grain.get("edge_only", defaults.text_edge_grain.edge_only)),
            edge_kernel=_clampi(text_grain.get("edge_kernel", defaults.text_edge_grain.edge_kernel), 3, 31) | 1,
            seed=_clampi(text_grain.get("seed", defaults.text_edge_grain.seed), 0, 2**31 - 1),
        ),
        text_fill_grain=TextFillGrainStyle(
            enabled=bool(text_fill.get("enabled", defaults.text_fill_grain.enabled)),
            strength=_clampf(text_fill.get("strength", defaults.text_fill_grain.strength), 0.0, 1.0),
            scale=_clampf(text_fill.get("scale", defaults.text_fill_grain.scale), 0.2, 10.0),
            seed=_clampi(text_fill.get("seed", defaults.text_fill_grain.seed), 0, 2**31 - 1),
            min_brightness=_clampi(text_fill.get("min_brightness", defaults.text_fill_grain.min_brightness), 0, 255),
            min_chroma=_clampi(text_fill.get("min_chroma", defaults.text_fill_grain.min_chroma), 0, 255),
        ),
        box=BoxStyle(
            enabled=bool(box.get("enabled", defaults.box.enabled)),
            apply_upper=bool(box.get("apply_upper", defaults.box.apply_upper)),
            apply_main=bool(box.get("apply_main", defaults.box.apply_main)),
            apply_lower=bool(box.get("apply_lower", defaults.box.apply_lower)),
            fill_rgba=_rgba(box.get("fill_rgba"), defaults.box.fill_rgba),
            pad_x=_clampi(box.get("pad_x", defaults.box.pad_x), 0, 300),
            pad_y=_clampi(box.get("pad_y", defaults.box.pad_y), 0, 300),
            radius=_clampi(box.get("radius", defaults.box.radius), 0, 300),
            text_rgba=_rgba(box.get("text_rgba"), defaults.box.text_rgba),
            stroke_width=_clampi(box.get("stroke_width", defaults.box.stroke_width), 0, 128),
        ),
        preview=PreviewStyle(
            sample_videos=tuple(str(x).zfill(3) for x in (preview.get("sample_videos") or defaults.preview.sample_videos)),
            qc_cols=_clampi(preview.get("qc_cols", defaults.preview.qc_cols), 1, 12),
            qc_rows=_clampi(preview.get("qc_rows", defaults.preview.qc_rows), 1, 12),
        ),
    )
    return (style, resolved)


def shadow_params_for_size(size: int, style: ShadowStyle) -> tuple[int, int, int]:
    dx = max(int(style.min_dx), int(round(float(size) * float(style.dx_ratio))))
    dy = dx
    blur = max(int(style.min_blur), int(round(float(size) * float(style.blur_ratio))))
    return (int(dx), int(dy), int(blur))


def apply_background_effects(
    img: Image.Image,
    *,
    style: Ch32ThumbStyle,
    stable_key: int,
) -> Image.Image:
    """
    Apply background-only effects. Returns a new RGBA image.
    stable_key: per-thumb integer for deterministic noise (e.g. int(video)).
    """
    base = img.convert("RGBA")
    w, h = base.size

    out = np.array(base).astype(np.float32)

    # Left darkening panel (improves readability & balance).
    if style.bg_darken.enabled and int(style.bg_darken.max_alpha) > 0:
        mask = _left_fade_mask(w=w, left_ratio=style.bg_darken.left_ratio, fade_ratio=style.bg_darken.fade_ratio)
        alpha = (float(style.bg_darken.max_alpha) / 255.0) * mask  # shape (1,w)
        out[:, :, 0:3] *= (1.0 - alpha[:, :, None])

    # Grain.
    if style.bg_grain.enabled and float(style.bg_grain.strength) > 0.0:
        mask = _left_fade_mask(w=w, left_ratio=style.bg_grain.left_ratio, fade_ratio=style.bg_grain.fade_ratio)
        noise = _noise_field(w=w, h=h, scale=style.bg_grain.scale, seed=int(style.bg_grain.seed) + int(stable_key))
        amp = float(style.bg_grain.strength) * 38.0  # px amplitude
        if style.bg_grain.monochrome:
            out[:, :, 0:3] = np.clip(out[:, :, 0:3] + (noise[:, :, None] * amp * mask[:, :, None]), 0.0, 255.0)
        else:
            # Color grain: decorrelate channels with different seeds.
            r = _noise_field(w=w, h=h, scale=style.bg_grain.scale, seed=int(style.bg_grain.seed) + int(stable_key) + 101)
            g = _noise_field(w=w, h=h, scale=style.bg_grain.scale, seed=int(style.bg_grain.seed) + int(stable_key) + 202)
            b = _noise_field(w=w, h=h, scale=style.bg_grain.scale, seed=int(style.bg_grain.seed) + int(stable_key) + 303)
            out[:, :, 0] = np.clip(out[:, :, 0] + (r * amp * mask), 0.0, 255.0)
            out[:, :, 1] = np.clip(out[:, :, 1] + (g * amp * mask), 0.0, 255.0)
            out[:, :, 2] = np.clip(out[:, :, 2] + (b * amp * mask), 0.0, 255.0)

    return Image.fromarray(out.clip(0, 255).astype(np.uint8), mode="RGBA")


def apply_text_texture(
    overlay: Image.Image,
    *,
    style: Ch32ThumbStyle,
    stable_key: int,
) -> Image.Image:
    """
    Apply text-only effects to a transparent RGBA overlay (text + shadow already drawn).
    Returns a new RGBA image.
    """
    base = overlay.convert("RGBA")
    w, h = base.size
    arr = np.array(base).astype(np.uint8)
    alpha = arr[:, :, 3].astype(np.float32)
    if alpha.max() <= 0:
        return base

    # 1) Edge alpha roughness (optional).
    if style.text_edge_grain.edge_only:
        if style.text_edge_grain.enabled and float(style.text_edge_grain.strength) > 0.0:
            a_img = Image.fromarray(alpha.astype(np.uint8), mode="L")
            k = int(style.text_edge_grain.edge_kernel)
            k = 3 if k < 3 else (k | 1)
            dil = a_img.filter(ImageFilter.MaxFilter(size=k))
            ero = a_img.filter(ImageFilter.MinFilter(size=k))
            edge = ImageChops.subtract(dil, ero)
            edge_mask = np.array(edge).astype(np.float32) / 255.0
            edge_mask = np.clip(edge_mask * 1.25, 0.0, 1.0)
        else:
            edge_mask = np.zeros((h, w), dtype=np.float32)
    else:
        edge_mask = np.ones((h, w), dtype=np.float32) if (
            style.text_edge_grain.enabled and float(style.text_edge_grain.strength) > 0.0
        ) else np.zeros((h, w), dtype=np.float32)

    if style.text_edge_grain.enabled and float(style.text_edge_grain.strength) > 0.0:
        noise = _noise_field(w=w, h=h, scale=style.text_edge_grain.scale, seed=int(style.text_edge_grain.seed) + int(stable_key))
        amp = float(style.text_edge_grain.strength) * 0.40
        mod = 1.0 + (noise * amp * edge_mask)
        alpha2 = np.clip(alpha * mod, 0.0, 255.0)
        arr[:, :, 3] = alpha2.astype(np.uint8)

    # 2) Fill grunge (optional): modulate RGB only where glyph is not black.
    if style.text_fill_grain.enabled and float(style.text_fill_grain.strength) > 0.0:
        r = arr[:, :, 0].astype(np.float32)
        g = arr[:, :, 1].astype(np.float32)
        b = arr[:, :, 2].astype(np.float32)
        brightness = (r + g + b) / 3.0
        chroma = np.maximum(np.maximum(r, g), b) - np.minimum(np.minimum(r, g), b)
        mask = (
            (arr[:, :, 3].astype(np.float32) > 0.0)
            & (brightness >= float(style.text_fill_grain.min_brightness))
            & (chroma >= float(style.text_fill_grain.min_chroma))
        )
        if mask.any():
            n = _noise_field(w=w, h=h, scale=style.text_fill_grain.scale, seed=int(style.text_fill_grain.seed) + int(stable_key))
            amp_px = float(style.text_fill_grain.strength) * 34.0
            delta = (n * amp_px).astype(np.float32)
            for c in (0, 1, 2):
                ch = arr[:, :, c].astype(np.float32)
                ch[mask] = np.clip(ch[mask] + delta[mask], 0.0, 255.0)
                arr[:, :, c] = ch.astype(np.uint8)

    return Image.fromarray(arr, mode="RGBA")
