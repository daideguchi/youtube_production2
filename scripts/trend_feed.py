"""Utility helpers for thumbnail trend feed management."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from _bootstrap import bootstrap

BASE_DIR = bootstrap(load_env=False)
FEED_PATH = BASE_DIR / "data" / "trends" / "thumbnail_feed.json"
DEFAULT_SOURCES_PATH = BASE_DIR / "configs" / "trend_sources.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TrendEntry:
    id: str
    title: str
    source: str
    url: str
    image_url: str
    channel_hint: Optional[str] = None
    notes: Optional[str] = None
    picked: bool = False
    created_at: str = _now_iso()
    assignments: List[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        if payload["assignments"] is None:
            payload["assignments"] = []
        return payload


def ensure_data_dir() -> None:
    FEED_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_feed() -> Dict[str, Any]:
    ensure_data_dir()
    if not FEED_PATH.exists():
        return {"updated_at": _now_iso(), "items": []}
    with FEED_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_feed(feed: Dict[str, Any]) -> None:
    ensure_data_dir()
    feed["updated_at"] = _now_iso()
    with FEED_PATH.open("w", encoding="utf-8") as handle:
        json.dump(feed, handle, ensure_ascii=False, indent=2)


def _hash_id(*values: str) -> str:
    digest = hashlib.sha1("||".join(values).encode("utf-8")).hexdigest()
    return digest[:16]


def load_source_candidates() -> List[Dict[str, Any]]:
    if DEFAULT_SOURCES_PATH.exists():
        with DEFAULT_SOURCES_PATH.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, list):
                return data
    # fallback placeholders
    return [
        {
            "title": "Python Trends",
            "source": "github-explore",
            "url": "https://github.com/topics/python",
            "image_url": "https://raw.githubusercontent.com/github/explore/main/topics/python/python.png",
            "channel_hint": "CH06",
        },
        {
            "title": "Space Mystery",
            "source": "placeholder",
            "url": "https://example.com/articles/space-mystery",
            "image_url": "https://raw.githubusercontent.com/github/explore/main/topics/galaxy/galaxy.png",
            "channel_hint": "CH04",
        },
    ]


def upsert_entries(feed: Dict[str, Any], candidates: List[Dict[str, Any]]) -> int:
    items: List[Dict[str, Any]] = feed.setdefault("items", [])
    index = {item["id"]: item for item in items if isinstance(item, dict) and item.get("id")}
    inserted = 0
    for entry in candidates:
        image_url = entry.get("image_url")
        url = entry.get("url")
        title = entry.get("title") or image_url or "untitled"
        if not image_url or not url:
            continue
        identifier = _hash_id(image_url, url)
        if identifier in index:
            # update metadata
            existing = index[identifier]
            existing["title"] = title
            existing["source"] = entry.get("source") or existing.get("source")
            existing["channel_hint"] = entry.get("channel_hint") or existing.get("channel_hint")
            existing["notes"] = entry.get("notes") or existing.get("notes")
            continue
        trend = TrendEntry(
            id=identifier,
            title=title,
            source=entry.get("source") or "unknown",
            url=url,
            image_url=image_url,
            channel_hint=entry.get("channel_hint"),
            notes=entry.get("notes"),
        )
        payload = trend.to_dict()
        items.append(payload)
        index[identifier] = payload
        inserted += 1
    return inserted


def find_entry(feed: Dict[str, Any], entry_id: Optional[str], index: int = 0) -> Optional[Dict[str, Any]]:
    items: List[Dict[str, Any]] = feed.get("items") or []
    if entry_id:
        for item in items:
            if item.get("id") == entry_id:
                return item
        return None
    if not items:
        return None
    safe_index = max(0, min(index, len(items) - 1))
    return items[safe_index]
