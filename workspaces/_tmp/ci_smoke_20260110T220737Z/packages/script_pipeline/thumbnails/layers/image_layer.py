#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from factory_common.image_client import ImageClient, ImageGenerationError, ImageTaskOptions
from script_pipeline.thumbnails.io_utils import PngOutputMode, save_png_atomic


SUPPORTED_EXTS: Set[str] = {".png", ".jpg", ".jpeg", ".webp"}


@dataclass(frozen=True)
class BgEnhanceParams:
    brightness: float = 1.0
    contrast: float = 1.0
    color: float = 1.0
    gamma: float = 1.0

    def is_identity(self) -> bool:
        return all(abs(float(v) - 1.0) < 1e-6 for v in (self.brightness, self.contrast, self.color, self.gamma))


@dataclass(frozen=True)
class BackgroundSourceResult:
    bg_src: Optional[Path]
    legacy_moved_from: Optional[str]


@dataclass(frozen=True)
class BackgroundGenerationResult:
    raw_path: Path
    generated: Dict[str, Any]


def legacy_background_candidates(channel_root: Path, video: str) -> List[Path]:
    out: List[Path] = []
    for ext in sorted(SUPPORTED_EXTS):
        out.append(channel_root / f"{video}{ext}")
    for ext in sorted(SUPPORTED_EXTS):
        out.append(channel_root / f"{int(video)}{ext}")
    return out


def find_existing_background(video_dir: Path) -> Optional[Path]:
    preferred = [
        video_dir / "10_bg.png",
        video_dir / "10_bg.jpg",
        video_dir / "10_bg.jpeg",
        video_dir / "10_bg.webp",
        video_dir / "90_bg_legacy.png",
        video_dir / "90_bg_legacy.jpg",
        video_dir / "90_bg_legacy.jpeg",
        video_dir / "90_bg_legacy.webp",
    ]
    for p in preferred:
        if p.exists() and p.is_file():
            return p
    for p in sorted(video_dir.iterdir(), key=lambda path: path.as_posix().lower()):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            return p
    return None


def find_existing_portrait(video_dir: Path) -> Optional[Path]:
    """
    Find a per-video portrait cutout (foreground) file.

    Convention:
      - 20_portrait.png (preferred, with transparency)
      - also accepts jpg/jpeg/webp, but PNG with alpha is recommended.
    """
    preferred = [
        video_dir / "20_portrait.png",
        video_dir / "20_portrait.jpg",
        video_dir / "20_portrait.jpeg",
        video_dir / "20_portrait.webp",
    ]
    for p in preferred:
        if p.exists() and p.is_file():
            return p
    return None


def _apply_foreground_enhancements(img: Image.Image, *, brightness: float, contrast: float, color: float) -> Image.Image:
    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")
    if abs(float(brightness) - 1.0) >= 1e-6:
        rgb = ImageEnhance.Brightness(rgb).enhance(float(brightness))
    if abs(float(contrast) - 1.0) >= 1e-6:
        rgb = ImageEnhance.Contrast(rgb).enhance(float(contrast))
    if abs(float(color) - 1.0) >= 1e-6:
        rgb = ImageEnhance.Color(rgb).enhance(float(color))
    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def _trim_transparent_border(img: Image.Image) -> Image.Image:
    if img.mode != "RGBA":
        return img
    alpha = img.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return img
    return img.crop(bbox)


def composite_portrait_on_base(
    base: Image.Image,
    *,
    portrait_path: Path,
    dest_box_px: Tuple[int, int, int, int],
    anchor: str = "bottom_center",
    portrait_zoom: float = 1.0,
    portrait_offset_px: Tuple[int, int] = (0, 0),
    trim_transparent: bool = False,
    fg_brightness: float = 1.20,
    fg_contrast: float = 1.08,
    fg_color: float = 0.98,
    shadow_offset_px: Tuple[int, int] = (0, 10),
    shadow_blur_px: int = 18,
    shadow_alpha: float = 0.75,
) -> Image.Image:
    """
    Composite a portrait (ideally transparent PNG) onto the base image.
    """
    img = base.convert("RGBA")
    x0, y0, w, h = (int(dest_box_px[0]), int(dest_box_px[1]), int(dest_box_px[2]), int(dest_box_px[3]))
    if w <= 0 or h <= 0:
        return img

    with Image.open(portrait_path) as fg_in:
        fg = fg_in.convert("RGBA")
    if trim_transparent:
        fg = _trim_transparent_border(fg)

    # Resize to fit within dest box (preserve aspect ratio).
    pw, ph = fg.size
    if pw <= 0 or ph <= 0:
        return img
    zoom = float(portrait_zoom) if float(portrait_zoom) > 0 else 1.0
    scale = min(w / float(pw), h / float(ph)) * zoom
    new_w = max(1, int(round(pw * scale)))
    new_h = max(1, int(round(ph * scale)))
    fg = fg.resize((new_w, new_h), Image.LANCZOS)
    fg = _apply_foreground_enhancements(fg, brightness=fg_brightness, contrast=fg_contrast, color=fg_color)

    # Placement within the dest box.
    if str(anchor).lower() == "center":
        px = int(x0 + (w - new_w) // 2)
        py = int(y0 + (h - new_h) // 2)
    else:
        # default: bottom_center
        px = int(x0 + (w - new_w) // 2)
        py = int(y0 + (h - new_h))

    px += int(portrait_offset_px[0])
    py += int(portrait_offset_px[1])

    # Soft shadow from alpha channel.
    alpha = fg.getchannel("A")
    if shadow_alpha > 0 and (shadow_blur_px > 0 or shadow_offset_px != (0, 0)):
        shadow_col = (0, 0, 0, int(round(max(0.0, min(1.0, float(shadow_alpha))) * 255)))
        shadow = Image.new("RGBA", fg.size, shadow_col)
        shadow.putalpha(alpha)
        if shadow_blur_px > 0:
            shadow = shadow.filter(ImageFilter.GaussianBlur(radius=int(shadow_blur_px)))
        img.alpha_composite(shadow, dest=(px + int(shadow_offset_px[0]), py + int(shadow_offset_px[1])))

    img.alpha_composite(fg, dest=(px, py))
    return img


@contextmanager
def composited_portrait_path(
    base_bg_path: Path,
    *,
    portrait_path: Path,
    dest_box_px: Tuple[int, int, int, int],
    temp_prefix: str,
    anchor: str = "bottom_center",
    portrait_zoom: float = 1.0,
    portrait_offset_px: Tuple[int, int] = (0, 0),
    trim_transparent: bool = False,
    fg_brightness: float = 1.20,
    fg_contrast: float = 1.08,
    fg_color: float = 0.98,
    shadow_offset_px: Tuple[int, int] = (0, 10),
    shadow_blur_px: int = 18,
    shadow_alpha: float = 0.75,
) -> Iterator[Path]:
    """
    Yield a temp PNG path for (background + portrait) composited image.
    """
    tmp_path: Optional[Path] = None
    handle = tempfile.NamedTemporaryFile(prefix=temp_prefix, suffix=".png", delete=False)
    try:
        tmp_path = Path(handle.name)
    finally:
        handle.close()

    try:
        base = Image.open(base_bg_path).convert("RGBA")
        out = composite_portrait_on_base(
            base,
            portrait_path=portrait_path,
            dest_box_px=dest_box_px,
            anchor=anchor,
            portrait_zoom=float(portrait_zoom),
            portrait_offset_px=(int(portrait_offset_px[0]), int(portrait_offset_px[1])),
            trim_transparent=bool(trim_transparent),
            fg_brightness=float(fg_brightness),
            fg_contrast=float(fg_contrast),
            fg_color=float(fg_color),
            shadow_offset_px=(int(shadow_offset_px[0]), int(shadow_offset_px[1])),
            shadow_blur_px=int(shadow_blur_px),
            shadow_alpha=float(shadow_alpha),
        )
        out.save(tmp_path, format="PNG", optimize=True)
        yield tmp_path
    finally:
        if tmp_path:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _soft_ellipse_mask(size: Tuple[int, int], *, box: Tuple[int, int, int, int], blur_px: int) -> Image.Image:
    w, h = size
    x0, y0, x1, y1 = box
    x0 = max(0, min(w, int(x0)))
    y0 = max(0, min(h, int(y0)))
    x1 = max(0, min(w, int(x1)))
    y1 = max(0, min(h, int(y1)))
    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((x0, y0, x1, y1), fill=255)
    if blur_px > 0:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=int(blur_px)))
    return mask


@contextmanager
def suppressed_center_region_path(
    base_bg_path: Path,
    *,
    dest_box_px: Tuple[int, int, int, int],
    temp_prefix: str,
    pad_ratio: float = 0.06,
    blur_ratio: float = 0.03,
    mask_blur_ratio: float = 0.04,
    brightness: float = 0.60,
    contrast: float = 0.95,
) -> Iterator[Path]:
    """
    Yield a temp PNG path where the center region is blurred+darkened.

    Used to suppress background portraits (avoid "double face") before overlaying
    the real cutout portrait.
    """
    tmp_path: Optional[Path] = None
    handle = tempfile.NamedTemporaryFile(prefix=temp_prefix, suffix=".png", delete=False)
    try:
        tmp_path = Path(handle.name)
    finally:
        handle.close()

    try:
        with Image.open(base_bg_path) as im:
            img = im.convert("RGBA")
        w, h = img.size
        x0, y0, bw, bh = (int(dest_box_px[0]), int(dest_box_px[1]), int(dest_box_px[2]), int(dest_box_px[3]))
        pad = int(round(min(w, h) * float(pad_ratio)))
        box = (x0 - pad, y0 - pad, x0 + bw + pad, y0 + bh + pad)

        blurred = img.filter(ImageFilter.GaussianBlur(radius=int(round(min(w, h) * float(blur_ratio)))))
        blurred_rgb = blurred.convert("RGB")
        blurred_rgb = ImageEnhance.Brightness(blurred_rgb).enhance(float(brightness))
        blurred_rgb = ImageEnhance.Contrast(blurred_rgb).enhance(float(contrast))
        blurred_dark = blurred_rgb.convert("RGBA")
        blurred_dark.putalpha(img.getchannel("A"))

        mask = _soft_ellipse_mask(img.size, box=box, blur_px=int(round(min(w, h) * float(mask_blur_ratio))))
        out = Image.composite(blurred_dark, img, mask)
        out.save(tmp_path, format="PNG", optimize=True)
        yield tmp_path
    finally:
        if tmp_path:
            try:
                tmp_path.unlink()
            except Exception:
                pass


def resolve_background_source(*, video_dir: Path, channel_root: Path, video: str) -> BackgroundSourceResult:
    bg_src = find_existing_background(video_dir)
    legacy_moved_from: Optional[str] = None
    if bg_src is None:
        for legacy in legacy_background_candidates(channel_root, video):
            if legacy.exists() and legacy.is_file():
                dest = video_dir / "90_bg_legacy.png"
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(legacy), str(dest))
                bg_src = dest
                legacy_moved_from = str(legacy)
                break
    return BackgroundSourceResult(bg_src=bg_src, legacy_moved_from=legacy_moved_from)


def generate_background_with_retries(
    *,
    client: ImageClient,
    prompt: str,
    model_key: str,
    negative_prompt: Optional[str] = None,
    out_raw_path: Path,
    video_id: str,
    max_attempts: int,
    sleep_sec: float,
) -> BackgroundGenerationResult:
    result = None
    last_exc: Optional[Exception] = None
    override = (os.getenv("IMAGE_CLIENT_FORCE_MODEL_KEY_THUMBNAIL_IMAGE_GEN") or "").strip()
    effective_model_key = override or model_key
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            print(f"{video_id}: generating bg via {effective_model_key} (attempt {attempt}/{attempts}) ...")
            result = client.generate(
                ImageTaskOptions(
                    task="thumbnail_image_gen",
                    prompt=str(prompt),
                    aspect_ratio="16:9",
                    size="1920x1080",
                    n=1,
                    negative_prompt=str(negative_prompt).strip() if negative_prompt else None,
                    extra={"model_key": effective_model_key, "allow_fallback": False},
                )
            )
            if result.images:
                break
            last_exc = RuntimeError("image generation returned no image bytes")
        except ImageGenerationError as exc:
            last_exc = exc
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
        time.sleep(max(0.5, float(sleep_sec)))

    if not result or not result.images:
        raise RuntimeError(f"image generation failed for {video_id}: {last_exc}") from last_exc

    out_raw_path.parent.mkdir(parents=True, exist_ok=True)
    out_raw_path.write_bytes(result.images[0])
    generated = {
        "provider": result.provider,
        "model": result.model,
        "model_key": effective_model_key,
        "request_id": result.request_id,
        "metadata": result.metadata,
    }
    time.sleep(max(0.0, float(sleep_sec)))
    return BackgroundGenerationResult(raw_path=out_raw_path, generated=generated)


def crop_resize_to_16x9(
    src_path: Path,
    dest_path: Path,
    *,
    width: int,
    height: int,
    output_mode: PngOutputMode = "final",
) -> None:
    """
    Center-crop to 16:9 then resize to (width,height), and save as PNG.

    This guarantees the output matches the requested canvas size (prevents letterboxing).
    """
    with Image.open(src_path) as img:
        img = img.convert("RGBA")
        src_w, src_h = img.size
        target_ratio = width / float(height)
        src_ratio = src_w / float(src_h)

        if abs(src_ratio - target_ratio) > 1e-3:
            if src_ratio > target_ratio:
                new_w = int(src_h * target_ratio)
                left = max(0, (src_w - new_w) // 2)
                img = img.crop((left, 0, left + new_w, src_h))
            else:
                new_h = int(src_w / target_ratio)
                top = max(0, (src_h - new_h) // 2)
                img = img.crop((0, top, src_w, top + new_h))

        img = img.resize((width, height), Image.LANCZOS)
        save_png_atomic(img, dest_path, mode=output_mode, verify=True)


def _apply_gamma_rgb(img: Image.Image, gamma: float) -> Image.Image:
    g = float(gamma)
    if g <= 0:
        raise ValueError("gamma must be > 0")
    if abs(g - 1.0) < 1e-6:
        return img
    lut = [int(round(((i / 255.0) ** g) * 255.0)) for i in range(256)]
    if img.mode == "RGB":
        return img.point(lut * 3)
    if img.mode == "L":
        return img.point(lut)
    return img.convert("RGB").point(lut * 3)


def apply_bg_enhancements(img: Image.Image, *, params: BgEnhanceParams) -> Image.Image:
    if params.is_identity():
        return img

    rgba = img.convert("RGBA")
    alpha = rgba.getchannel("A")
    rgb = rgba.convert("RGB")

    if abs(float(params.gamma) - 1.0) >= 1e-6:
        rgb = _apply_gamma_rgb(rgb, float(params.gamma))
    if abs(float(params.brightness) - 1.0) >= 1e-6:
        rgb = ImageEnhance.Brightness(rgb).enhance(float(params.brightness))
    if abs(float(params.contrast) - 1.0) >= 1e-6:
        rgb = ImageEnhance.Contrast(rgb).enhance(float(params.contrast))
    if abs(float(params.color) - 1.0) >= 1e-6:
        rgb = ImageEnhance.Color(rgb).enhance(float(params.color))

    out = rgb.convert("RGBA")
    out.putalpha(alpha)
    return out


def apply_horizontal_band_enhancements(
    img: Image.Image,
    *,
    x0: float,
    x1: float,
    params: BgEnhanceParams,
    power: float = 1.0,
) -> Image.Image:
    """
    Apply additional enhancements to a horizontal band (x0..x1) with a smooth gradient mask.

    This is useful for "人物を濃く" のように、画面右側だけトーンを締めたいときに使う。
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
            t = 0.0
        elif x >= end_px:
            t = 1.0
        else:
            t = (x - start_px) / span
        if abs(p - 1.0) > 1e-6:
            t = t**p
        values.append(int(round(t * 255)))

    mask_row = Image.new("L", (w, 1), 0)
    mask_row.putdata(values)
    mask = mask_row.resize((w, h))
    return Image.composite(adjusted, img, mask)

def apply_pan_zoom(
    img: Image.Image,
    *,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
) -> Image.Image:
    """
    Apply a lightweight pan/zoom (digital zoom + crop) to adjust framing.

    - zoom: >=1.0 (1.0 = no zoom)
    - pan_x/pan_y: -1..1 stays within "cover" range (0 = centered).
      Wider values intentionally reveal the base fill (pasteboard-style).
    """
    z = float(zoom)
    w, h = img.size
    if w <= 0 or h <= 0:
        return img

    # Keep sane bounds (UI/editor may push outside [-1, 1]).
    px = max(-5.0, min(5.0, float(pan_x)))
    py = max(-5.0, min(5.0, float(pan_y)))

    # When zoom==1, allow pasteboard-style translation by revealing the base fill.
    if z <= 1.0 + 1e-6:
        if abs(px) < 1e-6 and abs(py) < 1e-6:
            return img
        out = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        base = img.convert("RGBA")
        max_dx = max(1, w // 2)
        max_dy = max(1, h // 2)
        left = int(round(px * max_dx))
        top = int(round(py * max_dy))
        src_x0 = max(0, left)
        src_y0 = max(0, top)
        src_x1 = min(w, left + w)
        src_y1 = min(h, top + h)
        if src_x1 > src_x0 and src_y1 > src_y0:
            crop = base.crop((src_x0, src_y0, src_x1, src_y1))
            dst_x0 = src_x0 - left
            dst_y0 = src_y0 - top
            out.paste(crop, (dst_x0, dst_y0), crop)
        return out

    scaled_w = max(1, int(round(w * z)))
    scaled_h = max(1, int(round(h * z)))
    scaled = img.resize((scaled_w, scaled_h), Image.LANCZOS)

    max_dx = max(0, (scaled_w - w) // 2)
    max_dy = max(0, (scaled_h - h) // 2)

    cx = (scaled_w // 2) + int(round(px * max_dx))
    cy = (scaled_h // 2) + int(round(py * max_dy))

    left = int(cx - (w // 2))
    top = int(cy - (h // 2))

    # If the crop extends outside the scaled image, paste onto a black canvas.
    out = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    src_x0 = max(0, left)
    src_y0 = max(0, top)
    src_x1 = min(scaled_w, left + w)
    src_y1 = min(scaled_h, top + h)
    if src_x1 > src_x0 and src_y1 > src_y0:
        crop = scaled.crop((src_x0, src_y0, src_x1, src_y1))
        dst_x0 = src_x0 - left
        dst_y0 = src_y0 - top
        out.paste(crop, (dst_x0, dst_y0), crop)
    return out


@contextmanager
def enhanced_bg_path(
    base_bg_path: Path,
    *,
    params: BgEnhanceParams,
    temp_prefix: str,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    band_params: Optional[BgEnhanceParams] = None,
    band_x0: float = 0.0,
    band_x1: float = 0.0,
    band_power: float = 1.0,
) -> Iterator[Path]:
    """
    Yield a path to an enhanced background PNG.

    - If all settings are identity -> yield `base_bg_path` directly.
    - Otherwise, write a temp PNG and clean it up on exit.
    """
    z = float(zoom)
    px = float(pan_x)
    py = float(pan_y)
    if (
        params.is_identity()
        and (band_params is None or band_params.is_identity())
        and abs(z - 1.0) < 1e-6
        and abs(px) < 1e-6
        and abs(py) < 1e-6
    ):
        yield base_bg_path
        return

    tmp_path: Optional[Path] = None
    handle = tempfile.NamedTemporaryFile(prefix=temp_prefix, suffix=".png", delete=False)
    try:
        tmp_path = Path(handle.name)
    finally:
        handle.close()

    try:
        bg_img = Image.open(base_bg_path).convert("RGBA")
        if abs(z - 1.0) >= 1e-6 or abs(px) >= 1e-6 or abs(py) >= 1e-6:
            bg_img = apply_pan_zoom(bg_img, zoom=z, pan_x=px, pan_y=py)
        if not params.is_identity():
            bg_img = apply_bg_enhancements(bg_img, params=params)
        if band_params is not None and not band_params.is_identity():
            bg_img = apply_horizontal_band_enhancements(
                bg_img,
                x0=float(band_x0),
                x1=float(band_x1),
                params=band_params,
                power=float(band_power),
            )
        bg_img.save(tmp_path, format="PNG", optimize=True)
        yield tmp_path
    finally:
        if tmp_path:
            try:
                tmp_path.unlink()
            except Exception:
                pass
