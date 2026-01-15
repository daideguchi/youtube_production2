from __future__ import annotations

"""
Channel info store/cache (UI backend internal).

SoT (static): packages/script_pipeline/channels/**/channel_info.json
SoT (dynamic metrics): workspaces/channels/<CH>/channel_stats.json  (D-012)

created: 2026-01-09
"""

import json
import logging
import threading
from pathlib import Path
from typing import Dict, Iterable, Optional

from backend.core.tools.channel_stats_store import merge_channel_stats_into_channel_info
from backend.app.lock_store import write_text_with_lock
from factory_common.paths import script_pkg_root

logger = logging.getLogger(__name__)

CHANNELS_DIR = script_pkg_root() / "channels"
CHANNEL_INFO_PATH = CHANNELS_DIR / "channels_info.json"

CHANNEL_INFO_LOCK = threading.Lock()
CHANNEL_INFO: Dict[str, dict] = {}
CHANNEL_INFO_MTIME = 0.0


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def rebuild_channel_catalog() -> None:
    entries: list[dict] = []
    if CHANNELS_DIR.exists():
        for entry in sorted(CHANNELS_DIR.iterdir()):
            if not entry.is_dir():
                continue
            info_path = entry / "channel_info.json"
            if not info_path.exists():
                continue
            try:
                data = json.loads(info_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                logger.warning("channel_info.json の解析に失敗しました: %s", info_path)
                continue
            if isinstance(data, dict):
                entries.append(data)
    write_text_with_lock(
        CHANNEL_INFO_PATH,
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
    )


def _merge_channel_payload(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if value is None:
            continue
        if isinstance(value, dict):
            base_value = merged.get(key)
            if isinstance(base_value, dict):
                combined = dict(base_value)
                combined.update(value)
            else:
                combined = dict(value)
            merged[key] = combined
        else:
            merged[key] = value
    channel_id = merged.get("channel_id")
    if isinstance(channel_id, str):
        merged["channel_id"] = channel_id.upper()
    merged.setdefault("branding", {})
    merged.setdefault("youtube", {})
    return merged


def load_channel_info() -> Dict[str, dict]:
    mapping: Dict[str, dict] = {}
    items: Iterable[dict] = []
    if CHANNEL_INFO_PATH.exists():
        try:
            raw_items = _load_json(CHANNEL_INFO_PATH)
            if isinstance(raw_items, list):
                items = raw_items
            elif isinstance(raw_items, dict):
                items = raw_items.values()
            else:
                items = []
        except Exception as exc:  # pragma: no cover - log but continue
            logger.warning("Failed to load %s: %s", CHANNEL_INFO_PATH, exc)
            items = []
    for entry in items:
        code = entry.get("channel_id")
        if not code:
            continue
        mapping[code.upper()] = _merge_channel_payload({"channel_id": code.upper()}, entry)

    if CHANNELS_DIR.exists():
        for child in CHANNELS_DIR.iterdir():
            if not child.is_dir():
                continue
            info_path = child / "channel_info.json"
            if not info_path.exists():
                continue
            try:
                entry = _load_json(info_path)
            except Exception as exc:  # pragma: no cover - corrupted channel file
                logger.warning("Failed to parse %s: %s", info_path, exc)
                continue
            channel_code = entry.get("channel_id")
            if not channel_code:
                parts = child.name.split("-", 1)
                channel_code = parts[0].upper() if parts else None
            if not channel_code:
                continue
            existing = mapping.get(channel_code.upper(), {"channel_id": channel_code.upper()})
            mapping[channel_code.upper()] = _merge_channel_payload(existing, entry)
    for code, info in list(mapping.items()):
        mapping[code] = merge_channel_stats_into_channel_info(code, info)
    return mapping


def refresh_channel_info(force: bool = False) -> Dict[str, dict]:
    global CHANNEL_INFO, CHANNEL_INFO_MTIME
    mtime = 0.0
    try:
        mtime = max(mtime, CHANNEL_INFO_PATH.stat().st_mtime)
    except FileNotFoundError:
        pass

    # channels_info.json は“カタログ”。正本は各 CHxx-*/channel_info.json なので、
    # 個別ファイルの更新もキャッシュ更新トリガに含める。
    if CHANNELS_DIR.exists():
        for child in CHANNELS_DIR.iterdir():
            if not child.is_dir():
                continue
            info_path = child / "channel_info.json"
            if not info_path.exists():
                continue
            try:
                mtime = max(mtime, info_path.stat().st_mtime)
            except OSError:
                continue
    if force or not CHANNEL_INFO or mtime > CHANNEL_INFO_MTIME:
        CHANNEL_INFO = load_channel_info()
        CHANNEL_INFO_MTIME = mtime
    return CHANNEL_INFO


def find_channel_directory(channel_code: str) -> Optional[Path]:
    upper = channel_code.upper()
    if not CHANNELS_DIR.exists():
        return None
    for candidate in CHANNELS_DIR.iterdir():
        if candidate.is_dir() and candidate.name.upper().startswith(f"{upper}-"):
            return candidate
    return None


def resolve_channel_title(channel_code: str, info_map: Dict[str, dict]) -> Optional[str]:
    info = info_map.get(channel_code)
    if not isinstance(info, dict):
        return None
    branding = info.get("branding")
    if isinstance(branding, dict):
        title = branding.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    name = info.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    youtube_meta = info.get("youtube")
    if isinstance(youtube_meta, dict):
        yt_title = youtube_meta.get("title")
        if isinstance(yt_title, str) and yt_title.strip():
            return yt_title.strip()
    return None


def infer_channel_genre(info: dict) -> Optional[str]:
    genre = info.get("genre")
    if isinstance(genre, str) and genre.strip():
        return genre.strip()

    metadata = info.get("metadata")
    if isinstance(metadata, dict):
        meta_genre = metadata.get("genre")
        if isinstance(meta_genre, str) and meta_genre.strip():
            return meta_genre.strip()

    name = info.get("name")
    if isinstance(name, str):
        for separator in ("　", " "):
            if separator in name:
                candidate = name.split(separator, 1)[0].strip()
                if candidate:
                    return candidate
        stripped = name.strip()
        if stripped:
            return stripped

    return None
