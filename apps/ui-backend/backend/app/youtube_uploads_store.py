from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from backend.app.thumbnails_overview_models import ThumbnailChannelVideoResponse
from backend.app.ui_settings_store import _load_env_value
from backend.app.youtube_client import YouTubeDataClient
from factory_common.paths import script_data_root as ssot_script_data_root

logger = logging.getLogger("ui_backend")

DATA_ROOT = ssot_script_data_root()

YOUTUBE_UPLOAD_CACHE_DIR = DATA_ROOT / "_cache" / "youtube_uploads"
YOUTUBE_UPLOAD_CACHE_DIR.mkdir(parents=True, exist_ok=True)

YOUTUBE_UPLOAD_CACHE: Dict[str, tuple[datetime, list[ThumbnailChannelVideoResponse]]] = {}
YOUTUBE_UPLOAD_CACHE_TTL = timedelta(hours=float(os.getenv("YOUTUBE_UPLOAD_CACHE_TTL_HOURS", "6")))
YOUTUBE_UPLOAD_BACKOFF = timedelta(hours=float(os.getenv("YOUTUBE_UPLOAD_BACKOFF_HOURS", "12")))
YOUTUBE_UPLOADS_MAX_REFRESH_PER_REQUEST = int(os.getenv("YOUTUBE_UPLOADS_MAX_REFRESH_PER_REQUEST", "2"))
YOUTUBE_UPLOAD_FAILURE_STATE: Dict[str, datetime] = {}

YOUTUBE_BRANDING_TTL = timedelta(hours=float(os.getenv("YOUTUBE_BRANDING_TTL_HOURS", "24")))
YOUTUBE_BRANDING_BACKOFF: Dict[str, datetime] = {}

if not os.getenv("YOUTUBE_API_KEY"):
    _load_env_value("YOUTUBE_API_KEY")


def _parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_cached_uploads(channel_key: str) -> tuple[Optional[datetime], list[ThumbnailChannelVideoResponse]]:
    path = YOUTUBE_UPLOAD_CACHE_DIR / f"{channel_key}.json"
    if not path.exists():
        return None, []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - tolerate cache corruption
        logger.warning("Failed to read thumbnail cache for %s: %s", channel_key, exc)
        return None, []
    fetched_at = _parse_iso_datetime(payload.get("fetched_at"))
    videos_payload = payload.get("videos") or []
    videos: list[ThumbnailChannelVideoResponse] = []
    for item in videos_payload:
        try:
            videos.append(ThumbnailChannelVideoResponse.model_validate(item))
        except Exception:
            continue
    return fetched_at, videos


def _save_cached_uploads(channel_key: str, fetched_at: datetime, videos: list[ThumbnailChannelVideoResponse]):
    payload = {
        "fetched_at": fetched_at.replace(tzinfo=timezone.utc).isoformat(),
        "videos": [video.model_dump() for video in videos],
    }
    path = YOUTUBE_UPLOAD_CACHE_DIR / f"{channel_key}.json"
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:  # pragma: no cover - disk issues
        logger.warning("Failed to persist thumbnail cache for %s: %s", channel_key, exc)


YOUTUBE_CLIENT = YouTubeDataClient.from_env()
if YOUTUBE_CLIENT is None:
    logger.warning(
        "YOUTUBE_API_KEY が設定されていないため、YouTube Data API からのサムネイル取得をスキップします。"
        "ローカル案のプレビューにフォールバックします。"
    )

