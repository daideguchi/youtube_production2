#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ch32_text_only_thumbs.py — build CH32 "text-only" thumbnail overlays (transparent PNG).

Goal:
- Render only typography (no background / no人物) so it can be composited later.
- Use the same font family as CH24 (prefer Hiragino Kaku Gothic W8 on macOS).

Inputs (SoT):
- workspaces/planning/channels/CH32.csv  (column: サムネタイトル)

Outputs (canonical):
- workspaces/thumbnails/assets/CH32/{NNN}/{NNN}_text_only.png

Notes:
- If サムネタイトル contains literal "\\n", it is treated as an explicit line break.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=True)

from factory_common import paths as fpaths  # noqa: E402
from script_pipeline.thumbnails.io_utils import save_png_atomic  # noqa: E402

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

from ch32_thumb_style import (  # noqa: E402
    Ch32ThumbStyle,
    BoxStyle,
    ShadowStyle,
    apply_background_effects,
    apply_text_texture,
    load_style,
    shadow_params_for_size,
)


REPORT_SCHEMA = "ytm.ops.thumbnails.ch32_text_only_thumbs.v1"

RGBA_WHITE = (250, 250, 252, 255)
RGBA_YELLOW = (252, 214, 76, 255)
RGBA_RED = (214, 31, 31, 255)
RGBA_BLACK = (0, 0, 0, 255)

_MAIN_RED_HINTS = (
    "毒",
    "地獄",
    "崩壊",
    "絶望",
    "破滅",
    "死",
    "闇",
    "悪",
    "貧",
    "苦",
    "罰",
)

_PARTICLES = (
    "から",
    "まで",
    "より",
    "って",
    "とは",
    "にも",
    "には",
    "でも",
    "を",
    "に",
    "へ",
    "が",
    "は",
    "も",
    "と",
    "で",
    "の",
)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_compact_utc() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _normalize_channel(ch: str) -> str:
    return str(ch or "").strip().upper()


def _normalize_video(v: str) -> str:
    digits = "".join(ch for ch in str(v or "").strip() if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {v}")
    return digits.zfill(3)


def _planning_csv_path(channel: str) -> Path:
    return fpaths.planning_root() / "channels" / f"{_normalize_channel(channel)}.csv"


def _logs_dir() -> Path:
    return fpaths.logs_root() / "ops" / "thumbnails_ch32_text_only"


def _report_path(stamp: str) -> Path:
    return _logs_dir() / f"ch32_text_only_thumbs__{stamp}.json"


def _font_path_candidates() -> list[str]:
    # Prefer CH24 policy: Noto Sans JP Black (variable font) if available in workspace,
    # otherwise fall back to heavy Japanese system fonts available on macOS by default.
    return [
        # Variable font; we set variation to "Black" explicitly.
        str(fpaths.thumbnails_root() / "assets" / "_fonts" / "NotoSansJP_wght.ttf"),
        "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]


def _pick_font_spec() -> tuple[Optional[str], Optional[str]]:
    """
    Return (font_path, variation_name).
    variation_name is used only for variable fonts like NotoSansJP_wght.ttf.
    """
    for fp in _font_path_candidates():
        if not fp:
            continue
        p = Path(fp).expanduser()
        if p.exists():
            if p.name == "NotoSansJP_wght.ttf":
                return (str(p), "Black")
            return (str(p), None)
    return (None, None)


def _apply_font_variation(font: ImageFont.FreeTypeFont, variation: Optional[str]) -> None:
    if not variation:
        return
    v = str(variation).strip()
    if not v:
        return
    if not hasattr(font, "set_variation_by_name"):
        return
    try:
        font.set_variation_by_name(v)
    except Exception:
        return


@dataclass(frozen=True)
class ThumbTextSpecItem:
    video: str
    raw_title: str
    upper_lines: tuple[str, ...]
    main_lines: tuple[str, ...]
    lower_lines: tuple[str, ...]


def _parse_title_to_lines(title: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """
    Return (upper_lines, main_lines, lower_lines).

    Heuristics:
    - If title contains literal "\\n", respect explicit line breaks.
      - 2 lines: upper=1st, main=2nd
      - >=3 lines: upper=1st, main=2nd, lower=rest
    - Else if it contains '、', split at the first '、':
      - upper=pre, main=post
    - Else: main only.
    """
    raw = str(title or "").strip()
    if not raw:
        return ((), ("（未設定）",), ())

    cooked = raw.replace("\\\\n", "\n")
    explicit_lines = [ln.strip() for ln in cooked.splitlines() if ln.strip()]
    if len(explicit_lines) >= 3:
        return ((explicit_lines[0],), (explicit_lines[1],), tuple(explicit_lines[2:]))
    if len(explicit_lines) == 2:
        return ((explicit_lines[0],), (explicit_lines[1],), ())

    line = explicit_lines[0] if explicit_lines else cooked.strip()
    if "、" in line:
        pre, post = line.split("、", 1)
        pre = pre.strip()
        post = post.strip()
        if pre and post:
            return ((pre,), (post,), ())
        if post:
            return ((), (post,), ())
        if pre:
            return ((), (pre,), ())
    return ((), (line,), ())


def _should_use_red_for_main(text: str) -> bool:
    t = str(text or "").strip()
    if not t:
        return False
    if any(h in t for h in _MAIN_RED_HINTS):
        return True
    if len(t) <= 2:
        return True
    return False


def _stroke_for_size(size: int, *, ratio: float = 0.09, min_px: int = 10, max_px: int = 32) -> int:
    return max(int(min_px), min(int(max_px), int(round(float(size) * float(ratio)))))


def _shadow_params_for_size(size: int, shadow: ShadowStyle) -> tuple[int, int, int]:
    return shadow_params_for_size(int(size), shadow)


def _fit_font_for_lines(
    *,
    draw: ImageDraw.ImageDraw,
    font_path: str,
    font_variation: Optional[str],
    lines: Sequence[str],
    max_w: int,
    start_size: int,
    min_size: int,
    step: int,
    stroke_ratio: float,
    stroke_min: int,
    stroke_max: int,
) -> tuple[ImageFont.FreeTypeFont, int]:
    size = int(start_size)
    while size >= int(min_size):
        font = ImageFont.truetype(font_path, int(size))
        _apply_font_variation(font, font_variation)
        stroke_width = _stroke_for_size(int(size), ratio=float(stroke_ratio), min_px=int(stroke_min), max_px=int(stroke_max))
        ok = True
        for raw in lines:
            line = str(raw or "").strip()
            if not line:
                continue
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=int(stroke_width))
            if (bbox[2] - bbox[0]) > int(max_w):
                ok = False
                break
        if ok:
            return (font, int(stroke_width))
        size -= int(step)

    font = ImageFont.truetype(font_path, int(min_size))
    _apply_font_variation(font, font_variation)
    stroke_width = _stroke_for_size(int(min_size), ratio=float(stroke_ratio), min_px=int(stroke_min), max_px=int(stroke_max))
    return (font, int(stroke_width))


def _split_by_particle(line: str) -> tuple[str, str]:
    s = str(line or "").strip()
    if not s:
        return ("", "")
    for p in _PARTICLES:
        idx = s.find(p)
        if idx <= 0:
            continue
        if idx >= len(s) - len(p):
            continue
        return (s[:idx], s[idx:])
    return (s, "")


def _paste_shadow_text(
    *,
    base: Image.Image,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    stroke_width: int,
    shadow_dx: int,
    shadow_dy: int,
    shadow_blur: int,
    shadow_alpha: int = 235,
) -> None:
    if not str(text or "").strip():
        return
    draw = ImageDraw.Draw(base)
    bbox = draw.textbbox((int(x), int(y)), str(text), font=font, stroke_width=int(stroke_width))
    margin = int(stroke_width) + int(shadow_blur) + max(abs(int(shadow_dx)), abs(int(shadow_dy))) + 6
    x0 = max(0, int(bbox[0]) - margin)
    y0 = max(0, int(bbox[1]) - margin)
    x1 = min(base.size[0], int(bbox[2]) + margin)
    y1 = min(base.size[1], int(bbox[3]) + margin)
    if x1 <= x0 or y1 <= y0:
        return
    layer = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.text(
        (int(x) - x0 + int(shadow_dx), int(y) - y0 + int(shadow_dy)),
        str(text),
        font=font,
        fill=(0, 0, 0, int(shadow_alpha)),
        stroke_width=int(stroke_width),
        stroke_fill=(0, 0, 0, int(shadow_alpha)),
    )
    if int(shadow_blur) > 0:
        layer = layer.filter(ImageFilter.GaussianBlur(radius=int(shadow_blur)))
    base.alpha_composite(layer, (x0, y0))


def _draw_line(
    *,
    base: Image.Image,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    fill_rgba: tuple[int, int, int, int],
    stroke_width: int,
    shadow: ShadowStyle,
) -> None:
    s = str(text or "").strip()
    if not s:
        return
    if int(shadow.alpha) > 0:
        dx, dy, blur = _shadow_params_for_size(int(font.size), shadow)
        _paste_shadow_text(
            base=base,
            x=int(x),
            y=int(y),
            text=s,
            font=font,
            stroke_width=int(stroke_width),
            shadow_dx=int(dx),
            shadow_dy=int(dy),
            shadow_blur=int(blur),
            shadow_alpha=int(shadow.alpha),
        )
    draw = ImageDraw.Draw(base)
    draw.text(
        (int(x), int(y)),
        s,
        font=font,
        fill=fill_rgba,
        stroke_width=int(stroke_width),
        stroke_fill=RGBA_BLACK,
    )


def _draw_mixed_line(
    *,
    base: Image.Image,
    x: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont,
    stroke_width: int,
    shadow: ShadowStyle,
) -> None:
    s = str(text or "").strip()
    if not s:
        return

    pre, post = _split_by_particle(s)
    if not post:
        _draw_line(
            base=base,
            x=int(x),
            y=int(y),
            text=s,
            font=font,
            fill_rgba=RGBA_RED,
            stroke_width=int(stroke_width),
            shadow=shadow,
        )
        return

    if int(shadow.alpha) > 0:
        dx, dy, blur = _shadow_params_for_size(int(font.size), shadow)
        _paste_shadow_text(
            base=base,
            x=int(x),
            y=int(y),
            text=s,
            font=font,
            stroke_width=int(stroke_width),
            shadow_dx=int(dx),
            shadow_dy=int(dy),
            shadow_blur=int(blur),
            shadow_alpha=int(shadow.alpha),
        )

    draw = ImageDraw.Draw(base)
    cur_x = int(x)
    draw.text(
        (cur_x, int(y)),
        pre,
        font=font,
        fill=RGBA_RED,
        stroke_width=int(stroke_width),
        stroke_fill=RGBA_BLACK,
    )
    pre_bbox = draw.textbbox((0, 0), pre, font=font, stroke_width=int(stroke_width))
    cur_x += int(pre_bbox[2] - pre_bbox[0])
    draw.text(
        (cur_x, int(y)),
        post,
        font=font,
        fill=RGBA_WHITE,
        stroke_width=int(stroke_width),
        stroke_fill=RGBA_BLACK,
    )


def _render_block(
    *,
    base: Image.Image,
    x: int,
    y: int,
    max_w: int,
    lines: Sequence[str],
    font_path: str,
    font_variation: Optional[str],
    start_size: int,
    min_size: int,
    step: int,
    fill_rgba: tuple[int, int, int, int],
    stroke_ratio: float,
    stroke_min: int,
    stroke_max: int,
    shadow: ShadowStyle,
    line_gap_ratio: float,
    line_gap_min_px: int,
    box: BoxStyle | None = None,
    mixed: bool = False,
    font_override: ImageFont.FreeTypeFont | None = None,
    stroke_width_override: int | None = None,
) -> None:
    draw = ImageDraw.Draw(base)
    if font_override is not None and stroke_width_override is not None:
        font = font_override
        stroke_width = int(stroke_width_override)
    else:
        font, stroke_width = _fit_font_for_lines(
            draw=draw,
            font_path=font_path,
            font_variation=font_variation,
            lines=lines,
            max_w=int(max_w),
            start_size=int(start_size),
            min_size=int(min_size),
            step=int(step),
            stroke_ratio=float(stroke_ratio),
            stroke_min=int(stroke_min),
            stroke_max=int(stroke_max),
        )
    line_gap_px = max(int(line_gap_min_px), int(round(float(font.size) * float(line_gap_ratio))))
    cur_y = int(y)
    box_style = box if (box is not None and bool(getattr(box, "enabled", False))) else None
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            cur_y += int(font.size * 0.92) + int(line_gap_px)
            continue
        if box_style is not None:
            d = ImageDraw.Draw(base)
            bb = d.textbbox((int(x), int(cur_y)), line, font=font, stroke_width=int(box_style.stroke_width))
            rx0 = max(0, int(bb[0]) - int(box_style.pad_x))
            ry0 = max(0, int(bb[1]) - int(box_style.pad_y))
            rx1 = min(base.size[0], int(bb[2]) + int(box_style.pad_x))
            ry1 = min(base.size[1], int(bb[3]) + int(box_style.pad_y))
            if rx1 > rx0 and ry1 > ry0:
                d.rounded_rectangle(
                    (rx0, ry0, rx1, ry1),
                    radius=int(box_style.radius),
                    fill=box_style.fill_rgba,
                )
        if mixed and box_style is None:
            _draw_mixed_line(
                base=base,
                x=int(x),
                y=int(cur_y),
                text=line,
                font=font,
                stroke_width=int(box_style.stroke_width if box_style is not None else stroke_width),
                shadow=shadow,
            )
        else:
            _draw_line(
                base=base,
                x=int(x),
                y=int(cur_y),
                text=line,
                font=font,
                fill_rgba=(box_style.text_rgba if box_style is not None else fill_rgba),
                stroke_width=int(box_style.stroke_width if box_style is not None else stroke_width),
                shadow=shadow,
            )
        cur_y += int(font.size * 0.92) + int(line_gap_px)


def _block_height(font: ImageFont.FreeTypeFont, lines: Sequence[str], *, line_gap_ratio: float, line_gap_min_px: int) -> int:
    n = sum(1 for ln in lines if str(ln or "").strip())
    if n <= 0:
        return 0
    line_gap_px = max(int(line_gap_min_px), int(round(float(font.size) * float(line_gap_ratio))))
    line_step = int(font.size * 0.92) + int(line_gap_px)
    return int(n * line_step - line_gap_px)


def _inter_block_gap(
    a: ImageFont.FreeTypeFont,
    b: ImageFont.FreeTypeFont,
    *,
    block_gap_ratio: float,
    block_gap_min_px: int,
) -> int:
    return max(int(block_gap_min_px), int(round(min(float(a.size), float(b.size)) * float(block_gap_ratio))))


def _iter_text_spec_from_planning(*, channel: str, limit_videos: set[str] | None) -> Iterable[ThumbTextSpecItem]:
    path = _planning_csv_path(channel)
    if not path.exists():
        raise SystemExit(f"[MISSING] planning csv: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if _normalize_channel(row.get("チャンネル") or row.get("channel") or channel) != _normalize_channel(channel):
                continue
            video = _normalize_video(row.get("動画番号") or row.get("video") or "")
            if limit_videos and video not in limit_videos:
                continue
            raw_title = str(row.get("サムネタイトル") or "").strip()
            if not raw_title:
                continue
            upper_lines, main_lines, lower_lines = _parse_title_to_lines(raw_title)
            yield ThumbTextSpecItem(
                video=video,
                raw_title=raw_title,
                upper_lines=tuple(upper_lines),
                main_lines=tuple(main_lines),
                lower_lines=tuple(lower_lines),
            )


def _compose_00_thumb(*, dir_path: Path, overlay: Image.Image, style: Ch32ThumbStyle, run: bool) -> bool:
    if not run:
        return False
    vid = str(dir_path.name).strip()
    bg_candidates = [
        dir_path / f"10_bg.{vid}_user.png",
        dir_path / f"10_bg.{vid}.png",
        dir_path / "10_bg.png",
    ]
    bg_path = next((p for p in bg_candidates if p.exists()), None)
    if bg_path is None:
        return False

    with Image.open(bg_path) as im:
        bg = im.convert("RGBA")
    if bg.size != overlay.size:
        bg = bg.resize(overlay.size, Image.Resampling.LANCZOS)
    try:
        bg = apply_background_effects(bg, style=style, stable_key=int("".join(ch for ch in vid if ch.isdigit()) or "0"))
    except Exception:
        pass
    bg.alpha_composite(overlay)
    out_path = dir_path / "00_thumb.png"
    save_png_atomic(bg.convert("RGB"), out_path, mode="final", verify=True)
    return True


def _make_text_only_png(
    *,
    out_path: Path,
    item: ThumbTextSpecItem,
    font_path: str,
    font_variation: Optional[str],
    style: Ch32ThumbStyle,
    run: bool,
    compose: bool,
) -> bool:
    w, h = 1920, 1080
    pad = int(style.layout.pad_x)
    text_w = int(w * float(style.layout.text_area_w_ratio))

    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    max_w = max(1, int(text_w - pad * 2))

    draw = ImageDraw.Draw(base)
    main_text = " ".join([str(x).strip() for x in item.main_lines if str(x).strip()])
    main_fill = RGBA_RED if _should_use_red_for_main(main_text) else RGBA_YELLOW

    layout_scale = 1.0
    font_upper: ImageFont.FreeTypeFont | None = None
    font_main: ImageFont.FreeTypeFont | None = None
    font_lower: ImageFont.FreeTypeFont | None = None
    sw_upper: int | None = None
    sw_main: int | None = None
    sw_lower: int | None = None

    for attempt in range(4):
        font_upper = font_main = font_lower = None
        sw_upper = sw_main = sw_lower = None

        if item.upper_lines:
            font_upper, sw_upper = _fit_font_for_lines(
                draw=draw,
                font_path=font_path,
                font_variation=font_variation,
                lines=list(item.upper_lines),
                max_w=int(max_w),
                start_size=int(round(style.typography.upper.start_size * layout_scale)),
                min_size=int(style.typography.upper.min_size),
                step=int(style.typography.upper.step),
                stroke_ratio=float(style.typography.upper.stroke_ratio),
                stroke_min=int(style.typography.upper.stroke_min),
                stroke_max=int(style.typography.upper.stroke_max),
            )

        if item.main_lines:
            main_boost = 1.0
            if len(main_text) <= int(style.typography.main_boost_short_len):
                main_boost = float(style.typography.main_boost)
            font_main, sw_main = _fit_font_for_lines(
                draw=draw,
                font_path=font_path,
                font_variation=font_variation,
                lines=list(item.main_lines),
                max_w=int(max_w),
                start_size=int(round(style.typography.main.start_size * layout_scale * main_boost)),
                min_size=int(style.typography.main.min_size),
                step=int(style.typography.main.step),
                stroke_ratio=float(style.typography.main.stroke_ratio),
                stroke_min=int(style.typography.main.stroke_min),
                stroke_max=int(style.typography.main.stroke_max),
            )

        if item.lower_lines:
            font_lower, sw_lower = _fit_font_for_lines(
                draw=draw,
                font_path=font_path,
                font_variation=font_variation,
                lines=list(item.lower_lines),
                max_w=int(max_w),
                start_size=int(round(style.typography.lower.start_size * layout_scale)),
                min_size=int(style.typography.lower.min_size),
                step=int(style.typography.lower.step),
                stroke_ratio=float(style.typography.lower.stroke_ratio),
                stroke_min=int(style.typography.lower.stroke_min),
                stroke_max=int(style.typography.lower.stroke_max),
            )

        blocks: list[tuple[Sequence[str], ImageFont.FreeTypeFont]] = []
        if font_upper is not None and sw_upper is not None:
            blocks.append((list(item.upper_lines), font_upper))
        if font_main is not None and sw_main is not None:
            blocks.append((list(item.main_lines), font_main))
        if font_lower is not None and sw_lower is not None:
            blocks.append((list(item.lower_lines), font_lower))

        total_h = 0
        prev_font: ImageFont.FreeTypeFont | None = None
        for lines, fnt in blocks:
            if prev_font is not None:
                total_h += _inter_block_gap(
                    prev_font,
                    fnt,
                    block_gap_ratio=float(style.layout.block_gap_ratio),
                    block_gap_min_px=int(style.layout.block_gap_min_px),
                )
            total_h += _block_height(
                fnt,
                lines,
                line_gap_ratio=float(style.layout.line_gap_ratio),
                line_gap_min_px=int(style.layout.line_gap_min_px),
            )
            prev_font = fnt

        if total_h <= int(h * 0.92) or attempt >= 3:
            break
        layout_scale *= 0.92

    blocks = []
    if font_upper is not None and sw_upper is not None:
        blocks.append((list(item.upper_lines), font_upper))
    if font_main is not None and sw_main is not None:
        blocks.append((list(item.main_lines), font_main))
    if font_lower is not None and sw_lower is not None:
        blocks.append((list(item.lower_lines), font_lower))

    total_h = 0
    prev_font = None
    for lines, fnt in blocks:
        if prev_font is not None:
            total_h += _inter_block_gap(
                prev_font,
                fnt,
                block_gap_ratio=float(style.layout.block_gap_ratio),
                block_gap_min_px=int(style.layout.block_gap_min_px),
            )
        total_h += _block_height(
            fnt,
            lines,
            line_gap_ratio=float(style.layout.line_gap_ratio),
            line_gap_min_px=int(style.layout.line_gap_min_px),
        )
        prev_font = fnt
    free_h = float(h - max(1, total_h))
    if len(blocks) == 1:
        factor = float(style.layout.start_y_one_block)
    elif len(blocks) == 2:
        factor = float(style.layout.start_y_two_blocks)
    else:
        factor = float(style.layout.start_y_three_blocks)
    start_y = int(max(int(style.layout.start_y_min), round(free_h * factor) + int(style.layout.start_y_bias_px)))
    cur_y = start_y

    if item.upper_lines and font_upper is not None and sw_upper is not None:
        _render_block(
            base=base,
            x=pad,
            y=cur_y,
            max_w=max_w,
            lines=list(item.upper_lines),
            font_path=font_path,
            font_variation=font_variation,
            start_size=int(round(style.typography.upper.start_size * layout_scale)),
            min_size=int(style.typography.upper.min_size),
            step=int(style.typography.upper.step),
            fill_rgba=RGBA_WHITE,
            stroke_ratio=float(style.typography.upper.stroke_ratio),
            stroke_min=int(style.typography.upper.stroke_min),
            stroke_max=int(style.typography.upper.stroke_max),
            shadow=style.shadow,
            line_gap_ratio=float(style.layout.line_gap_ratio),
            line_gap_min_px=int(style.layout.line_gap_min_px),
            box=(style.box if bool(style.box.apply_upper) else None),
            font_override=font_upper,
            stroke_width_override=int(sw_upper),
        )
        cur_y += _block_height(
            font_upper,
            list(item.upper_lines),
            line_gap_ratio=float(style.layout.line_gap_ratio),
            line_gap_min_px=int(style.layout.line_gap_min_px),
        )

    if item.upper_lines and item.main_lines and font_upper is not None and font_main is not None:
        cur_y += _inter_block_gap(
            font_upper,
            font_main,
            block_gap_ratio=float(style.layout.block_gap_ratio),
            block_gap_min_px=int(style.layout.block_gap_min_px),
        )

    if item.main_lines and font_main is not None and sw_main is not None:
        main_boost = 1.0
        if len(main_text) <= int(style.typography.main_boost_short_len):
            main_boost = float(style.typography.main_boost)
        _render_block(
            base=base,
            x=pad,
            y=cur_y,
            max_w=max_w,
            lines=list(item.main_lines),
            font_path=font_path,
            font_variation=font_variation,
            start_size=int(round(style.typography.main.start_size * layout_scale * main_boost)),
            min_size=int(style.typography.main.min_size),
            step=int(style.typography.main.step),
            fill_rgba=main_fill,
            stroke_ratio=float(style.typography.main.stroke_ratio),
            stroke_min=int(style.typography.main.stroke_min),
            stroke_max=int(style.typography.main.stroke_max),
            shadow=style.shadow,
            line_gap_ratio=float(style.layout.line_gap_ratio),
            line_gap_min_px=int(style.layout.line_gap_min_px),
            box=(style.box if bool(style.box.apply_main) else None),
            font_override=font_main,
            stroke_width_override=int(sw_main),
        )
        cur_y += _block_height(
            font_main,
            list(item.main_lines),
            line_gap_ratio=float(style.layout.line_gap_ratio),
            line_gap_min_px=int(style.layout.line_gap_min_px),
        )

    if item.main_lines and item.lower_lines and font_main is not None and font_lower is not None:
        cur_y += _inter_block_gap(
            font_main,
            font_lower,
            block_gap_ratio=float(style.layout.block_gap_ratio),
            block_gap_min_px=int(style.layout.block_gap_min_px),
        )

    if item.lower_lines and font_lower is not None and sw_lower is not None:
        _render_block(
            base=base,
            x=pad,
            y=cur_y,
            max_w=max_w,
            lines=list(item.lower_lines),
            font_path=font_path,
            font_variation=font_variation,
            start_size=int(round(style.typography.lower.start_size * layout_scale)),
            min_size=int(style.typography.lower.min_size),
            step=int(style.typography.lower.step),
            fill_rgba=RGBA_RED,
            stroke_ratio=float(style.typography.lower.stroke_ratio),
            stroke_min=int(style.typography.lower.stroke_min),
            stroke_max=int(style.typography.lower.stroke_max),
            mixed=True,
            shadow=style.shadow,
            line_gap_ratio=float(style.layout.line_gap_ratio),
            line_gap_min_px=int(style.layout.line_gap_min_px),
            box=(style.box if bool(style.box.apply_lower) else None),
            font_override=font_lower,
            stroke_width_override=int(sw_lower),
        )

    if not run:
        return False
    try:
        base = apply_text_texture(base, style=style, stable_key=int(item.video))
    except Exception:
        pass
    save_png_atomic(base, out_path, mode="final", verify=True)
    if bool(compose):
        return _compose_00_thumb(dir_path=out_path.parent, overlay=base, style=style, run=True)
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build CH32 text-only thumbnail overlays (transparent PNG).")
    ap.add_argument("--channel", default="CH32")
    ap.add_argument("--videos", nargs="*", default=None, help="limit videos (e.g. 001 002). default: all in planning")
    ap.add_argument("--style", default="", help="style JSON path (default: CH32 library/style/live.json; auto-created)")
    ap.add_argument("--run", action="store_true", help="actually write outputs")
    ap.add_argument("--compose", action="store_true", help="also write 00_thumb.png by compositing onto 10_bg.* if present")
    args = ap.parse_args(argv)

    channel = _normalize_channel(args.channel)
    limit_videos = None
    if args.videos:
        limit_videos = {_normalize_video(v) for v in args.videos}

    style, style_path = load_style(channel=channel, style_path=str(args.style or "").strip() or None)

    font_path, font_variation = _pick_font_spec()
    if not font_path:
        raise SystemExit("Japanese-capable font not found on this host")

    items = list(_iter_text_spec_from_planning(channel=channel, limit_videos=limit_videos))
    if not items:
        print("[WARN] no items found (check planning CSV / filters)")
        return 0

    stamp = _now_compact_utc()
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": _now_iso_utc(),
        "channel": channel,
        "font_path": font_path,
        "font_variation": font_variation,
        "style_path": str(style_path),
        "run": bool(args.run),
        "items": [],
    }

    ok = 0
    composed = 0
    for it in items:
        out_dir = fpaths.thumbnail_assets_dir(channel, it.video)
        out_path = out_dir / f"{it.video}_text_only.png"
        did_compose = _make_text_only_png(
            out_path=out_path,
            item=it,
            font_path=font_path,
            font_variation=font_variation,
            style=style,
            run=bool(args.run),
            compose=bool(args.compose),
        )
        ok += 1
        if did_compose:
            composed += 1
        report["items"].append(
            {
                "video": it.video,
                "title": it.raw_title,
                "upper_lines": list(it.upper_lines),
                "main_lines": list(it.main_lines),
                "lower_lines": list(it.lower_lines),
                "out_path": str(out_path),
                "composed_00_thumb": bool(did_compose),
            }
        )
        print(f"[OK] {channel}-{it.video} -> {out_path}")

    if args.run:
        _logs_dir().mkdir(parents=True, exist_ok=True)
        rp = _report_path(stamp)
        tmp = rp.with_suffix(rp.suffix + ".tmp")
        tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(rp)
        print(f"[REPORT] {rp}")

    print(f"[DONE] items={ok} composed={composed} run={bool(args.run)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
