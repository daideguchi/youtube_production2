#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from factory_common import paths as fpaths

from script_pipeline.thumbnails.compiler.layer_specs import (
    find_text_layout_item_for_video,
    load_layer_spec_yaml,
)


RGBA = Tuple[int, int, int, int]


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


def _measure_line(font: ImageFont.FreeTypeFont, text: str, stroke_width: int) -> Tuple[int, int]:
    if not text:
        return (0, 0)
    bbox = font.getbbox(text, stroke_width=int(stroke_width or 0), anchor="la")
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    return (max(0, w), max(0, h))


def _wrap_tokens_to_lines(
    tokens: Sequence[str],
    font: ImageFont.FreeTypeFont,
    max_width: int,
    max_lines: int,
    stroke_width: int,
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
        w, _ = _measure_line(font, candidate, stroke_width=stroke_width)
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
                w2, _ = _measure_line(font, cand, stroke_width=stroke_width)
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
    min_size: int = 22,
) -> FitResult:
    raw = (text or "").strip()
    if not raw:
        return FitResult(lines=[], font_size=base_size, line_gap=0, stroke_width=stroke_width)

    tokens = _tokenize_for_wrap(raw)
    size = max(min_size, int(base_size))
    while size >= min_size:
        font = ImageFont.truetype(font_path, size)
        lines, overflow = _wrap_tokens_to_lines(tokens, font, max_width=max_width, max_lines=max_lines, stroke_width=stroke_width)
        gap = max(2, int(round(size * 0.03)))
        total_h = 0
        max_w = 0
        for idx, line in enumerate(lines):
            w, h = _measure_line(font, line, stroke_width=stroke_width)
            max_w = max(max_w, w)
            total_h += h
            if idx > 0:
                total_h += gap
        if (not overflow) and max_w <= max_width and total_h <= max_height:
            return FitResult(lines=lines, font_size=size, line_gap=gap, stroke_width=stroke_width)
        size -= 2

    font = ImageFont.truetype(font_path, min_size)
    lines, _ = _wrap_tokens_to_lines(tokens, font, max_width=max_width, max_lines=max_lines, stroke_width=stroke_width)
    gap = max(2, int(round(min_size * 0.03)))
    return FitResult(lines=lines, font_size=min_size, line_gap=gap, stroke_width=stroke_width)


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


def _discover_font_path_by_family(prefer: Sequence[str]) -> Optional[str]:
    prefer_norm = [p.lower() for p in prefer if isinstance(p, str) and p.strip()]
    if not prefer_norm:
        return None
    for line in _fc_list_lines():
        if ":" not in line:
            continue
        font_path = line.split(":", 1)[0].strip()
        hay = line.lower()
        if any(p in hay for p in prefer_norm):
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
    if isinstance(families, list):
        found = _discover_font_path_by_family([str(x) for x in families])
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
    max_width: int,
    fill: Dict[str, Any],
    stroke_enabled: bool,
    stroke_color: RGBA,
    stroke_width: int,
    glow: Optional[ShadowSpec],
    shadow: Optional[ShadowSpec],
) -> Image.Image:
    img = base.convert("RGBA")
    font = ImageFont.truetype(font_path, font_size)

    # Precompute packed positions so that the *visible* top of each line starts at y and lines are tightly stacked.
    packed: List[Tuple[str, int, int, Tuple[int, int, int, int]]] = []
    y_cursor = int(y)
    sw = int(stroke_width if stroke_enabled else 0)
    for line in lines:
        if not line:
            continue
        bbox = font.getbbox(line, stroke_width=sw, anchor="la")
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

    # Glow: draw once on a separate layer, then blur (offset can be 0,0).
    if glow and (glow.offset != (0, 0) or glow.blur > 0):
        glow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_layer)
        for line, x_pos, y_pos, _bbox in packed:
            gd.text(
                (x_pos + glow.offset[0], y_pos + glow.offset[1]),
                line,
                font=font,
                fill=glow.color,
                stroke_width=0,
                anchor="la",
            )
        if glow.blur > 0:
            glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=glow.blur))
        img = Image.alpha_composite(img, glow_layer)

    # Shadow: draw once on a separate layer, then blur.
    if shadow and (shadow.offset != (0, 0) or shadow.blur > 0):
        shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow_layer)
        for line, x_pos, y_pos, _bbox in packed:
            sd.text(
                (x_pos + shadow.offset[0], y_pos + shadow.offset[1]),
                line,
                font=font,
                fill=shadow.color,
                stroke_width=0,
                anchor="la",
            )
        if shadow.blur > 0:
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=shadow.blur))
        img = Image.alpha_composite(img, shadow_layer)

    # Stroke layer (optional) — keeps gradient fill intact.
    if stroke_enabled and stroke_width > 0:
        stroke_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(stroke_layer)
        for line, x_pos, y_pos, _bbox in packed:
            sd.text(
                (x_pos, y_pos),
                line,
                font=font,
                fill=(0, 0, 0, 0),
                stroke_width=stroke_width,
                stroke_fill=stroke_color,
                anchor="la",
            )
        img = Image.alpha_composite(img, stroke_layer)

    # Fill
    mode = str(fill.get("mode") or "solid").strip().lower()
    if mode == "solid":
        color = _parse_color(str(fill.get("color") or "#ffffff"))
        draw = ImageDraw.Draw(img)
        for line, x_pos, y_pos, _bbox in packed:
            draw.text((x_pos, y_pos), line, font=font, fill=color, anchor="la")
        return img

    if mode == "linear_gradient":
        stops = fill.get("stops") or []
        for line, x_anchor, y_anchor, _bbox in packed:
            bbox0 = font.getbbox(line, stroke_width=0, anchor="la")
            w = max(1, bbox0[2] - bbox0[0])
            h = max(1, bbox0[3] - bbox0[1])
            grad = _build_vertical_gradient((w, h), stops=stops)

            mask = Image.new("L", (w, h), 0)
            md = ImageDraw.Draw(mask)
            # Draw into the tight bbox so (0,0) aligns with bbox0's top-left.
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

def compose_text_layout(
    base_image_path: Path,
    *,
    text_layout_spec: Dict[str, Any],
    video_id: str,
    text_override: Optional[Dict[str, str]] = None,
) -> Image.Image:
    base = Image.open(base_image_path).convert("RGBA")

    item = find_text_layout_item_for_video(text_layout_spec, video_id)
    if not isinstance(item, dict):
        raise KeyError(f"video_id not found in text_layout spec: {video_id}")

    template_id = str(item.get("template_id") or "").strip()
    if not template_id:
        raise ValueError(f"missing template_id for video_id={video_id}")

    global_cfg = text_layout_spec.get("global") if isinstance(text_layout_spec, dict) else None
    if not isinstance(global_cfg, dict):
        raise ValueError("text_layout.global is missing")

    overlays_cfg = global_cfg.get("overlays") if isinstance(global_cfg.get("overlays"), dict) else {}
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

    fonts_cfg = global_cfg.get("fonts") if isinstance(global_cfg.get("fonts"), dict) else {}
    effects = global_cfg.get("effects_defaults") if isinstance(global_cfg.get("effects_defaults"), dict) else {}
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
        raw_text = _decode_text_escapes(raw_text).strip()
        if not raw_text:
            continue

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

        base_size = int(slot_cfg.get("base_size_px", 64))
        max_lines = int(slot_cfg.get("max_lines", 2))
        align = str(slot_cfg.get("align") or "left").strip().lower()
        if align not in {"left", "center", "right"}:
            align = "left"

        stroke_enabled = bool(slot_cfg.get("stroke", True))
        shadow_enabled = bool(slot_cfg.get("shadow", True))
        glow_enabled = bool(slot_cfg.get("glow", False))

        fit = _fit_text_to_box(
            raw_text,
            font_path=font_path,
            base_size=base_size,
            max_width=max(1, w),
            max_height=max(1, h),
            max_lines=max(1, max_lines),
            stroke_width=stroke_width if stroke_enabled else 0,
        )
        if not fit.lines:
            continue

        out = _render_text_lines(
            out,
            lines=fit.lines,
            x=x0,
            y=y0,
            font_path=font_path,
            font_size=fit.font_size,
            line_gap=fit.line_gap,
            align=align,
            max_width=max(1, w),
            fill=fill,
            stroke_enabled=stroke_enabled,
            stroke_color=stroke_color,
            stroke_width=fit.stroke_width if stroke_enabled else 0,
            glow=glow_spec if glow_enabled else None,
            shadow=shadow_spec if shadow_enabled else None,
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
