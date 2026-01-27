from __future__ import annotations

import base64
import io
import mimetypes
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

router = APIRouter(tags=["thumbnails"])

_MISSING_THUMB_PNG_BASE64 = (
    # 2x2 gray PNG (tiny fallback when Pillow isn't available).
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR4nGM8ceLEfwYGBgYm"
    "BigAADOoA1v30LPTAAAAAElFTkSuQmCC"
)


def _is_default_thumbnail_png(name: str) -> bool:
    base = (name or "").strip()
    if not base:
        return False
    if not base.lower().endswith(".png"):
        return False
    # Common UI references: 00_thumb.png / 00_thumb_1.png / 00_thumb_2.png / ...
    return bool(re.fullmatch(r"00_thumb(?:_\d+)?\.png", base, flags=re.IGNORECASE))


def _missing_thumb_placeholder_enabled() -> bool:
    raw = (os.getenv("YTM_THUMBNAILS_MISSING_PLACEHOLDER") or "").strip().lower()
    if not raw:
        return False
    return raw in {"1", "true", "yes", "y", "on"}


@lru_cache(maxsize=512)
def _missing_thumb_png_bytes(label: str) -> bytes:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return base64.b64decode(_MISSING_THUMB_PNG_BASE64)

    img = Image.new("RGBA", (640, 360), (230, 230, 230, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    lines = ["MISSING THUMBNAIL", label]
    y = 12
    for line in lines:
        draw.text((12, y), line, fill=(20, 20, 20, 255), font=font)
        y += 16
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@router.get("/thumbnails/assets/{channel}/{video}/{asset_path:path}")
def get_thumbnail_asset(channel: str, video: str, asset_path: str):
    from backend import main as backend_main

    # Backward-compatible special-case:
    # Some UIs derive QC contactsheet URLs from the file path
    # `workspaces/thumbnails/assets/<CHxx>/library/qc/contactsheet.png` and request:
    #   /thumbnails/assets/<CHxx>/library/qc/contactsheet.png
    # The canonical route for library assets is `/thumbnails/library/<CHxx>/...`,
    # but keep the legacy path working to avoid hiding QC during incidents.
    if (video or "").strip().lower() == "library":
        return get_thumbnail_library_asset(channel=channel, asset_path=asset_path)

    channel_code = channel.strip().upper()
    if not channel_code or Path(channel_code).name != channel_code:
        raise HTTPException(status_code=404, detail="invalid channel")
    video_number = backend_main._coerce_video_from_dir(video)
    if not video_number:
        raise HTTPException(status_code=404, detail="invalid video")
    if not asset_path or asset_path.strip() == "":
        raise HTTPException(status_code=404, detail="invalid asset")
    rel_asset = Path(asset_path)
    if rel_asset.is_absolute():
        raise HTTPException(status_code=404, detail="invalid asset")
    if any(part == ".." for part in rel_asset.parts):
        raise HTTPException(status_code=404, detail="invalid asset")

    candidates: List[tuple[Path, Path]] = []

    asset_root = backend_main.THUMBNAIL_ASSETS_DIR / channel_code / video_number
    candidates.append((asset_root, asset_root / rel_asset))
    channel_dir = backend_main.find_channel_directory(channel_code)
    if channel_dir:
        channel_root = channel_dir / "thumbnails" / video_number
        candidates.append((channel_root, channel_root / rel_asset))

    for root, candidate in candidates:
        if not root.exists():
            continue
        try:
            resolved_root = root.resolve()
            resolved_candidate = candidate.resolve()
            resolved_candidate.relative_to(resolved_root)
        except (OSError, ValueError):
            continue
        if not resolved_candidate.is_file():
            continue
        media_type = mimetypes.guess_type(resolved_candidate.name)[0] or "application/octet-stream"
        headers = {"Cache-Control": "no-store", "Pragma": "no-cache", "Expires": "0"}
        return FileResponse(
            resolved_candidate,
            media_type=media_type,
            filename=resolved_candidate.name,
            headers=headers,
            content_disposition_type="inline",
        )

    # Optional stop-gap (OFF by default): return placeholder for missing default thumbnails.
    if _missing_thumb_placeholder_enabled() and _is_default_thumbnail_png(rel_asset.name):
        headers = {
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Expires": "0",
            "X-YTM-Placeholder": "1",
        }
        label = f"{channel_code}/{video_number}/{rel_asset.as_posix()}"
        return Response(content=_missing_thumb_png_bytes(label), media_type="image/png", headers=headers)

    raise HTTPException(status_code=404, detail="thumbnail asset not found")


@router.get("/thumbnails/library/{channel}/{asset_path:path}")
def get_thumbnail_library_asset(channel: str, asset_path: str):
    from backend import main as backend_main

    channel_code = channel.strip().upper()
    if not channel_code or Path(channel_code).name != channel_code:
        raise HTTPException(status_code=404, detail="invalid channel")
    _, candidate = backend_main._resolve_library_asset_path(channel_code, asset_path)
    media_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache", "Expires": "0"}
    return FileResponse(
        candidate,
        media_type=media_type,
        filename=candidate.name,
        headers=headers,
        content_disposition_type="inline",
    )
