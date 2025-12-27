#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image

from script_pipeline.thumbnails.compiler.compose_text_layout import compose_text_layout


def compose_text_to_png(
    base_image_path: Path,
    *,
    text_layout_spec: Dict[str, Any],
    video_id: str,
    out_path: Path,
    optimize: bool = True,
    text_override: Optional[Dict[str, str]] = None,
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
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_img.save(out_path, format="PNG", optimize=bool(optimize))
    return out_img

