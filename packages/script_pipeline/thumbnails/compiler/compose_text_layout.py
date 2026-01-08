#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import hashlib
import random
import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from factory_common import paths as fpaths

from script_pipeline.thumbnails.compiler.layer_specs import (
    find_text_layout_item_for_video,
    load_layer_spec_yaml,
)


RGBA = Tuple[int, int, int, int]

_INLINE_TAG_RE = re.compile(r"\[(?P<close>/)?(?P<tag>[A-Za-z_]+)\]")

_FC_MATCH_CACHE: Dict[str, Optional[str]] = {}


def _parse_hex_color(value: str) -> RGBA:
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


def _parse_rgba(value: str) -> RGBA:
    v = value.strip().lower().replace(" ", "")
    if not v.startswith("rgba(") or not v.endswith(")"):
        raise ValueError(f"Not an rgba() color: {value}")
    body = v[len("rgba(") : -1]
    parts = body.split(",")
    if len(parts) != 4:
        raise ValueError(f"Not an rgba() color: {value}")
    r = int(parts[0])
    g = int(parts[1])
    b = int(parts[2])
    a_raw = float(parts[3])
    a = int(round(a_raw * 255)) if a_raw <= 1 else int(round(a_raw))
    a = max(0, min(255, a))
    return (r, g, b, a)


def _parse_color(value: str) -> RGBA:
    v = str(value).strip()
    if not v:
        raise ValueError("empty color")
    if v.startswith("#"):
        return _parse_hex_color(v)
    if v.lower().startswith("rgba("):
        return _parse_rgba(v)
    raise ValueError(f"Unsupported color format: {value}")


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_rgba(c1: RGBA, c2: RGBA, t: float) -> RGBA:
    return (
        int(round(_lerp(c1[0], c2[0], t))),
        int(round(_lerp(c1[1], c2[1], t))),
        int(round(_lerp(c1[2], c2[2], t))),
        int(round(_lerp(c1[3], c2[3], t))),
    )


def _build_vertical_gradient(size: Tuple[int, int], stops: Sequence[Sequence[Any]]) -> Image.Image:
    w, h = size
    if w <= 0 or h <= 0:
        raise ValueError(f"invalid gradient size: {size}")
    parsed: List[Tuple[RGBA, float]] = []
    for stop in stops:
        if not isinstance(stop, (list, tuple)) or len(stop) != 2:
            continue
        color, pos = stop
        if not isinstance(pos, (int, float)):
            continue
        parsed.append((_parse_color(str(color)), float(pos)))
    if not parsed:
        return Image.new("RGBA", size, (255, 255, 255, 255))
    parsed.sort(key=lambda it: it[1])

    img = Image.new("RGBA", size)
    px = img.load()
    if px is None:
        return img

    for y in range(h):
        t = y / max(1, (h - 1))
        lo = parsed[0]
        hi = parsed[-1]
        for i in range(len(parsed) - 1):
            if parsed[i][1] <= t <= parsed[i + 1][1]:
                lo = parsed[i]
                hi = parsed[i + 1]
                break
        if hi[1] == lo[1]:
            col = lo[0]
        else:
            local_t = (t - lo[1]) / (hi[1] - lo[1])
            col = _lerp_rgba(lo[0], hi[0], local_t)
        for x in range(w):
            px[x, y] = col
    return img


def _tokenize_for_wrap(text: str) -> List[str]:
    tokens: List[str] = []
    buf = ""
    buf_kind: Optional[str] = None  # "ascii" | "space" | None
    for ch in text:
        if ch == "\n":
            if buf:
                tokens.append(buf)
                buf = ""
                buf_kind = None
            tokens.append("\n")
            continue
        if ch.isspace():
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(" ")
            buf_kind = None
            continue
        is_ascii = ord(ch) < 128 and (ch.isalnum() or ch in {"-", "_", ".", ":"})
        kind = "ascii" if is_ascii else "char"
        if kind == "char":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
            buf_kind = None
            continue
        # ascii token
        if buf_kind == "ascii":
            buf += ch
        else:
            if buf:
                tokens.append(buf)
            buf = ch
            buf_kind = "ascii"
    if buf:
        tokens.append(buf)
    return tokens


def _iter_text_glyph_offsets(font: ImageFont.FreeTypeFont, text: str, tracking: int) -> Iterable[Tuple[str, int]]:
    """
    Yield (char, x_offset_px) for `text` when applying per-glyph tracking (letter spacing).

    Notes:
    - This is used to emulate "tracking" for Pillow which doesn't support letter spacing.
    - We intentionally treat the string as a sequence of glyphs (Python chars) to keep the
      implementation dependency-free and deterministic.
    """
    t = int(tracking or 0)
    x = 0
    for i, ch in enumerate(text):
        yield ch, x
        if i == len(text) - 1:
            break
        adv = _font_advance_px(font, ch)
        x += int(adv + t)


def _bbox_text_with_tracking(
    font: ImageFont.FreeTypeFont,
    text: str,
    *,
    stroke_width: int,
    tracking: int,
) -> Tuple[int, int, int, int]:
    """
    Compute a tight bbox for `text` with per-glyph tracking.

    Returned bbox is in the same coordinate space as `font.getbbox(..., anchor="la")`,
    i.e. relative to the anchor point (0,0) for the rendered text.
    """
    sw = int(stroke_width or 0)
    tr = int(tracking or 0)
    if not text or tr == 0:
        return font.getbbox(text or "", stroke_width=sw, anchor="la")

    x0: Optional[int] = None
    y0: Optional[int] = None
    x1: Optional[int] = None
    y1: Optional[int] = None

    for ch, x_off in _iter_text_glyph_offsets(font, text, tr):
        bbox = font.getbbox(ch, stroke_width=sw, anchor="la")
        gx0 = int(x_off + bbox[0])
        gy0 = int(bbox[1])
        gx1 = int(x_off + bbox[2])
        gy1 = int(bbox[3])
        if x0 is None:
            x0, y0, x1, y1 = gx0, gy0, gx1, gy1
        else:
            x0 = min(x0, gx0)
            y0 = min(y0 or 0, gy0)
            x1 = max(x1 or 0, gx1)
            y1 = max(y1 or 0, gy1)

    if x0 is None or y0 is None or x1 is None or y1 is None:
        return (0, 0, 0, 0)
    return (int(x0), int(y0), int(x1), int(y1))


def _measure_line(
    font: ImageFont.FreeTypeFont,
    text: str,
    *,
    stroke_width: int,
    tracking: int,
) -> Tuple[int, int]:
    if not text:
        return (0, 0)
    bbox = _bbox_text_with_tracking(font, text, stroke_width=int(stroke_width or 0), tracking=int(tracking or 0))
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    return (max(0, int(w)), max(0, int(h)))


def _wrap_tokens_to_lines(
    tokens: Sequence[str],
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int,
    stroke_width: int,
    tracking: int,
) -> Tuple[List[str], bool]:
    lines: List[str] = []
    cur = ""
    overflow = False

    def push_line(s: str) -> None:
        nonlocal lines
        if s is None:
            return
        line = s.strip()
        if line:
            lines.append(line)

    for tok in tokens:
        if tok == "\n":
            push_line(cur)
            cur = ""
            if len(lines) >= max_lines:
                overflow = True
                break
            continue
        if tok == " " and not cur:
            continue
        candidate = cur + tok
        w, _ = _measure_line(font, candidate, stroke_width=stroke_width, tracking=tracking)
        if w <= max_width:
            cur = candidate
            continue
        if cur:
            push_line(cur)
            cur = tok.strip() if tok != " " else ""
        else:
            # single token too wide -> hard split by characters
            hard = ""
            for ch in tok:
                cand = hard + ch
                w2, _ = _measure_line(font, cand, stroke_width=stroke_width, tracking=tracking)
                if w2 <= max_width or not hard:
                    hard = cand
                    continue
                push_line(hard)
                hard = ch
                if len(lines) >= max_lines:
                    overflow = True
                    break
            cur = hard
        if len(lines) >= max_lines:
            overflow = True
            break

    if not overflow:
        push_line(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        overflow = True
    return lines, overflow


@dataclass(frozen=True)
class FitResult:
    lines: List[str]
    font_size: int
    line_gap: int
    stroke_width: int


def _fit_text_to_box(
    text: str,
    font_path: str,
    base_size: int,
    max_width: int,
    max_height: int,
    max_lines: int,
    stroke_width: int,
    tracking: int = 0,
    min_size: int = 22,
) -> FitResult:
    raw = (text or "").strip()
    if not raw:
        return FitResult(lines=[], font_size=base_size, line_gap=0, stroke_width=stroke_width)

    tokens = _tokenize_for_wrap(raw)
    size = max(min_size, int(base_size))
    tr = int(tracking or 0)
    while size >= min_size:
        font = _load_truetype(font_path, size)
        lines, overflow = _wrap_tokens_to_lines(
            tokens,
            font,
            max_width=max_width,
            max_lines=max_lines,
            stroke_width=stroke_width,
            tracking=tr,
        )
        gap = max(2, int(round(size * 0.03)))
        total_h = 0
        max_w = 0
        for idx, line in enumerate(lines):
            w, h = _measure_line(font, line, stroke_width=stroke_width, tracking=tr)
            max_w = max(max_w, w)
            total_h += h
            if idx > 0:
                total_h += gap
        if (not overflow) and max_w <= max_width and total_h <= max_height:
            return FitResult(lines=lines, font_size=size, line_gap=gap, stroke_width=stroke_width)
        size -= 2

    font = _load_truetype(font_path, min_size)
    lines, _ = _wrap_tokens_to_lines(tokens, font, max_width=max_width, max_lines=max_lines, stroke_width=stroke_width, tracking=tr)
    gap = max(2, int(round(min_size * 0.03)))
    return FitResult(lines=lines, font_size=min_size, line_gap=gap, stroke_width=stroke_width)


def _total_text_height_px(
    *,
    lines: Sequence[str],
    font_path: str,
    font_size: int,
    line_gap: int,
    stroke_width: int,
) -> int:
    if not lines:
        return 0
    font = _load_truetype(font_path, int(font_size))
    sw = int(stroke_width or 0)
    total = 0
    for idx, line in enumerate(lines):
        if not line:
            continue
        bbox = font.getbbox(line, stroke_width=sw, anchor="la")
        bh = max(0, bbox[3] - bbox[1])
        total += bh
        if idx < len(lines) - 1:
            total += int(line_gap)
    return int(total)


@dataclass(frozen=True)
class SlotRenderPlan:
    x0: int
    y0: int
    w: int
    h: int
    align: str
    font_path: str
    fill: Dict[str, Any]
    stroke_enabled: bool
    shadow_enabled: bool
    base_size: int
    max_lines: int


@dataclass(frozen=True)
class ShadowSpec:
    color: RGBA
    offset: Tuple[int, int]
    blur: int


def _decode_text_escapes(text: str) -> str:
    """
    Decode authoring-friendly escape sequences used in YAML (e.g. "\\n" for newline).

    This is intentionally conservative: only the sequences we *expect* in copy are decoded.
    """
    if not isinstance(text, str) or not text:
        return ""
    return text.replace("\\n", "\n")

def _normalize_inline_fill_tag(tag: str) -> Optional[str]:
    t = str(tag or "").strip().lower()
    if not t:
        return None
    if t.endswith("_fill"):
        return t
    mapping = {
        "w": "white_fill",
        "white": "white_fill",
        "y": "yellow_fill",
        "yellow": "yellow_fill",
        "r": "red_fill",
        "red": "red_fill",
        "g": "gold_fill",
        "gold": "gold_fill",
        "p": "purple_fill",
        "purple": "purple_fill",
    }
    return mapping.get(t)


def _parse_inline_fill_tags(text: str, *, default_fill_key: str) -> Tuple[str, List[Tuple[int, int, str]]]:
    """
    Parse inline fill tags like:
      "[y]人生[/y][r]が崩れる[/r]"

    Returns:
      (plain_text, spans)

    Where spans are (start, end, fill_key) indices over plain_text.
    """
    raw = str(text or "")
    if "[" not in raw:
        return raw, []

    parts: List[str] = []
    spans: List[Tuple[int, int, str]] = []
    plain_len = 0

    def _append_chunk(chunk: str, fill_key: str) -> None:
        nonlocal plain_len, spans
        if not chunk:
            return
        parts.append(chunk)
        start = plain_len
        plain_len += len(chunk)
        if spans and spans[-1][2] == fill_key and spans[-1][1] == start:
            spans[-1] = (spans[-1][0], plain_len, fill_key)
        else:
            spans.append((start, plain_len, fill_key))

    fill_stack: List[str] = [str(default_fill_key or "").strip() or "white_fill"]
    cur_fill = fill_stack[-1]
    recognized = False
    last = 0

    for m in _INLINE_TAG_RE.finditer(raw):
        _append_chunk(raw[last : m.start()], cur_fill)
        is_close = bool(m.group("close"))
        tag = m.group("tag") or ""
        if is_close:
            if len(fill_stack) > 1:
                fill_stack.pop()
            cur_fill = fill_stack[-1]
            recognized = True
        else:
            fill_key = _normalize_inline_fill_tag(tag)
            if fill_key:
                fill_stack.append(fill_key)
                cur_fill = fill_key
                recognized = True
            else:
                # Unknown tag -> treat literally.
                _append_chunk(raw[m.start() : m.end()], cur_fill)
        last = m.end()

    _append_chunk(raw[last:], cur_fill)
    plain = "".join(parts)
    if not recognized:
        return raw, []
    if not spans:
        return plain, []
    return plain, spans


def _solid_fill_color_from_effects(
    effects: Dict[str, Any],
    *,
    fill_key: str,
    fallback: RGBA,
) -> RGBA:
    v = effects.get(fill_key)
    if not isinstance(v, dict):
        return fallback
    if str(v.get("mode") or "").strip().lower() != "solid":
        return fallback
    try:
        return _parse_color(str(v.get("color") or "#ffffff"))
    except Exception:
        return fallback


def _font_advance_px(font: ImageFont.FreeTypeFont, text: str, *, tracking: int = 0) -> int:
    if not text:
        return 0
    if len(text) >= 2 and int(tracking or 0) != 0:
        tr = int(tracking or 0)
        total = 0
        for i, ch in enumerate(text):
            total += _font_advance_px(font, ch)
            if i < len(text) - 1:
                total += tr
        return int(total)
    try:
        if hasattr(font, "getlength"):
            return int(round(float(font.getlength(text))))
    except Exception:
        pass
    bbox = font.getbbox(text, stroke_width=0, anchor="la")
    return max(0, int(bbox[2] - bbox[0]))


def _resolve_font_file_path(path: str) -> str:
    raw = str(path or "").strip()
    if not raw:
        return raw
    p = Path(raw).expanduser()
    if p.is_absolute():
        return str(p)
    # Allow repo-relative font refs in layer specs (tracked under workspaces/thumbnails/assets/_fonts).
    candidate = (fpaths.repo_root() / p).resolve()
    if candidate.exists():
        return str(candidate)
    return str(p)


def _resolve_repo_file_path(path: str) -> Optional[str]:
    raw = str(path or "").strip()
    if not raw:
        return None
    p = Path(raw).expanduser()
    if p.is_absolute():
        return str(p) if p.exists() else None
    candidate = (fpaths.repo_root() / p).resolve()
    if candidate.exists():
        return str(candidate)
    return None


@lru_cache(maxsize=64)
def _load_rgba_image_cached(path: str) -> Image.Image:
    with Image.open(path) as img:
        return img.convert("RGBA")


def _fit_rgba_image_to_box(img: Image.Image, *, size: Tuple[int, int], fit: str) -> Image.Image:
    sw, sh = int(size[0]), int(size[1])
    sw = max(1, sw)
    sh = max(1, sh)
    mode = str(fit or "cover").strip().lower()
    if mode not in {"stretch", "cover", "contain"}:
        mode = "cover"
    if mode == "stretch":
        return img.resize((sw, sh), resample=Image.LANCZOS)
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
    if mode == "contain":
        scale = min(sw / float(iw), sh / float(ih))
    else:
        scale = max(sw / float(iw), sh / float(ih))
    nw = max(1, int(round(iw * scale)))
    nh = max(1, int(round(ih * scale)))
    resized = img.resize((nw, nh), resample=Image.LANCZOS)
    if mode == "contain":
        canvas = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        dx = int((sw - nw) // 2)
        dy = int((sh - nh) // 2)
        canvas.paste(resized, (dx, dy), resized)
        return canvas
    # cover
    left = max(0, int((nw - sw) // 2))
    top = max(0, int((nh - sh) // 2))
    return resized.crop((left, top, left + sw, top + sh))


def _apply_backdrop_image_overlay(
    base: Image.Image,
    *,
    box_px: Tuple[int, int, int, int],
    image_path: str,
    fit: str,
    colorize: bool,
    color: RGBA,
    alpha: float,
) -> Image.Image:
    img = base.convert("RGBA")
    w, h = img.size
    x0, y0, x1, y1 = [int(v) for v in box_px]
    x0 = max(0, min(w - 1, x0))
    y0 = max(0, min(h - 1, y0))
    x1 = max(x0 + 1, min(w, x1))
    y1 = max(y0 + 1, min(h, y1))
    sw = max(1, x1 - x0)
    sh = max(1, y1 - y0)

    resolved = _resolve_repo_file_path(image_path)
    if not resolved:
        return img
    src = _load_rgba_image_cached(resolved).copy()
    overlay = _fit_rgba_image_to_box(src, size=(sw, sh), fit=fit).convert("RGBA")

    if colorize:
        # Use the asset as an alpha-mask only, and apply a solid color.
        alpha_ch = overlay.getchannel("A")
        solid = Image.new("RGBA", (sw, sh), (color[0], color[1], color[2], 255))
        solid.putalpha(alpha_ch)
        overlay = solid

    a = max(0.0, min(1.0, float(alpha)))
    if a < 0.999:
        alpha_ch = overlay.getchannel("A").point(lambda p: int(round(p * a)))
        overlay.putalpha(alpha_ch)

    full = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    full.alpha_composite(overlay, dest=(x0, y0))
    return Image.alpha_composite(img, full)


def _apply_backdrop_image_overlay_multi(
    base: Image.Image,
    *,
    boxes_px: Sequence[Tuple[int, int, int, int]],
    image_path: str,
    fit: str,
    colorize: bool,
    color: RGBA,
    alpha: float,
) -> Image.Image:
    if not boxes_px:
        return base.convert("RGBA")
    if not colorize:
        out = base.convert("RGBA")
        for box_px in boxes_px:
            out = _apply_backdrop_image_overlay(
                out,
                box_px=box_px,
                image_path=image_path,
                fit=fit,
                colorize=colorize,
                color=color,
                alpha=alpha,
            )
        return out

    img = base.convert("RGBA")
    w, h = img.size

    resolved = _resolve_repo_file_path(image_path)
    if not resolved:
        return img
    src = _load_rgba_image_cached(resolved).copy()

    a = max(0.0, min(1.0, float(alpha)))
    mask_full = Image.new("L", (w, h), 0)
    for raw_box in boxes_px:
        x0, y0, x1, y1 = [int(v) for v in raw_box]
        x0 = max(0, min(w - 1, x0))
        y0 = max(0, min(h - 1, y0))
        x1 = max(x0 + 1, min(w, x1))
        y1 = max(y0 + 1, min(h, y1))
        sw = max(1, x1 - x0)
        sh = max(1, y1 - y0)

        overlay = _fit_rgba_image_to_box(src, size=(sw, sh), fit=fit).convert("RGBA")
        alpha_ch = overlay.getchannel("A")
        if a < 0.999:
            alpha_ch = alpha_ch.point(lambda p: int(round(p * a)))

        tmp = Image.new("L", (w, h), 0)
        tmp.paste(alpha_ch, (x0, y0))
        mask_full = ImageChops.lighter(mask_full, tmp)

    solid = Image.new("RGBA", (w, h), (color[0], color[1], color[2], 255))
    solid.putalpha(mask_full)
    return Image.alpha_composite(img, solid)


def _split_font_ref(font_ref: str) -> Tuple[str, int, Optional[str]]:
    """
    Support TTC face selection + variable font selection using a compact encoding:

    - TTC index: "path#index"
    - Variable name: "path@Black"
    - Combined: "path#index@Black"

    Pillow's FreeType loader supports an explicit face `index`, but our layer specs
    historically only carried a font *path*. Returning "path#index" from discovery
    keeps the surface area small and backward compatible.
    """
    s = str(font_ref or "").strip()
    if not s:
        return ("", 0, None)

    variation: Optional[str] = None
    if "@" in s:
        base, raw_var = s.rsplit("@", 1)
        base = base.strip()
        raw_var = raw_var.strip()
        if base and raw_var:
            s = base
            variation = raw_var

    if "#" not in s:
        return (s, 0, variation)
    path, idx = s.rsplit("#", 1)
    if idx.isdigit():
        return (path, int(idx), variation)
    return (s, 0, variation)


def _try_resolve_font_ref_as_path(font_ref: str) -> Optional[str]:
    path, index, variation = _split_font_ref(font_ref)
    if not path:
        return None
    resolved_path = _resolve_font_file_path(path)
    p = Path(resolved_path)
    if not p.exists():
        return None
    out = resolved_path
    if index:
        out = f"{out}#{index}"
    if variation:
        out = f"{out}@{variation}"
    return out


def _apply_font_variation(font: ImageFont.FreeTypeFont, variation: Optional[str]) -> None:
    if not variation:
        return
    v = str(variation).strip()
    if not v:
        return
    if not hasattr(font, "set_variation_by_name"):
        return
    try:
        # Variable fonts like NotoSansJP_wght.ttf expose names (Thin..Black).
        font.set_variation_by_name(v)
        return
    except Exception:
        pass


@lru_cache(maxsize=512)
def _load_truetype(font_ref: str, size: int) -> ImageFont.FreeTypeFont:
    path, index, variation = _split_font_ref(font_ref)
    resolved = _resolve_font_file_path(path)
    font = ImageFont.truetype(resolved, int(size), index=int(index))
    _apply_font_variation(font, variation)
    return font


def _fc_list_lines() -> List[str]:
    try:
        proc = subprocess.run(
            ["fc-list"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        return []
    return proc.stdout.splitlines()


def _parse_fc_match_font_ref(stdout: str) -> Optional[str]:
    file_path: Optional[str] = None
    index = 0
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if line.startswith("file:"):
            # Example: file: "/System/Library/Fonts/ヒラギノ明朝 ProN.ttc"(s)
            if '"' in line:
                try:
                    file_path = line.split('"', 2)[1]
                except Exception:
                    file_path = None
            else:
                val = line.split(":", 1)[1].strip()
                file_path = val.split("(", 1)[0].strip()
            continue
        if line.startswith("index:"):
            # Example: index: 2(i)(w)
            m = re.search(r"index:\s*(\d+)", line)
            if m:
                try:
                    index = int(m.group(1))
                except Exception:
                    index = 0
            continue

    if not file_path:
        return None
    p = Path(file_path)
    if not p.exists():
        return None
    return f"{p}#{index}" if index else str(p)


def _fc_match_font_ref(family: str) -> Optional[str]:
    key = str(family or "").strip()
    if not key:
        return None
    if key in _FC_MATCH_CACHE:
        return _FC_MATCH_CACHE[key]
    try:
        proc = subprocess.run(
            ["fc-match", "-v", key],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        _FC_MATCH_CACHE[key] = None
        return None

    out = proc.stdout or ""
    # Avoid accepting unrelated fallback fonts: require the queried family to appear.
    # NOTE: fc-match patterns may include modifiers like ":style=Bold", which won't
    # necessarily appear verbatim in the verbose output. In that case, validate only
    # the base family portion before ":".
    family_key = key.split(":", 1)[0].strip() if ":" in key else key
    if family_key.lower() not in out.lower():
        _FC_MATCH_CACHE[key] = None
        return None

    ref = _parse_fc_match_font_ref(out)
    _FC_MATCH_CACHE[key] = ref
    return ref


def _discover_font_ref_by_family(prefer: Sequence[str]) -> Optional[str]:
    prefer_clean = [str(p).strip() for p in prefer if isinstance(p, str) and str(p).strip()]
    if not prefer_clean:
        return None

    for fam in prefer_clean:
        direct = _try_resolve_font_ref_as_path(fam)
        if direct:
            return direct
        ref = _fc_match_font_ref(fam)
        if ref:
            return ref

    # Fallback: older fc-list based discovery (no TTC face index support).
    lines = _fc_list_lines()
    for needle in [p.lower() for p in prefer_clean]:
        for line in lines:
            if ":" not in line:
                continue
            font_path = line.split(":", 1)[0].strip()
            hay = line.lower()
            if needle in hay:
                p = Path(font_path)
                if p.exists():
                    return str(p)
    return None


def _fallback_font_path() -> str:
    # Reuse the same fallback convention as the existing 3-line compiler.
    from script_pipeline.thumbnails.compiler import compile_buddha_3line as legacy

    return legacy.resolve_font_path(None)


def _resolve_font_path_from_spec(fonts: Dict[str, Any], font_key: str) -> str:
    families = fonts.get(font_key)
    if isinstance(families, str) and families.strip():
        direct = _try_resolve_font_ref_as_path(families)
        if direct:
            return direct
        found = _discover_font_ref_by_family([families])
        if found:
            return found
    if isinstance(families, list):
        found = _discover_font_ref_by_family([str(x) for x in families])
        if found:
            return found
    return _fallback_font_path()


def _render_text_lines(
    base: Image.Image,
    *,
    lines: Sequence[str],
    x: int,
    y: int,
    font_path: str,
    font_size: int,
    line_gap: int,
    align: str,
    tracking: int,
    max_width: int,
    fill: Dict[str, Any],
    inline_spans: Optional[List[List[Tuple[int, int, RGBA]]]] = None,
    stroke_enabled: bool,
    stroke_color: RGBA,
    stroke_width: int,
    glow: Optional[ShadowSpec],
    shadow: Optional[ShadowSpec],
) -> Image.Image:
    img = base.convert("RGBA")
    font = _load_truetype(font_path, font_size)
    tr = int(tracking or 0)

    # Precompute packed positions so that the *visible* top of each line starts at y and lines are tightly stacked.
    packed: List[Tuple[str, int, int, Tuple[int, int, int, int]]] = []
    y_cursor = int(y)
    sw = int(stroke_width if stroke_enabled else 0)
    for line in lines:
        if not line:
            continue
        bbox = _bbox_text_with_tracking(font, line, stroke_width=sw, tracking=tr)
        bw = max(0, bbox[2] - bbox[0])
        bh = max(0, bbox[3] - bbox[1])
        if align == "center":
            left_edge = x + max(0, (max_width - bw) // 2)
        elif align == "right":
            left_edge = x + max(0, max_width - bw)
        else:
            left_edge = x
        x_anchor = int(left_edge - bbox[0])
        y_anchor = int(y_cursor - bbox[1])
        packed.append((line, x_anchor, y_anchor, bbox))
        y_cursor += bh + int(line_gap)

    def _draw_line_solid(
        draw: ImageDraw.ImageDraw,
        *,
        line: str,
        x_anchor: int,
        y_anchor: int,
        color_default: RGBA,
        spans: Optional[List[Tuple[int, int, RGBA]]],
        stroke_w: int = 0,
        stroke_fill: Optional[RGBA] = None,
    ) -> None:
        if not line:
            return
        if tr == 0 and not spans:
            draw.text(
                (x_anchor, y_anchor),
                line,
                font=font,
                fill=color_default,
                stroke_width=int(stroke_w),
                stroke_fill=stroke_fill,
                anchor="la",
            )
            return
        colors: Optional[List[RGBA]] = None
        if spans:
            colors = [color_default] * len(line)
            for start, end, c in spans:
                s = max(0, int(start))
                e = min(len(line), int(end))
                if s >= e:
                    continue
                for i in range(s, e):
                    colors[i] = c
        for idx, (ch, x_off) in enumerate(_iter_text_glyph_offsets(font, line, tr)):
            fill_col = colors[idx] if colors is not None and idx < len(colors) else color_default
            draw.text(
                (int(x_anchor + x_off), int(y_anchor)),
                ch,
                font=font,
                fill=fill_col,
                stroke_width=int(stroke_w),
                stroke_fill=stroke_fill,
                anchor="la",
            )

    # Glow: draw once on a separate layer, then blur (offset can be 0,0).
    if glow and (glow.offset != (0, 0) or glow.blur > 0):
        glow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_layer)
        for line, x_pos, y_pos, _bbox in packed:
            _draw_line_solid(
                gd,
                line=line,
                x_anchor=int(x_pos + glow.offset[0]),
                y_anchor=int(y_pos + glow.offset[1]),
                color_default=glow.color,
                spans=None,
            )
        if glow.blur > 0:
            glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=glow.blur))
        img = Image.alpha_composite(img, glow_layer)

    # Shadow: draw once on a separate layer, then blur.
    if shadow and (shadow.offset != (0, 0) or shadow.blur > 0):
        shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow_layer)
        for line, x_pos, y_pos, _bbox in packed:
            _draw_line_solid(
                sd,
                line=line,
                x_anchor=int(x_pos + shadow.offset[0]),
                y_anchor=int(y_pos + shadow.offset[1]),
                color_default=shadow.color,
                spans=None,
            )
        if shadow.blur > 0:
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow.blur))
        img = Image.alpha_composite(img, shadow_layer)

    # Stroke layer (optional) — keeps gradient fill intact.
    if stroke_enabled and stroke_width > 0:
        stroke_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(stroke_layer)
        for line, x_pos, y_pos, _bbox in packed:
            _draw_line_solid(
                sd,
                line=line,
                x_anchor=int(x_pos),
                y_anchor=int(y_pos),
                color_default=(0, 0, 0, 0),
                spans=None,
                stroke_w=int(stroke_width),
                stroke_fill=stroke_color,
            )
        img = Image.alpha_composite(img, stroke_layer)

    # Fill
    mode = str(fill.get("mode") or "solid").strip().lower()
    if mode == "solid":
        color = _parse_color(str(fill.get("color") or "#ffffff"))
        draw = ImageDraw.Draw(img)
        for i, (line, x_pos, y_pos, _bbox) in enumerate(packed):
            spans = inline_spans[i] if inline_spans and i < len(inline_spans) else None
            _draw_line_solid(draw, line=line, x_anchor=int(x_pos), y_anchor=int(y_pos), color_default=color, spans=spans)
        return img

    if mode == "linear_gradient":
        stops = fill.get("stops") or []
        for line, x_anchor, y_anchor, _bbox in packed:
            bbox0 = _bbox_text_with_tracking(font, line, stroke_width=0, tracking=tr)
            w = max(1, bbox0[2] - bbox0[0])
            h = max(1, bbox0[3] - bbox0[1])
            grad = _build_vertical_gradient((w, h), stops=stops)

            mask = Image.new("L", (w, h), 0)
            md = ImageDraw.Draw(mask)
            # Draw into the tight bbox so (0,0) aligns with bbox0's top-left.
            # NOTE: tracking is not supported for gradient fills; keep mask consistent with bbox.
            md.text((-bbox0[0], -bbox0[1]), line, font=font, fill=255, anchor="la")

            fill_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            fill_layer = Image.composite(grad, fill_layer, mask)

            dest_x = int(x_anchor + bbox0[0])
            dest_y = int(y_anchor + bbox0[1])
            img.alpha_composite(fill_layer, dest=(dest_x, dest_y))
        return img

    raise ValueError(f"unsupported fill mode: {mode}")


def _normalize_video_id(channel: str, video: str) -> str:
    ch = str(channel).upper().strip()
    v = str(video).strip()
    if v.isdigit():
        v = f"{int(v):03d}"
    return f"{ch}-{v}"


def _apply_horizontal_overlay(
    base: Image.Image,
    *,
    x0: float,
    x1: float,
    color: RGBA,
    alpha_left: float,
    alpha_right: float,
) -> Image.Image:
    """
    Apply a horizontal alpha gradient overlay between x0..x1 (normalized 0..1).
    """
    img = base.convert("RGBA")
    w, h = img.size
    x0 = max(0.0, min(1.0, float(x0)))
    x1 = max(0.0, min(1.0, float(x1)))
    if x1 <= x0 + 1e-6:
        return img
    a0 = max(0.0, min(1.0, float(alpha_left)))
    a1 = max(0.0, min(1.0, float(alpha_right)))

    start_px = int(round(x0 * w))
    end_px = int(round(x1 * w))
    start_px = max(0, min(w, start_px))
    end_px = max(0, min(w, end_px))
    if end_px <= start_px:
        return img

    values: List[int] = [0] * w
    span = max(1, end_px - start_px)
    for x in range(start_px, end_px):
        t = (x - start_px) / span
        alpha = (a0 * (1.0 - t)) + (a1 * t)
        values[x] = int(round(alpha * 255))

    mask_row = Image.new("L", (w, 1), 0)
    mask_row.putdata(values)
    mask = mask_row.resize((w, h))

    overlay = Image.new("RGBA", (w, h), (color[0], color[1], color[2], 255))
    overlay.putalpha(mask)
    return Image.alpha_composite(img, overlay)

def _apply_vertical_overlay(
    base: Image.Image,
    *,
    y0: float,
    y1: float,
    color: RGBA,
    alpha_top: float,
    alpha_bottom: float,
) -> Image.Image:
    """
    Apply a vertical alpha gradient overlay between y0..y1 (normalized 0..1).
    """
    img = base.convert("RGBA")
    w, h = img.size
    y0 = max(0.0, min(1.0, float(y0)))
    y1 = max(0.0, min(1.0, float(y1)))
    if y1 <= y0 + 1e-6:
        return img
    a0 = max(0.0, min(1.0, float(alpha_top)))
    a1 = max(0.0, min(1.0, float(alpha_bottom)))

    start_px = int(round(y0 * h))
    end_px = int(round(y1 * h))
    start_px = max(0, min(h, start_px))
    end_px = max(0, min(h, end_px))
    if end_px <= start_px:
        return img

    values: List[int] = [0] * h
    span = max(1, end_px - start_px)
    for y in range(start_px, end_px):
        t = (y - start_px) / span
        alpha = (a0 * (1.0 - t)) + (a1 * t)
        values[y] = int(round(alpha * 255))

    mask_col = Image.new("L", (1, h), 0)
    mask_col.putdata(values)
    mask = mask_col.resize((w, h))

    overlay = Image.new("RGBA", (w, h), (color[0], color[1], color[2], 255))
    overlay.putalpha(mask)
    return Image.alpha_composite(img, overlay)

def _stable_seed_u64(key: str) -> int:
    digest = hashlib.sha256(str(key or "").encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _smooth_noise(values: List[float], *, window: int) -> List[float]:
    if not values:
        return []
    w = max(1, int(window))
    if w <= 1:
        return list(values)
    if w % 2 == 0:
        w += 1
    half = w // 2
    n = len(values)
    out: List[float] = [0.0] * n
    for i in range(n):
        s = 0.0
        c = 0
        lo = max(0, i - half)
        hi = min(n - 1, i + half)
        for j in range(lo, hi + 1):
            s += float(values[j])
            c += 1
        out[i] = s / float(c or 1)
    return out


def _build_brush_band_mask(
    size: Tuple[int, int],
    *,
    edge: str,
    max_alpha: int,
    seed: int,
    roughness: float,
    feather_px: int,
    hole_count: int,
    blur_px: int,
) -> Image.Image:
    """
    Build a "brush stroke" alpha mask for a band region.

    edge:
      - "bottom": irregular bottom edge (top band)
      - "top": irregular top edge (bottom band)
    """
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0:
        return Image.new("L", (max(1, w), max(1, h)), 0)

    edge_key = str(edge or "").strip().lower()
    if edge_key not in {"bottom", "top"}:
        edge_key = "bottom"

    a = max(0, min(255, int(max_alpha)))
    rough = max(0.0, min(0.55, float(roughness)))
    feather = max(1, int(feather_px))
    blur = max(0, int(blur_px))

    rng = random.Random(int(seed) & 0xFFFFFFFF_FFFFFFFF)

    # Low-frequency noise curve along X
    step = max(18, int(round(w / 72.0)))
    cps = [rng.uniform(-1.0, 1.0) for _ in range((w // step) + 3)]
    raw_noise: List[float] = [0.0] * w
    for i in range(len(cps) - 1):
        x0 = i * step
        x1 = min(w - 1, (i + 1) * step)
        if x0 >= w:
            break
        v0, v1 = cps[i], cps[i + 1]
        span = max(1, x1 - x0)
        for x in range(x0, x1 + 1):
            t = (x - x0) / float(span)
            raw_noise[x] = (v0 * (1.0 - t)) + (v1 * t)
    noise = _smooth_noise(raw_noise, window=max(5, step // 2))

    # Edge curve (near the far side of the band so the text region stays solid).
    if edge_key == "bottom":
        base_edge = int(round(h * 0.97))
        # Keep most of the band solid to preserve text legibility.
        min_edge = int(round(h * 0.90))
        max_edge = h - 1
    else:
        base_edge = int(round(h * 0.03))
        min_edge = 0
        max_edge = int(round(h * 0.20))

    amp = max(1, int(round(h * rough)))
    curve: List[int] = [0] * w
    for x in range(w):
        ey = base_edge + int(round(noise[x] * amp))
        curve[x] = max(min_edge, min(max_edge, ey))

    mask = Image.new("L", (w, h), 0)
    pix = mask.load()

    for x in range(w):
        ey = int(curve[x])
        if edge_key == "bottom":
            for y in range(0, max(0, min(h, ey))):
                pix[x, y] = a
            for y in range(max(0, ey), max(0, min(h, ey + feather))):
                t = (y - ey) / float(feather)
                pix[x, y] = int(round(a * (1.0 - t)))
        else:
            for y in range(max(0, ey), h):
                pix[x, y] = a
            for y in range(max(0, ey - feather), max(0, min(h, ey))):
                t = (ey - y) / float(feather)
                pix[x, y] = int(round(a * (1.0 - t)))

    # Punch a few "bristle holes" near the rough edge.
    holes = max(0, int(hole_count))
    if holes:
        draw = ImageDraw.Draw(mask)
        for _ in range(holes):
            # Small "paint thinning" specks near the rough edge (avoid big blobs/slits).
            rw = rng.randint(max(6, w // 320), max(18, w // 90))
            rh = rng.randint(max(4, h // 80), max(14, h // 26))
            rw = max(6, min(w - 1, rw))
            rh = max(4, min(h - 1, rh))
            x0 = rng.randint(0, max(0, w - rw))
            if edge_key == "bottom":
                y0 = rng.randint(max(0, h - max(8, int(round(feather * 2.2)))), max(0, h - rh))
            else:
                y0 = rng.randint(0, min(max(0, h - rh), max(0, int(round(feather * 1.6)))))
            fill = int(round(a * rng.uniform(0.55, 0.90)))
            draw.ellipse([x0, y0, x0 + rw, y0 + rh], fill=max(0, min(a, fill)))

    # Add dry-brush texture near the rough edge (avoid "flat rectangle" look).
    if a > 0 and rough > 0.0:
        edge_band = int(round(max(18.0, float(feather) * 1.6, float(h) * 0.10)))
        edge_band = max(8, min(h, edge_band))
        # Higher-res noise to avoid large "bubble" artifacts.
        nw = max(96, w // 24)
        nh = max(24, edge_band // 3)
        noise_small = Image.new("L", (nw, nh), 0)
        noise_small.putdata([rng.randint(0, 255) for _ in range(nw * nh)])
        noise = noise_small.resize((w, edge_band), resample=Image.BILINEAR)
        noise_px = noise.load()

        if edge_key == "bottom":
            y_start = h - edge_band
            for y in range(y_start, h):
                wy = (y - y_start) / float(max(1, edge_band - 1))  # 0..1 (strong near bottom)
                for x in range(w):
                    cur = int(pix[x, y] or 0)
                    if cur <= 0:
                        continue
                    n = float(noise_px[x, y - y_start]) / 255.0
                    f = 0.60 + (0.40 * n)
                    pix[x, y] = int(round(cur * ((1.0 - wy) + (wy * f))))
        else:
            y_end = edge_band
            for y in range(0, y_end):
                wy = (y_end - 1 - y) / float(max(1, y_end - 1))  # 0..1 (strong near top)
                for x in range(w):
                    cur = int(pix[x, y] or 0)
                    if cur <= 0:
                        continue
                    n = float(noise_px[x, y]) / 255.0
                    f = 0.60 + (0.40 * n)
                    pix[x, y] = int(round(cur * ((1.0 - wy) + (wy * f))))

    if blur:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=float(blur)))
    return mask


def _build_brush_stroke_mask(
    size: Tuple[int, int],
    *,
    max_alpha: int,
    seed: int,
    roughness: float,
    feather_px: int,
    hole_count: int,
    blur_px: int,
) -> Image.Image:
    """
    Build an organic "brush stroke" alpha mask meant to sit behind text (not a full-width band).

    This is intentionally stylized: irregular edges + slight splatter so it reads as "ink/brush".
    """
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0:
        return Image.new("L", (max(1, w), max(1, h)), 0)

    a = max(0, min(255, int(max_alpha)))
    rough = max(0.0, min(0.85, float(roughness)))
    feather = max(0, int(feather_px))
    blur = max(0, int(blur_px))

    rng = random.Random(int(seed) & 0xFFFFFFFF_FFFFFFFF)

    # Build a brush-like "ink band" by combining top+bottom rough edges and tapering the ends.
    holes = max(0, int(hole_count))
    edge_holes = max(0, int(round(holes * 0.55)))
    inner_holes = max(0, holes - edge_holes)
    edge_blur = max(0, blur - 1)
    edge_rough = max(0.0, min(0.55, (rough * 0.92) + 0.04))

    top = _build_brush_band_mask(
        (w, h),
        edge="top",
        max_alpha=a,
        seed=int(seed) ^ 0xBADC0FFE,
        roughness=edge_rough,
        feather_px=feather,
        hole_count=edge_holes,
        blur_px=edge_blur,
    )
    bottom = _build_brush_band_mask(
        (w, h),
        edge="bottom",
        max_alpha=a,
        seed=(int(seed) + 1337) ^ 0xC0FFEE,
        roughness=edge_rough,
        feather_px=feather,
        hole_count=edge_holes,
        blur_px=edge_blur,
    )
    mask = ImageChops.darker(top, bottom)

    # End taper: fade alpha near left/right ends so it reads as a brush stroke, not a rectangle.
    end_span = max(10, int(round(w * (0.14 + (0.10 * rough)))))
    taper_vals: List[int] = [0] * w
    for x in range(w):
        dist = min(x, (w - 1) - x)
        t = min(1.0, float(dist) / float(end_span))
        # Ease-in to keep center solid and ends thin.
        t = t ** 0.70
        factor = 0.10 + (0.90 * t)
        taper_vals[x] = int(round(255 * factor))
    taper_mask = Image.new("L", (w, 1), 0)
    taper_mask.putdata(taper_vals)
    taper_mask = taper_mask.resize((w, h), resample=Image.BILINEAR)
    mask = ImageChops.multiply(mask, taper_mask)

    draw = ImageDraw.Draw(mask)

    # Interior thinning (subtle): a few low-alpha "dry" patches inside the band.
    if inner_holes:
        for _ in range(inner_holes):
            rw = rng.randint(max(10, w // 120), max(38, w // 55))
            rh = rng.randint(max(6, h // 22), max(26, h // 10))
            rw = max(6, min(w - 1, rw))
            rh = max(4, min(h - 1, rh))
            x0 = rng.randint(0, max(0, w - rw))
            y0 = rng.randint(0, max(0, h - rh))
            fill = int(round(a * rng.uniform(0.10, 0.55)))
            draw.ellipse([x0, y0, x0 + rw, y0 + rh], fill=max(0, min(a, fill)))

    # Splatter near the top/bottom edges for a more "brush/ink" look.
    splatter = max(0, int(round(holes * (0.70 + (rough * 0.85)))))
    splatter = min(splatter, 70 + max(0, w // 28))
    if splatter:
        for _ in range(splatter):
            r = rng.randint(2, max(3, int(round(min(w, h) * 0.028))))
            x0 = rng.randint(0, max(0, w - 1))
            if rng.random() < 0.5:
                y0 = rng.randint(0, max(0, int(round(h * 0.36))))
            else:
                y0 = rng.randint(min(h - 1, int(round(h * 0.64))), h - 1)
            fill = int(round(a * rng.uniform(0.12, 0.58)))
            draw.ellipse([x0 - r, y0 - r, x0 + r, y0 + r], fill=max(0, min(a, fill)))

    # Final softening for anti-aliasing (keep small so texture survives).
    if blur > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=float(blur)))
    return mask


def _apply_brush_band_overlay(
    base: Image.Image,
    *,
    y0: float,
    y1: float,
    color: RGBA,
    alpha: float,
    edge: str,
    seed: int,
    roughness: float,
    feather_px: int,
    hole_count: int,
    blur_px: int,
) -> Image.Image:
    img = base.convert("RGBA")
    w, h = img.size

    y0 = max(0.0, min(1.0, float(y0)))
    y1 = max(0.0, min(1.0, float(y1)))
    if y1 <= y0 + 1e-6:
        return img

    start_px = int(round(y0 * h))
    end_px = int(round(y1 * h))
    start_px = max(0, min(h, start_px))
    end_px = max(0, min(h, end_px))
    if end_px <= start_px:
        return img

    band_h = max(1, end_px - start_px)
    a = max(0.0, min(1.0, float(alpha)))
    max_alpha = int(round(a * 255))

    mask = _build_brush_band_mask(
        (w, band_h),
        edge=edge,
        max_alpha=max_alpha,
        seed=seed,
        roughness=roughness,
        feather_px=feather_px,
        hole_count=hole_count,
        blur_px=blur_px,
    )
    overlay = Image.new("RGBA", (w, band_h), (color[0], color[1], color[2], 255))
    overlay.putalpha(mask)

    full = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    full.alpha_composite(overlay, dest=(0, start_px))
    return Image.alpha_composite(img, full)


def _apply_brush_stroke_overlay(
    base: Image.Image,
    *,
    box_px: Tuple[int, int, int, int],
    color: RGBA,
    alpha: float,
    seed: int,
    roughness: float,
    feather_px: int,
    hole_count: int,
    blur_px: int,
) -> Image.Image:
    img = base.convert("RGBA")
    w, h = img.size
    x0, y0, x1, y1 = [int(v) for v in box_px]
    x0 = max(0, min(w - 1, x0))
    y0 = max(0, min(h - 1, y0))
    x1 = max(x0 + 1, min(w, x1))
    y1 = max(y0 + 1, min(h, y1))
    sw = max(1, x1 - x0)
    sh = max(1, y1 - y0)

    a = max(0.0, min(1.0, float(alpha)))
    max_alpha = int(round(a * 255))
    if max_alpha <= 0:
        return img

    mask = _build_brush_stroke_mask(
        (sw, sh),
        max_alpha=max_alpha,
        seed=seed,
        roughness=roughness,
        feather_px=feather_px,
        hole_count=hole_count,
        blur_px=blur_px,
    )
    overlay = Image.new("RGBA", (sw, sh), (color[0], color[1], color[2], 255))
    overlay.putalpha(mask)

    full = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    full.alpha_composite(overlay, dest=(x0, y0))
    return Image.alpha_composite(img, full)


def _deep_merge_dict(base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def compose_text_layout(
    base_image_path: Path,
    *,
    text_layout_spec: Dict[str, Any],
    video_id: str,
    text_override: Optional[Dict[str, str]] = None,
    template_id_override: Optional[str] = None,
    effects_override: Optional[Dict[str, Any]] = None,
    overlays_override: Optional[Dict[str, Any]] = None,
) -> Image.Image:
    base = Image.open(base_image_path).convert("RGBA")

    item = find_text_layout_item_for_video(text_layout_spec, video_id)
    if not isinstance(item, dict):
        raise KeyError(f"video_id not found in text_layout spec: {video_id}")

    template_id = str(template_id_override or "").strip() or str(item.get("template_id") or "").strip()
    if not template_id:
        raise ValueError(f"missing template_id for video_id={video_id}")

    global_cfg = text_layout_spec.get("global") if isinstance(text_layout_spec, dict) else None
    if not isinstance(global_cfg, dict):
        raise ValueError("text_layout.global is missing")

    overlays_cfg = global_cfg.get("overlays") if isinstance(global_cfg.get("overlays"), dict) else {}
    if isinstance(overlays_override, dict) and overlays_override:
        overlays_cfg = _deep_merge_dict(overlays_cfg, overlays_override)
    left_overlay = overlays_cfg.get("left_tsz") if isinstance(overlays_cfg.get("left_tsz"), dict) else None
    if isinstance(left_overlay, dict) and bool(left_overlay.get("enabled", True)):
        safe = global_cfg.get("safe_zones") if isinstance(global_cfg.get("safe_zones"), dict) else {}
        left_safe = safe.get("left_TSZ") if isinstance(safe.get("left_TSZ"), dict) else {}
        x0 = float(left_overlay.get("x0", left_safe.get("x0", 0.0)))
        x1 = float(left_overlay.get("x1", left_safe.get("x1", 0.52)))
        alpha_left = float(left_overlay.get("alpha_left", 0.65))
        alpha_right = float(left_overlay.get("alpha_right", 0.0))
        col = _parse_color(str(left_overlay.get("color") or "#000000"))
        base = _apply_horizontal_overlay(
            base,
            x0=x0,
            x1=x1,
            color=col,
            alpha_left=alpha_left,
            alpha_right=alpha_right,
        )

    top_band = overlays_cfg.get("top_band") if isinstance(overlays_cfg.get("top_band"), dict) else None
    if isinstance(top_band, dict) and bool(top_band.get("enabled", True)):
        y0 = float(top_band.get("y0", 0.0))
        y1 = float(top_band.get("y1", 0.25))
        alpha_top = float(top_band.get("alpha_top", 0.70))
        alpha_bottom = float(top_band.get("alpha_bottom", 0.0))
        col = _parse_color(str(top_band.get("color") or "#000000"))
        mode = str(top_band.get("mode") or "").strip().lower()
        if mode in {"brush", "ink"}:
            alpha = float(top_band.get("alpha", max(alpha_top, alpha_bottom)))
            roughness = float(top_band.get("roughness", 0.10))
            feather_px = int(top_band.get("feather_px", 28))
            hole_count = int(top_band.get("hole_count", 26))
            blur_px = int(top_band.get("blur_px", 2))
            seed_raw = top_band.get("seed")
            try:
                seed = int(seed_raw) if seed_raw is not None else int(_stable_seed_u64(f"{video_id}|top_band|{mode}"))
            except Exception:
                seed = int(_stable_seed_u64(f"{video_id}|top_band|{mode}"))
            base = _apply_brush_band_overlay(
                base,
                y0=y0,
                y1=y1,
                color=col,
                alpha=alpha,
                edge="bottom",
                seed=seed,
                roughness=roughness,
                feather_px=feather_px,
                hole_count=hole_count,
                blur_px=blur_px,
            )
        else:
            base = _apply_vertical_overlay(
                base,
                y0=y0,
                y1=y1,
                color=col,
                alpha_top=alpha_top,
                alpha_bottom=alpha_bottom,
            )

    bottom_band = overlays_cfg.get("bottom_band") if isinstance(overlays_cfg.get("bottom_band"), dict) else None
    if isinstance(bottom_band, dict) and bool(bottom_band.get("enabled", True)):
        y0 = float(bottom_band.get("y0", 0.70))
        y1 = float(bottom_band.get("y1", 1.0))
        alpha_top = float(bottom_band.get("alpha_top", 0.0))
        alpha_bottom = float(bottom_band.get("alpha_bottom", 0.80))
        col = _parse_color(str(bottom_band.get("color") or "#000000"))
        mode = str(bottom_band.get("mode") or "").strip().lower()
        if mode in {"brush", "ink"}:
            alpha = float(bottom_band.get("alpha", max(alpha_top, alpha_bottom)))
            roughness = float(bottom_band.get("roughness", 0.08))
            feather_px = int(bottom_band.get("feather_px", 26))
            hole_count = int(bottom_band.get("hole_count", 22))
            blur_px = int(bottom_band.get("blur_px", 2))
            seed_raw = bottom_band.get("seed")
            try:
                seed = int(seed_raw) if seed_raw is not None else int(_stable_seed_u64(f"{video_id}|bottom_band|{mode}"))
            except Exception:
                seed = int(_stable_seed_u64(f"{video_id}|bottom_band|{mode}"))
            base = _apply_brush_band_overlay(
                base,
                y0=y0,
                y1=y1,
                color=col,
                alpha=alpha,
                edge="top",
                seed=seed,
                roughness=roughness,
                feather_px=feather_px,
                hole_count=hole_count,
                blur_px=blur_px,
            )
        else:
            base = _apply_vertical_overlay(
                base,
                y0=y0,
                y1=y1,
                color=col,
                alpha_top=alpha_top,
                alpha_bottom=alpha_bottom,
            )

    fonts_cfg = global_cfg.get("fonts") if isinstance(global_cfg.get("fonts"), dict) else {}
    effects = global_cfg.get("effects_defaults") if isinstance(global_cfg.get("effects_defaults"), dict) else {}
    if isinstance(effects_override, dict) and effects_override:
        effects = _deep_merge_dict(effects, effects_override)
    stroke_cfg = effects.get("stroke") if isinstance(effects.get("stroke"), dict) else {}
    shadow_cfg = effects.get("shadow") if isinstance(effects.get("shadow"), dict) else {}

    stroke_color = _parse_color(str(stroke_cfg.get("color") or "#000000"))
    stroke_width = int(stroke_cfg.get("width_px", 8))

    shadow_alpha = float(shadow_cfg.get("alpha", 0.65))
    shadow_alpha = max(0.0, min(1.0, shadow_alpha))
    shadow_rgba = _parse_color(str(shadow_cfg.get("color") or "#000000"))
    shadow_color: RGBA = (shadow_rgba[0], shadow_rgba[1], shadow_rgba[2], int(round(shadow_alpha * 255)))
    shadow_offset = shadow_cfg.get("offset_px") or [6, 6]
    try:
        off_x = int(shadow_offset[0])
        off_y = int(shadow_offset[1])
    except Exception:
        off_x, off_y = (6, 6)
    blur = int(shadow_cfg.get("blur_px", 10))
    shadow_spec = ShadowSpec(color=shadow_color, offset=(off_x, off_y), blur=blur)
    shadow_rgb_default = (shadow_color[0], shadow_color[1], shadow_color[2])

    glow_cfg = effects.get("glow") if isinstance(effects.get("glow"), dict) else {}
    glow_alpha = float(glow_cfg.get("alpha", 0.0))
    glow_alpha = max(0.0, min(1.0, glow_alpha))
    glow_rgba = _parse_color(str(glow_cfg.get("color") or "#ffffff"))
    glow_color: RGBA = (glow_rgba[0], glow_rgba[1], glow_rgba[2], int(round(glow_alpha * 255)))
    glow_blur = int(glow_cfg.get("blur_px", 0))
    glow_spec = ShadowSpec(color=glow_color, offset=(0, 0), blur=max(0, glow_blur))

    templates = text_layout_spec.get("templates")
    if not isinstance(templates, dict):
        raise ValueError("text_layout.templates is missing")
    tpl = templates.get(template_id)
    if not isinstance(tpl, dict):
        raise KeyError(f"template_id not found: {template_id}")
    slots = tpl.get("slots")
    if not isinstance(slots, dict):
        raise ValueError(f"template slots missing for {template_id}")

    text_payload = item.get("text")
    if not isinstance(text_payload, dict):
        raise ValueError(f"text missing for {video_id}")

    img_w, img_h = base.size
    out = base

    for slot_name, slot_cfg in slots.items():
        if not isinstance(slot_cfg, dict):
            continue
        raw_text = ""
        if text_override and isinstance(text_override.get(slot_name), str):
            raw_text = str(text_override.get(slot_name) or "")
        else:
            raw_text = str(text_payload.get(slot_name) or "")

        box = slot_cfg.get("box")
        if not isinstance(box, list) or len(box) != 4:
            continue
        x0 = int(round(float(box[0]) * img_w))
        y0 = int(round(float(box[1]) * img_h))
        w = int(round(float(box[2]) * img_w))
        h = int(round(float(box[3]) * img_h))

        font_key = str(slot_cfg.get("font") or "").strip()
        font_path = _resolve_font_path_from_spec(fonts_cfg, font_key) if font_key else _fallback_font_path()

        fill_key = str(slot_cfg.get("fill") or "").strip()
        fill = effects.get(fill_key) if isinstance(effects.get(fill_key), dict) else None
        if not isinstance(fill, dict):
            fill = {"mode": "solid", "color": "#ffffff"}

        raw_text = _decode_text_escapes(raw_text).strip()
        if not raw_text:
            continue
        plain_text, spans = _parse_inline_fill_tags(raw_text, default_fill_key=fill_key)
        plain_text = plain_text.strip()
        if not plain_text:
            continue

        base_size = int(slot_cfg.get("base_size_px", 64))
        tracking = int(slot_cfg.get("tracking", 0))
        max_lines = int(slot_cfg.get("max_lines", 2))
        align = str(slot_cfg.get("align") or "left").strip().lower()
        if align not in {"left", "center", "right"}:
            align = "left"

        valign = str(slot_cfg.get("valign") or "top").strip().lower()
        if valign in {"center", "middle"}:
            valign = "middle"
        if valign not in {"top", "middle", "bottom"}:
            valign = "top"

        stroke_enabled = bool(slot_cfg.get("stroke", True))

        shadow_cfg_override: Optional[Dict[str, Any]] = None
        raw_shadow_override = slot_cfg.get("shadow_override")
        if isinstance(raw_shadow_override, dict):
            shadow_cfg_override = raw_shadow_override

        raw_shadow = slot_cfg.get("shadow", True)
        if isinstance(raw_shadow, dict):
            # Legacy: allow dict in shadow (older authored specs).
            shadow_cfg_override = raw_shadow
            shadow_enabled = bool(raw_shadow.get("enabled", True))
        else:
            shadow_enabled = bool(raw_shadow)
        glow_enabled = bool(slot_cfg.get("glow", False))

        slot_stroke_width = stroke_width
        if slot_cfg.get("stroke_width_px") is not None:
            try:
                slot_stroke_width = int(slot_cfg.get("stroke_width_px"))
            except Exception:
                slot_stroke_width = stroke_width
        slot_stroke_width = max(0, int(slot_stroke_width))

        shadow_spec_slot = shadow_spec
        if shadow_cfg_override is not None:
            alpha = shadow_alpha
            if shadow_cfg_override.get("alpha") is not None:
                try:
                    alpha = float(shadow_cfg_override.get("alpha"))
                except Exception:
                    alpha = shadow_alpha
            alpha = max(0.0, min(1.0, float(alpha)))

            rgb = shadow_rgb_default
            if shadow_cfg_override.get("color") is not None:
                try:
                    c = _parse_color(str(shadow_cfg_override.get("color") or "#000000"))
                    rgb = (c[0], c[1], c[2])
                except Exception:
                    rgb = shadow_rgb_default

            offset_x, offset_y = off_x, off_y
            if shadow_cfg_override.get("offset_px") is not None:
                try:
                    v = shadow_cfg_override.get("offset_px") or [off_x, off_y]
                    offset_x = int(v[0])
                    offset_y = int(v[1])
                except Exception:
                    offset_x, offset_y = off_x, off_y

            blur_px = blur
            if shadow_cfg_override.get("blur_px") is not None:
                try:
                    blur_px = int(shadow_cfg_override.get("blur_px"))
                except Exception:
                    blur_px = blur

            shadow_spec_slot = ShadowSpec(
                color=(rgb[0], rgb[1], rgb[2], int(round(alpha * 255))),
                offset=(int(offset_x), int(offset_y)),
                blur=max(0, int(blur_px)),
            )

        fit = _fit_text_to_box(
            plain_text,
            font_path=font_path,
            base_size=base_size,
            max_width=max(1, w),
            max_height=max(1, h),
            max_lines=max(1, max_lines),
            stroke_width=slot_stroke_width if stroke_enabled else 0,
            tracking=tracking,
        )
        if not fit.lines:
            continue

        inline_spans: Optional[List[List[Tuple[int, int, RGBA]]]] = None
        if spans and len(fit.lines) == 1 and "\n" not in plain_text and fit.lines[0] == plain_text:
            mode = str(fill.get("mode") or "solid").strip().lower()
            if mode == "solid":
                try:
                    fallback_color = _parse_color(str(fill.get("color") or "#ffffff"))
                except Exception:
                    fallback_color = (255, 255, 255, 255)
                colored: List[Tuple[int, int, RGBA]] = []
                for start, end, span_fill_key in spans:
                    colored.append(
                        (start, end, _solid_fill_color_from_effects(effects, fill_key=span_fill_key, fallback=fallback_color))
                    )
                inline_spans = [colored]

        y_draw = y0
        if valign != "top":
            text_h = _total_text_height_px(
                lines=fit.lines,
                font_path=font_path,
                font_size=fit.font_size,
                line_gap=fit.line_gap,
                stroke_width=fit.stroke_width if stroke_enabled else 0,
            )
            if text_h > 0 and text_h < h:
                if valign == "bottom":
                    y_draw = int(y0 + (h - text_h))
                else:
                    y_draw = int(y0 + (h - text_h) // 2)

        backdrop_cfg = slot_cfg.get("backdrop")
        if isinstance(backdrop_cfg, dict) and bool(backdrop_cfg.get("enabled", True)):
            mode = str(backdrop_cfg.get("mode") or "brush_stroke").strip().lower()
            if mode in {"brush_stroke", "brushstroke", "brush", "image", "png", "asset"}:
                try:
                    bd_alpha = float(backdrop_cfg.get("alpha", 0.90))
                except Exception:
                    bd_alpha = 0.90
                bd_alpha = max(0.0, min(1.0, bd_alpha))

                try:
                    bd_color = _parse_color(str(backdrop_cfg.get("color") or "#000000"))
                except Exception:
                    bd_color = (0, 0, 0, 255)

                try:
                    pad_x = int(backdrop_cfg.get("pad_x_px", 84))
                except Exception:
                    pad_x = 84
                try:
                    pad_y = int(backdrop_cfg.get("pad_y_px", 24))
                except Exception:
                    pad_y = 24
                pad_x = max(0, int(pad_x))
                pad_y = max(0, int(pad_y))

                seed_raw = backdrop_cfg.get("seed")
                try:
                    seed = (
                        int(seed_raw)
                        if seed_raw is not None
                        else int(_stable_seed_u64(f"{video_id}|{template_id}|{slot_name}|backdrop|{mode}"))
                    )
                except Exception:
                    seed = int(_stable_seed_u64(f"{video_id}|{template_id}|{slot_name}|backdrop|{mode}"))

                # Compute a tight bbox for the actually-rendered text and place the backdrop behind it.
                font = _load_truetype(font_path, fit.font_size)
                tr = int(tracking or 0)
                sw = int(fit.stroke_width if stroke_enabled else 0)

                bbox_union: Optional[Tuple[int, int, int, int]] = None
                line_boxes: List[Tuple[int, int, int, int]] = []
                y_cursor = int(y_draw)
                for line in fit.lines:
                    if not line:
                        continue
                    bbox = _bbox_text_with_tracking(font, line, stroke_width=sw, tracking=tr)
                    bw = max(0, int(bbox[2] - bbox[0]))
                    bh = max(0, int(bbox[3] - bbox[1]))
                    if align == "center":
                        left_edge = x0 + max(0, (w - bw) // 2)
                    elif align == "right":
                        left_edge = x0 + max(0, w - bw)
                    else:
                        left_edge = x0
                    x_anchor = int(left_edge - bbox[0])
                    y_anchor = int(y_cursor - bbox[1])
                    abs_box = (
                        int(x_anchor + bbox[0]),
                        int(y_anchor + bbox[1]),
                        int(x_anchor + bbox[2]),
                        int(y_anchor + bbox[3]),
                    )
                    line_boxes.append(abs_box)
                    if bbox_union is None:
                        bbox_union = abs_box
                    else:
                        bbox_union = (
                            min(bbox_union[0], abs_box[0]),
                            min(bbox_union[1], abs_box[1]),
                            max(bbox_union[2], abs_box[2]),
                            max(bbox_union[3], abs_box[3]),
                        )
                    y_cursor += bh + int(fit.line_gap)

                per_line = bool(backdrop_cfg.get("per_line", False))
                boxes = line_boxes if per_line else ([bbox_union] if bbox_union is not None else [])

                if boxes and bd_alpha > 0.0:
                    boxes_px: List[Tuple[int, int, int, int]] = []
                    for b in boxes:
                        boxes_px.append((b[0] - pad_x, b[1] - pad_y, b[2] + pad_x, b[3] + pad_y))
                    if mode in {"brush_stroke", "brushstroke", "brush"}:
                        try:
                            roughness = float(backdrop_cfg.get("roughness", 0.25))
                        except Exception:
                            roughness = 0.25
                        try:
                            feather_px = int(backdrop_cfg.get("feather_px", 22))
                        except Exception:
                            feather_px = 22
                        try:
                            hole_count = int(backdrop_cfg.get("hole_count", 18))
                        except Exception:
                            hole_count = 18
                        try:
                            blur_px = int(backdrop_cfg.get("blur_px", 1))
                        except Exception:
                            blur_px = 1
                        for box_px in boxes_px:
                            out = _apply_brush_stroke_overlay(
                                out,
                                box_px=box_px,
                                color=bd_color,
                                alpha=bd_alpha,
                                seed=seed,
                                roughness=roughness,
                                feather_px=feather_px,
                                hole_count=hole_count,
                                blur_px=blur_px,
                            )
                    else:
                        image_path = str(backdrop_cfg.get("image_path") or "").strip()
                        if image_path:
                            fit_mode = str(backdrop_cfg.get("fit") or "cover").strip().lower()
                            colorize = bool(backdrop_cfg.get("colorize", False))
                            if per_line and colorize:
                                out = _apply_backdrop_image_overlay_multi(
                                    out,
                                    boxes_px=boxes_px,
                                    image_path=image_path,
                                    fit=fit_mode,
                                    colorize=colorize,
                                    color=bd_color,
                                    alpha=bd_alpha,
                                )
                            else:
                                for box_px in boxes_px:
                                    out = _apply_backdrop_image_overlay(
                                        out,
                                        box_px=box_px,
                                        image_path=image_path,
                                        fit=fit_mode,
                                        colorize=colorize,
                                        color=bd_color,
                                        alpha=bd_alpha,
                                    )

        out = _render_text_lines(
            out,
            lines=fit.lines,
            x=x0,
            y=y_draw,
            font_path=font_path,
            font_size=fit.font_size,
            line_gap=fit.line_gap,
            align=align,
            tracking=tracking,
            max_width=max(1, w),
            fill=fill,
            inline_spans=inline_spans,
            stroke_enabled=stroke_enabled,
            stroke_color=stroke_color,
            stroke_width=fit.stroke_width if stroke_enabled else 0,
            glow=glow_spec if glow_enabled else None,
            shadow=shadow_spec_slot if shadow_enabled else None,
        )

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Compose thumbnail text using a text_layout layer spec.")
    ap.add_argument("--text-layout-id", default="text_layout_v3", help="Layer spec id (from templates.json registry)")
    ap.add_argument("--channel", help="Channel code (e.g., CH10)")
    ap.add_argument("--video", help="Video number (e.g., 001)")
    ap.add_argument("--video-id", help="Video id override (e.g., CH10-001)")
    ap.add_argument("--base", required=True, help="Base image path (PNG/JPG)")
    ap.add_argument("--out", required=True, help="Output image path")
    args = ap.parse_args()

    video_id = ""
    if args.video_id:
        video_id = str(args.video_id).strip()
    elif args.channel and args.video:
        video_id = _normalize_video_id(args.channel, args.video)
    else:
        raise SystemExit("Provide --video-id or (--channel and --video).")

    spec = load_layer_spec_yaml(str(args.text_layout_id).strip())
    out_img = compose_text_layout(Path(args.base), text_layout_spec=spec, video_id=video_id)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_img.convert("RGB").save(out_path, format="PNG", optimize=True)
    print(f"[OK] wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
