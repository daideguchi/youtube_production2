from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from factory_common.paths import workspace_root


def channels_stats_root() -> Path:
    return workspace_root() / "channels"


def channel_stats_path(channel_code: str) -> Path:
    code = str(channel_code or "").strip().upper()
    if not code:
        raise ValueError("channel_code is empty")
    return channels_stats_root() / code / "channel_stats.json"


def read_channel_stats(channel_code: str) -> Optional[Dict[str, Any]]:
    path = channel_stats_path(channel_code)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def write_channel_stats(channel_code: str, payload: Dict[str, Any]) -> Path:
    path = channel_stats_path(channel_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized: Dict[str, Any] = dict(payload) if isinstance(payload, dict) else {}
    normalized.setdefault("channel_id", str(channel_code).upper())
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def merge_channel_stats_into_channel_info(channel_code: str, info: Dict[str, Any]) -> Dict[str, Any]:
    stats = read_channel_stats(channel_code)
    if not stats:
        return info

    out: Dict[str, Any] = dict(info) if isinstance(info, dict) else {"channel_id": str(channel_code).upper()}

    branding = out.get("branding")
    branding_dict: Dict[str, Any] = dict(branding) if isinstance(branding, dict) else {}
    youtube = out.get("youtube")
    youtube_dict: Dict[str, Any] = dict(youtube) if isinstance(youtube, dict) else {}

    stats_branding = stats.get("branding")
    if isinstance(stats_branding, dict):
        branding_dict.update(stats_branding)
    stats_youtube = stats.get("youtube")
    if isinstance(stats_youtube, dict):
        youtube_dict.update(stats_youtube)

    out["branding"] = branding_dict
    out["youtube"] = youtube_dict

    synced_at = stats.get("synced_at")
    if isinstance(synced_at, str) and synced_at.strip():
        out["synced_at"] = synced_at.strip()

    return out
