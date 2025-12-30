#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image

from script_pipeline.thumbnails.compiler.compose_text_layout import compose_text_layout
from script_pipeline.thumbnails.io_utils import PngOutputMode, save_png_atomic


def compose_text_to_png(
    base_image_path: Path,
    *,
    text_layout_spec: Dict[str, Any],
    video_id: str,
    out_path: Path,
    output_mode: PngOutputMode = "final",
    optimize: Optional[bool] = None,
    compress_level: Optional[int] = None,
    text_override: Optional[Dict[str, str]] = None,
    template_id_override: Optional[str] = None,
    effects_override: Optional[Dict[str, Any]] = None,
    overlays_override: Optional[Dict[str, Any]] = None,
) -> Image.Image:
    """
    Compose text onto a base image and write PNG.

    Note: image sizing/brightness is handled by Image Layer; this layer only renders text.
    """
    out_img = compose_text_layout(
        Path(base_image_path),
        text_layout_spec=text_layout_spec,
        video_id=str(video_id),
        text_override=text_override,
        template_id_override=template_id_override,
        effects_override=effects_override,
        overlays_override=overlays_override,
    )
    save_png_atomic(
        out_img,
        out_path,
        mode=output_mode,
        optimize=optimize,
        compress_level=compress_level,
        verify=True,
    )
    return out_img
