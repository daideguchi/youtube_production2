#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ch32_apply_text_to_images.py — apply CH32 thumbnail text to arbitrary base images (preview helper).

This is a convenience tool for quickly testing operator-provided background images
with the same typography policy used for CH24/CH32.

Inputs (SoT):
- workspaces/planning/channels/CH32.csv (column: サムネタイトル)

Outputs:
- By default, writes next to each input as: <name>__with_text.png
"""

from __future__ import annotations

import argparse
import csv
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from _bootstrap import bootstrap

bootstrap(load_env=True)

from factory_common import paths as fpaths  # noqa: E402

from PIL import Image, ImageDraw, ImageFilter, ImageFont  # noqa: E402

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


def _font_path_candidates() -> list[str]:
    # Prefer CH24 policy: Noto Sans JP Black (variable font) if available in workspace,
    # otherwise fall back to heavy Japanese system fonts available on macOS by default.
    return [
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


def _shadow_params_for_size(size: int) -> tuple[int, int, int]:
    dx = max(4, int(round(float(size) * 0.04)))
    dy = dx
    blur = max(2, int(round(float(size) * 0.02)))
    return (dx, dy, blur)


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
) -> None:
    s = str(text or "").strip()
    if not s:
        return
    dx, dy, blur = _shadow_params_for_size(int(font.size))
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
        shadow_alpha=235,
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
) -> None:
    s = str(text or "").strip()
    if not s:
        return

    pre, post = _split_by_particle(s)
    if not post:
        _draw_line(base=base, x=int(x), y=int(y), text=s, font=font, fill_rgba=RGBA_RED, stroke_width=int(stroke_width))
        return

    dx, dy, blur = _shadow_params_for_size(int(font.size))
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
        shadow_alpha=235,
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
    line_gap_px = max(12, int(round(float(font.size) * 0.08)))
    cur_y = int(y)
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            cur_y += int(font.size * 0.92) + int(line_gap_px)
            continue
        if mixed:
            _draw_mixed_line(base=base, x=int(x), y=int(cur_y), text=line, font=font, stroke_width=int(stroke_width))
        else:
            _draw_line(
                base=base,
                x=int(x),
                y=int(cur_y),
                text=line,
                font=font,
                fill_rgba=fill_rgba,
                stroke_width=int(stroke_width),
            )
        cur_y += int(font.size * 0.92) + int(line_gap_px)


def _block_height(font: ImageFont.FreeTypeFont, lines: Sequence[str]) -> int:
    n = sum(1 for ln in lines if str(ln or "").strip())
    if n <= 0:
        return 0
    line_gap_px = max(12, int(round(float(font.size) * 0.08)))
    line_step = int(font.size * 0.92) + int(line_gap_px)
    return int(n * line_step - line_gap_px)


def _inter_block_gap(a: ImageFont.FreeTypeFont, b: ImageFont.FreeTypeFont) -> int:
    return max(16, int(round(min(float(a.size), float(b.size)) * 0.14)))


def _extract_channel_from_name(name: str) -> Optional[str]:
    # Accept patterns like "ch32_1", "CH32-004", etc.
    m = re.search(r"ch(?P<d>\d{1,3})", str(name or "").lower())
    if not m:
        return None
    try:
        n = int(m.group("d"))
    except Exception:
        return None
    return f"CH{n:02d}"


def _extract_video_from_name(name: str) -> Optional[str]:
    # Accept patterns like *_4.png, *-004.png, *_04, etc. (prefer trailing number).
    m = re.search(r"(?:_|-)(?P<n>\d{1,3})$", str(name or ""))
    if not m:
        return None
    try:
        n = int(m.group("n"))
    except Exception:
        return None
    if n <= 0:
        return None
    return f"{n:03d}"


@dataclass(frozen=True)
class PlanRow:
    channel: str
    video: str
    thumb_title: str


def _load_planning_titles(channel: str) -> dict[str, str]:
    path = _planning_csv_path(channel)
    if not path.exists():
        raise FileNotFoundError(f"planning csv not found: {path}")
    out: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if _normalize_channel(row.get("チャンネル") or row.get("channel") or channel) != _normalize_channel(channel):
                continue
            video = _normalize_video(row.get("動画番号") or row.get("video") or "")
            title = str(row.get("サムネタイトル") or "").strip()
            if not title:
                continue
            out[video] = title
    return out


def _cover_resize_to_16x9(img: Image.Image, *, width: int = 1920, height: int = 1080) -> Image.Image:
    if img.size == (int(width), int(height)):
        return img
    w, h = img.size
    if w <= 0 or h <= 0:
        return img.resize((int(width), int(height)), Image.Resampling.NEAREST)
    scale = max(float(width) / float(w), float(height) / float(h))
    nw = max(1, int(round(float(w) * scale)))
    nh = max(1, int(round(float(h) * scale)))
    resized = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = max(0, (nw - int(width)) // 2)
    top = max(0, (nh - int(height)) // 2)
    return resized.crop((left, top, left + int(width), top + int(height)))


def _apply_text_to_image(
    *,
    base_path: Path,
    out_path: Path,
    title: str,
    font_path: str,
    font_variation: Optional[str],
    run: bool,
) -> None:
    with Image.open(base_path) as im:
        base = im.convert("RGBA")

    base = _cover_resize_to_16x9(base, width=1920, height=1080)
    w, h = base.size

    pad = 64
    text_w = int(w * 0.62)
    max_w = max(1, int(text_w - pad * 2))

    upper_lines, main_lines, lower_lines = _parse_title_to_lines(title)
    draw = ImageDraw.Draw(base)
    main_text = " ".join([str(x).strip() for x in main_lines if str(x).strip()])
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

        if upper_lines:
            font_upper, sw_upper = _fit_font_for_lines(
                draw=draw,
                font_path=font_path,
                font_variation=font_variation,
                lines=list(upper_lines),
                max_w=int(max_w),
                start_size=int(round(140 * layout_scale)),
                min_size=64,
                step=2,
                stroke_ratio=0.070,
                stroke_min=8,
                stroke_max=18,
            )

        if main_lines:
            main_boost = 1.0
            if len(main_text) <= 2:
                main_boost = 1.35
            font_main, sw_main = _fit_font_for_lines(
                draw=draw,
                font_path=font_path,
                font_variation=font_variation,
                lines=list(main_lines),
                max_w=int(max_w),
                start_size=int(round(560 * layout_scale * main_boost)),
                min_size=120,
                step=4,
                stroke_ratio=0.105,
                stroke_min=20,
                stroke_max=48,
            )

        if lower_lines:
            font_lower, sw_lower = _fit_font_for_lines(
                draw=draw,
                font_path=font_path,
                font_variation=font_variation,
                lines=list(lower_lines),
                max_w=int(max_w),
                start_size=int(round(520 * layout_scale)),
                min_size=120,
                step=4,
                stroke_ratio=0.105,
                stroke_min=20,
                stroke_max=48,
            )

        blocks: list[tuple[Sequence[str], ImageFont.FreeTypeFont]] = []
        if font_upper and sw_upper:
            blocks.append((list(upper_lines), font_upper))
        if font_main and sw_main:
            blocks.append((list(main_lines), font_main))
        if font_lower and sw_lower:
            blocks.append((list(lower_lines), font_lower))

        total_h = 0
        prev_font: ImageFont.FreeTypeFont | None = None
        for lines, fnt in blocks:
            if prev_font is not None:
                total_h += _inter_block_gap(prev_font, fnt)
            total_h += _block_height(fnt, lines)
            prev_font = fnt

        if total_h <= int(h * 0.92) or attempt >= 3:
            break
        layout_scale *= 0.92

    blocks = []
    if font_upper and sw_upper:
        blocks.append((list(upper_lines), font_upper))
    if font_main and sw_main:
        blocks.append((list(main_lines), font_main))
    if font_lower and sw_lower:
        blocks.append((list(lower_lines), font_lower))

    total_h = 0
    prev_font = None
    for lines, fnt in blocks:
        if prev_font is not None:
            total_h += _inter_block_gap(prev_font, fnt)
        total_h += _block_height(fnt, lines)
        prev_font = fnt
    free_h = float(h - max(1, total_h))
    if len(blocks) == 1:
        factor = 0.30
    elif len(blocks) == 2:
        factor = 0.40
    else:
        factor = 0.20
    start_y = int(max(70, round(free_h * factor)))
    cur_y = start_y

    if upper_lines and font_upper and sw_upper:
        _render_block(
            base=base,
            x=pad,
            y=cur_y,
            max_w=max_w,
            lines=list(upper_lines),
            font_path=font_path,
            font_variation=font_variation,
            start_size=int(round(140 * layout_scale)),
            min_size=64,
            step=2,
            fill_rgba=RGBA_WHITE,
            stroke_ratio=0.070,
            stroke_min=8,
            stroke_max=18,
            font_override=font_upper,
            stroke_width_override=int(sw_upper),
        )
        cur_y += _block_height(font_upper, list(upper_lines))

    if upper_lines and main_lines and font_upper and font_main:
        cur_y += _inter_block_gap(font_upper, font_main)

    if main_lines and font_main and sw_main:
        main_boost = 1.0
        if len(main_text) <= 2:
            main_boost = 1.35
        _render_block(
            base=base,
            x=pad,
            y=cur_y,
            max_w=max_w,
            lines=list(main_lines),
            font_path=font_path,
            font_variation=font_variation,
            start_size=int(round(560 * layout_scale * main_boost)),
            min_size=120,
            step=4,
            fill_rgba=main_fill,
            stroke_ratio=0.105,
            stroke_min=20,
            stroke_max=48,
            font_override=font_main,
            stroke_width_override=int(sw_main),
        )
        cur_y += _block_height(font_main, list(main_lines))

    if main_lines and lower_lines and font_main and font_lower:
        cur_y += _inter_block_gap(font_main, font_lower)

    if lower_lines and font_lower and sw_lower:
        _render_block(
            base=base,
            x=pad,
            y=cur_y,
            max_w=max_w,
            lines=list(lower_lines),
            font_path=font_path,
            font_variation=font_variation,
            start_size=int(round(520 * layout_scale)),
            min_size=120,
            step=4,
            fill_rgba=RGBA_RED,
            stroke_ratio=0.105,
            stroke_min=20,
            stroke_max=48,
            mixed=True,
            font_override=font_lower,
            stroke_width_override=int(sw_lower),
        )

    if not run:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out_path, format="PNG", optimize=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Apply CH32 thumbnail text to provided base images (preview).")
    ap.add_argument("images", nargs="+", help="input PNG/JPG paths")
    ap.add_argument("--channel", default="CH32", help="default channel when not inferrable from filename")
    ap.add_argument("--run", action="store_true", help="actually write outputs")
    ap.add_argument("--out-dir", default="", help="optional output dir (default: next to each input)")
    ap.add_argument(
        "--force-channel",
        action="store_true",
        help="ignore any 'chNN' in filenames and always use --channel",
    )
    ap.add_argument(
        "--force-video",
        default="",
        help="use this video number for ALL inputs (e.g. 004). default: infer from filename",
    )
    args = ap.parse_args(argv)

    default_channel = _normalize_channel(args.channel)
    forced_video = _normalize_video(args.force_video) if str(args.force_video or "").strip() else None

    font_path, font_variation = _pick_font_spec()
    if not font_path:
        raise SystemExit("Japanese-capable font not found on this host")

    titles_cache: dict[str, dict[str, str]] = {}

    stamp = _now_compact_utc()
    print(f"[INFO] started_at={_now_iso_utc()} run={bool(args.run)} stamp={stamp}")
    font_note = f"{font_path}@{font_variation}" if font_variation else font_path
    print(f"[INFO] font={font_note}")

    out_dir = Path(str(args.out_dir)).expanduser() if str(args.out_dir or "").strip() else None
    ok = 0
    for raw in args.images:
        src = Path(raw).expanduser()
        if not src.exists():
            print(f"[SKIP] missing: {src}")
            continue

        inferred_channel = None if args.force_channel else _extract_channel_from_name(src.stem)
        channel = inferred_channel or default_channel

        inferred_video = forced_video or _extract_video_from_name(src.stem)
        if not inferred_video:
            print(f"[SKIP] cannot infer video from filename: {src.name} (use --force-video)")
            continue

        if channel not in titles_cache:
            try:
                titles_cache[channel] = _load_planning_titles(channel)
            except Exception as e:
                if channel != default_channel and default_channel not in titles_cache:
                    titles_cache[default_channel] = _load_planning_titles(default_channel)
                if channel != default_channel:
                    print(f"[WARN] planning missing for {channel}; fallback to {default_channel}: {e}")
                    channel = default_channel
                else:
                    raise

        title_map = titles_cache[channel]
        title = title_map.get(inferred_video)
        if not title:
            print(f"[SKIP] title missing in planning for {channel}-{inferred_video}")
            continue

        if out_dir is None:
            dest = src.with_name(f"{src.stem}__with_text.png")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            dest = out_dir / f"{src.stem}__with_text.png"

        _apply_text_to_image(
            base_path=src,
            out_path=dest,
            title=title,
            font_path=font_path,
            font_variation=font_variation,
            run=bool(args.run),
        )
        ok += 1
        print(f"[OK] {src.name} -> {dest} ({channel}-{inferred_video}: {title})")

    print(f"[DONE] ok={ok} run={bool(args.run)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
