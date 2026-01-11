from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.core.tools import thumbnails_lookup as thumbnails_lookup_tools
from backend.routers.ssot_docs import normalize_channel_code

router = APIRouter(prefix="/api/thumbnails", tags=["thumbnails"])


def _normalize_video_number(video: str) -> str:
    raw = video.strip()
    if not raw or Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="Invalid video identifier")
    if not raw.isdigit():
        raise HTTPException(status_code=400, detail="Video identifier must be numeric")
    return raw.zfill(3)


@router.get("/lookup")
def thumbnail_lookup(
    channel: str = Query(..., description="CHコード (例: CH02)"),
    video: Optional[str] = Query(None, description="動画番号 (例: 019)"),
    title: Optional[str] = Query(None, description="動画タイトル（任意）"),
    limit: int = Query(3, description="返す件数"),
):
    channel_code = normalize_channel_code(channel)
    video_no = _normalize_video_number(video) if video else None
    thumbs = thumbnails_lookup_tools.find_thumbnails(channel_code, video_no, title, limit=limit)
    return {"items": thumbs}

