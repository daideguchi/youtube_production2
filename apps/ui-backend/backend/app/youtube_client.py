"""YouTube Data API v3 client (API key based).

Real fetch that falls back gracefully when quotaやキー不足があってもUIを壊さない。
"""

from __future__ import annotations

import os
import re
import requests
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any


class YouTubeDataAPIError(Exception):
    pass


class ChannelMetadata:
    def __init__(
        self,
        *,
        channel_id: str,
        handle: Optional[str],
        url: str,
        title: str,
        avatar_url: str,
        banner_url: str,
        subscriber_count: int,
        view_count: int,
        video_count: int,
        updated_at: Optional[str],
    ):
        self.channel_id = channel_id
        self.handle = handle
        self.url = url
        self.title = title
        self.avatar_url = avatar_url
        self.banner_url = banner_url
        self.subscriber_count = subscriber_count
        self.view_count = view_count
        self.video_count = video_count
        self.updated_at = updated_at

    def to_branding_payload(self) -> dict:
        return {
            "avatar_url": self.avatar_url,
            "banner_url": self.banner_url,
            "title": self.title,
            "subscriber_count": self.subscriber_count,
            "view_count": self.view_count,
            "video_count": self.video_count,
            "custom_url": self.handle or "",
            "handle": self.handle or "",
            "url": self.url,
            "updated_at": self.updated_at,
        }

    def to_youtube_payload(self) -> dict:
        return {
            "channel_id": self.channel_id,
            "title": self.title,
            "custom_url": self.handle or "",
            "handle": self.handle or "",
            "url": self.url,
            "view_count": self.view_count,
            "subscriber_count": self.subscriber_count,
            "video_count": self.video_count,
            "synced_at": self.updated_at,
            "source": self.url or self.handle or self.channel_id,
        }


class YouTubeDataClient:
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("YOUTUBE_API_KEY") or os.getenv("GOOGLE_API_KEY")

    @classmethod
    def from_env(cls):
        return cls()

    @staticmethod
    def is_stale(synced_at: Optional[str]) -> bool:
        if not synced_at:
            return True
        try:
            dt = datetime.fromisoformat(synced_at.replace("Z", "+00:00"))
        except Exception:
            return True
        return datetime.now(timezone.utc) - dt > timedelta(days=7)

    def _require_key(self) -> str:
        if not self.api_key:
            raise YouTubeDataAPIError("YOUTUBE_API_KEY が設定されていません")
        return self.api_key

    def _get(self, endpoint: str, params: Dict[str, Any]) -> dict:
        key = self._require_key()
        base = f"https://www.googleapis.com/youtube/v3/{endpoint}"
        merged = dict(params)
        merged["key"] = key
        try:
            resp = requests.get(base, params=merged, timeout=15)
        except requests.RequestException as exc:
            raise YouTubeDataAPIError(f"通信エラー: {exc}") from exc
        if resp.status_code == 403:
            raise YouTubeDataAPIError("YouTube Data API quota exceeded or forbidden")
        if resp.status_code == 400:
            raise YouTubeDataAPIError(f"Bad request: {resp.text}")
        if not resp.ok:
            raise YouTubeDataAPIError(f"HTTP {resp.status_code}: {resp.text}")
        try:
            return resp.json()
        except ValueError as exc:
            raise YouTubeDataAPIError(f"Invalid JSON: {exc}") from exc

    def _search_channel_id(self, query: str) -> Optional[str]:
        data = self._get(
            "search",
            {
                "part": "id",
                "type": "channel",
                "q": query,
                "maxResults": 1,
            },
        )
        items = data.get("items") or []
        if not items:
            return None
        return items[0].get("id", {}).get("channelId")

    def _parse_identifier(self, identifier: str) -> Dict[str, Optional[str]]:
        ident = (identifier or "").strip()
        channel_id = None
        handle = None
        url = None

        if ident.startswith("http"):
            url = ident
            if "/channel/" in ident:
                channel_id = ident.split("/channel/", 1)[1].split("/")[0]
            if "/@" in ident or "youtube.com/@" in ident:
                try:
                    handle = "@" + ident.split("@", 1)[1].split("/", 1)[0]
                except Exception:
                    handle = None
        elif ident.startswith("@"):
            handle = ident
            url = f"https://www.youtube.com/{ident}"
        elif ident.startswith("UC"):
            channel_id = ident
            url = f"https://www.youtube.com/channel/{ident}"
        else:
            handle = ident if ident else None
            if handle:
                url = f"https://www.youtube.com/@{handle.lstrip('@')}"
                handle = "@" + handle.lstrip("@")
        return {"channel_id": channel_id, "handle": handle, "url": url}

    def fetch_channel(self, identifier: str) -> ChannelMetadata:
        parsed = self._parse_identifier(identifier)
        channel_id = parsed["channel_id"]
        handle = parsed["handle"]
        url = parsed["url"]

        if not channel_id and handle:
            channel_id = self._search_channel_id(handle)
        if not channel_id and handle:
            channel_id = self._search_channel_id(handle.lstrip("@"))
        if not channel_id and not handle:
            channel_id = self._search_channel_id(identifier)

        if not channel_id:
            raise YouTubeDataAPIError("チャンネルIDを特定できませんでした")

        data = self._get(
            "channels",
            {
                "part": "snippet,statistics,brandingSettings",
                "id": channel_id,
                "maxResults": 1,
            },
        )
        items = data.get("items") or []
        if not items:
            raise YouTubeDataAPIError("チャンネルが見つかりませんでした")
        item = items[0]
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        branding = (item.get("brandingSettings") or {}).get("image") or {}

        thumb = (snippet.get("thumbnails") or {}).get("default") or {}
        avatar_url = thumb.get("url") or "https://placehold.co/128x128/1f2933/ffffff?text=YT"
        banner_url = branding.get("bannerExternalUrl") or ""
        handle_from_api = snippet.get("customUrl")
        title = snippet.get("title") or handle or channel_id
        updated_at = snippet.get("publishedAt")

        return ChannelMetadata(
            channel_id=channel_id,
            handle=handle_from_api or handle,
            url=url or f"https://www.youtube.com/channel/{channel_id}",
            title=title,
            avatar_url=avatar_url,
            banner_url=banner_url,
            subscriber_count=int(stats.get("subscriberCount") or 0),
            view_count=int(stats.get("viewCount") or 0),
            video_count=int(stats.get("videoCount") or 0),
            updated_at=updated_at,
        )

    def fetch_recent_uploads(self, channel_id: str, max_results: int = 6) -> List[dict]:
        data = self._get(
            "search",
            {
                "part": "snippet",
                "channelId": channel_id,
                "order": "date",
                "type": "video",
                "maxResults": max(1, min(max_results, 20)),
            },
        )
        items = data.get("items") or []
        out: List[dict] = []
        for item in items:
            vid = item.get("id", {}).get("videoId")
            sn = item.get("snippet") or {}
            if not vid:
                continue
            out.append(
                {
                    "video_id": vid,
                    "title": sn.get("title"),
                    "published_at": sn.get("publishedAt"),
                    "thumbnail": (sn.get("thumbnails") or {}).get("default", {}).get("url"),
                }
            )
        return out

    def get_video_metadata(self, video_id: str) -> dict:
        data = self._get(
            "videos",
            {
                "part": "snippet,statistics,contentDetails",
                "id": video_id,
            },
        )
        items = data.get("items") or []
        if not items:
            return {}
        item = items[0]
        snippet = item.get("snippet") or {}
        stats = item.get("statistics") or {}
        content = item.get("contentDetails") or {}
        return {
            "video_id": video_id,
            "title": snippet.get("title"),
            "description": snippet.get("description"),
            "published_at": snippet.get("publishedAt"),
            "duration": content.get("duration"),
            "view_count": int(stats.get("viewCount") or 0),
            "like_count": int(stats.get("likeCount") or 0),
            "comment_count": int(stats.get("commentCount") or 0),
        }
