from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["thumbnails"])


@router.get("/thumbnails/assets/{channel}/{video}/{asset_path:path}")
def get_thumbnail_asset(channel: str, video: str, asset_path: str):
    from backend import main as backend_main

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

