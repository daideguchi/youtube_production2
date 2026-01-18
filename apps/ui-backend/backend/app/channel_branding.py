from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException

from backend.app.channel_info_store import refresh_channel_info
from backend.app.youtube_client import YouTubeDataClient, YouTubeDataAPIError
from backend.app.youtube_uploads_store import (
    YOUTUBE_BRANDING_BACKOFF,
    YOUTUBE_CLIENT,
    YOUTUBE_UPLOAD_BACKOFF,
)
from backend.core.tools.channel_stats_store import merge_channel_stats_into_channel_info, write_channel_stats

logger = logging.getLogger(__name__)

ESSENTIAL_BRANDING_KEYS = ("avatar_url", "subscriber_count", "view_count", "video_count")


def _has_essential_branding(info: dict) -> bool:
    branding = info.get("branding") or {}
    for key in ESSENTIAL_BRANDING_KEYS:
        if branding.get(key) in (None, ""):
            return False
    return True


def ensure_channel_branding(
    channel_code: str,
    info: dict,
    *,
    force_refresh: bool = False,
    ignore_backoff: bool = False,
    strict: bool = False,
) -> Optional[dict]:
    if YOUTUBE_CLIENT is None:
        if strict:
            raise HTTPException(status_code=503, detail="YouTube Data API が無効化されています")
        return info.get("branding")

    if not force_refresh:
        return info.get("branding")

    refresh_channel_info()

    youtube_info = info.get("youtube") or {}
    identifier = (
        youtube_info.get("channel_id")
        or youtube_info.get("handle")
        or youtube_info.get("custom_url")
        or youtube_info.get("url")
        or youtube_info.get("source")
        or info.get("youtube_url")
        or info.get("youtube_handle")
        or (info.get("branding") or {}).get("handle")
        or (info.get("branding") or {}).get("custom_url")
    )

    if not identifier:
        if strict:
            raise HTTPException(status_code=400, detail="channel_info に YouTube チャンネルID/URL が登録されていません")
        return info.get("branding")

    branding = info.get("branding") or {}
    now = datetime.now(timezone.utc)
    backoff_until = YOUTUBE_BRANDING_BACKOFF.get(channel_code)
    if backoff_until and backoff_until > now and not ignore_backoff:
        if strict:
            raise HTTPException(
                status_code=429,
                detail=f"YouTube API は {backoff_until.isoformat()} まで一時停止中です",
            )
        return branding or None
    if backoff_until and (ignore_backoff or backoff_until <= now):
        YOUTUBE_BRANDING_BACKOFF.pop(channel_code, None)

    try:
        metadata = YOUTUBE_CLIENT.fetch_channel(identifier)
    except YouTubeDataAPIError as exc:  # pragma: no cover - API error logging only
        logger.warning("YouTube metadata fetch failed for %s: %s", channel_code, exc)
        message = str(exc).lower()
        if "quota" in message or "useratelimitexceeded" in message:
            YOUTUBE_BRANDING_BACKOFF[channel_code] = now + YOUTUBE_UPLOAD_BACKOFF
        if strict:
            raise HTTPException(status_code=502, detail=f"YouTube API error: {exc}") from exc
        return branding or None
    except Exception as exc:  # pragma: no cover - network failure handled gracefully
        logger.warning("Unexpected error during YouTube fetch for %s: %s", channel_code, exc)
        if strict:
            raise HTTPException(status_code=502, detail=f"Unexpected YouTube error: {exc}") from exc
        return branding or None

    branding_payload = metadata.to_branding_payload()
    if branding.get("theme_color"):
        branding_payload["theme_color"] = branding.get("theme_color")

    info["branding"] = branding_payload
    info.setdefault("youtube", {})
    youtube_payload = metadata.to_youtube_payload()
    youtube_payload["source"] = identifier
    now_iso = datetime.now(timezone.utc).isoformat()
    youtube_payload["synced_at"] = now_iso
    info["youtube"].update(youtube_payload)
    YOUTUBE_BRANDING_BACKOFF.pop(channel_code, None)

    write_channel_stats(
        channel_code,
        {
            "channel_id": channel_code.upper(),
            "synced_at": now_iso,
            "branding": branding_payload,
            "youtube": dict(info.get("youtube") or {}),
        },
    )
    return branding_payload


def ensure_youtube_metrics(channel_code: str, info: dict) -> dict:
    if YOUTUBE_CLIENT is None:
        return info
    info = merge_channel_stats_into_channel_info(channel_code, info)
    branding = info.get("branding") or {}
    youtube_payload = info.get("youtube") or {}
    synced_at = youtube_payload.get("synced_at") or branding.get("updated_at") or info.get("synced_at")
    needs_refresh = not _has_essential_branding(info) or YouTubeDataClient.is_stale(synced_at)
    if not needs_refresh:
        return info
    try:
        ensure_channel_branding(channel_code, info, force_refresh=True, ignore_backoff=False, strict=False)
    except HTTPException:
        return info
    return merge_channel_stats_into_channel_info(channel_code, info)

