#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Tuple

from PIL import Image, ImageEnhance

from factory_common.image_client import ImageClient, ImageGenerationError, ImageTaskOptions


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
    out_raw_path: Path,
    video_id: str,
    max_attempts: int,
    sleep_sec: float,
) -> BackgroundGenerationResult:
    result = None
    last_exc: Optional[Exception] = None
    attempts = max(1, int(max_attempts))
    for attempt in range(1, attempts + 1):
        try:
            print(f"{video_id}: generating bg via {model_key} (attempt {attempt}/{attempts}) ...")
            result = client.generate(
                ImageTaskOptions(
                    task="thumbnail_image_gen",
                    prompt=str(prompt),
                    aspect_ratio="16:9",
                    size="1920x1080",
                    n=1,
                    extra={"model_key": model_key, "allow_fallback": False},
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
        "model_key": model_key,
        "request_id": result.request_id,
        "metadata": result.metadata,
    }
    time.sleep(max(0.0, float(sleep_sec)))
    return BackgroundGenerationResult(raw_path=out_raw_path, generated=generated)


def crop_resize_to_16x9(src_path: Path, dest_path: Path, *, width: int, height: int) -> None:
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
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest_path, format="PNG", optimize=True)


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


@contextmanager
def enhanced_bg_path(base_bg_path: Path, *, params: BgEnhanceParams, temp_prefix: str) -> Iterator[Path]:
    """
    Yield a path to an enhanced background PNG.

    - If params are identity -> yield `base_bg_path` directly.
    - Otherwise, write a temp PNG and clean it up on exit.
    """
    if params.is_identity():
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
        bg_img = apply_bg_enhancements(bg_img, params=params)
        bg_img.save(tmp_path, format="PNG", optimize=True)
        yield tmp_path
    finally:
        if tmp_path:
            try:
                tmp_path.unlink()
            except Exception:
                pass
