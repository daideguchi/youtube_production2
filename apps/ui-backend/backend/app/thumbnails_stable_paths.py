from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from fastapi import HTTPException

from factory_common.paths import thumbnails_root as ssot_thumbnails_root

THUMBNAIL_ASSETS_DIR = ssot_thumbnails_root() / "assets"


def normalize_thumbnail_stable_id(raw: Optional[str]) -> Optional[str]:
    """
    Normalize a "stable output id" such as `00_thumb_1` / `00_thumb_2`.

    Accepts:
      - 00_thumb_1
      - 00_thumb_1.png / 00_thumb_1.jpg / 00_thumb_1.webp
      - thumb_1 / thumb_2 (legacy labels)
      - a / b (legacy labels)
      - default / __default__ / 00_thumb / thumb (treated as non-stable / canonical output)
    """
    if raw is None:
        return None
    value = str(raw or "").strip()
    if not value:
        return None
    cleaned = value.split("?", 1)[0].split("#", 1)[0].strip()
    if not cleaned:
        return None
    base = Path(cleaned).name.strip()
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        if base.lower().endswith(ext):
            base = base[: -len(ext)]
            break
    lowered = base.strip().lower()
    if lowered in {"default", "__default__", "00_thumb", "thumb"}:
        return None
    if lowered in {"thumb_1", "thumb1", "1", "a"}:
        return "00_thumb_1"
    if lowered in {"thumb_2", "thumb2", "2", "b"}:
        return "00_thumb_2"
    normalized = base.strip()
    if normalized.startswith("00_thumb_") and normalized[len("00_thumb_") :].isdigit():
        return normalized

    match = re.search(r"(00_thumb_\\d+)", lowered)
    if match:
        return match.group(1)

    match = re.search(r"(?:^|[^a-z0-9])(thumb[_-]?(1|2))(?:$|[^a-z0-9])", lowered)
    if match:
        return "00_thumb_1" if match.group(2) == "1" else "00_thumb_2"

    raise HTTPException(
        status_code=400,
        detail="stable must be like 00_thumb_1 / 00_thumb_2 (or 00_thumb_<n>)",
    )


def thumb_spec_stable_path(channel_code: str, video_number: str, stable: str) -> Path:
    return THUMBNAIL_ASSETS_DIR / channel_code / video_number / f"thumb_spec.{stable}.json"


def text_line_spec_stable_path(channel_code: str, video_number: str, stable: str) -> Path:
    return THUMBNAIL_ASSETS_DIR / channel_code / video_number / f"text_line_spec.{stable}.json"


def elements_spec_stable_path(channel_code: str, video_number: str, stable: str) -> Path:
    return THUMBNAIL_ASSETS_DIR / channel_code / video_number / f"elements_spec.{stable}.json"

