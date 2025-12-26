from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from factory_common.paths import audio_pkg_root

# yaml is optional; dictionaries are best-effort when missing.
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


READING_DICT_ROOT = audio_pkg_root() / "data" / "reading_dict"
AMBIGUOUS_SURFACES = {
    # 文脈によって読みが揺れるため、辞書登録禁止
    "今日",
    "昨日",
    "明日",
    "今",
    "今年",
    "来年",
    "去年",
    "一行",
}

KANJI_RE = re.compile(r"[\u4E00-\u9FFF]")
_HIRA_TO_KATAKANA = str.maketrans(
    {chr(h): chr(h + 0x60) for h in range(ord("ぁ"), ord("ゔ") + 1)}
)


def normalize_reading_kana(reading: str) -> str:
    """Normalize reading into Katakana when possible."""
    return str(reading or "").strip().translate(_HIRA_TO_KATAKANA)


def is_safe_reading(reading: str) -> bool:
    """Return True when reading does not contain Kanji characters."""
    reading = normalize_reading_kana(reading)
    if not reading:
        return False
    return KANJI_RE.search(reading) is None


def is_banned_surface(surface: str) -> bool:
    """Return True when the surface should NOT be cached in dictionaries."""
    if not surface:
        return True
    if len(surface) <= 1:
        return True
    return surface in AMBIGUOUS_SURFACES


@dataclass
class ReadingEntry:
    surface: str
    reading_hira: str
    reading_kana: str
    voicevox_kana: Optional[str] = None
    accent_moras: Optional[list[str]] = None
    source: str = "manual"
    last_updated: str = ""

    def to_dict(self) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "reading_hira": self.reading_hira,
            "reading_kana": self.reading_kana,
            "accent_moras": self.accent_moras,
            "source": self.source,
            "last_updated": self.last_updated,
        }
        if self.voicevox_kana:
            payload["voicevox_kana"] = self.voicevox_kana
        return payload


def _load_yaml(path: Path) -> Dict[str, Dict[str, object]]:
    if not path.exists():
        return {}
    if yaml is None:
        # Without PyYAML we cannot parse; treat as empty mapping.
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}
    return {}


def _filter_entries(data: Dict[str, Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    """Drop entries that are unsafe to cache (ambiguous or 1-char)."""
    cleaned: Dict[str, Dict[str, object]] = {}
    for surface, meta in data.items():
        if is_banned_surface(surface):
            continue
        cleaned[surface] = meta
    return cleaned


def load_channel_reading_dict(channel: str) -> Dict[str, Dict[str, object]]:
    """Load (surface -> entry) mapping for the channel.

    The dictionary is stored as YAML for easy manual edits. Missing files return an
    empty mapping.
    """

    path = READING_DICT_ROOT / f"{channel}.yaml"
    return _filter_entries(_load_yaml(path))


def _ensure_root() -> None:
    READING_DICT_ROOT.mkdir(parents=True, exist_ok=True)


def save_channel_reading_dict(channel: str, data: Dict[str, Dict[str, object]]) -> None:
    _ensure_root()
    path = READING_DICT_ROOT / f"{channel}.yaml"
    serialized = _filter_entries({str(k): v for k, v in data.items()})
    if yaml is None:
        raise RuntimeError("PyYAML is required to save reading_dict.yaml")
    path.write_text(yaml.safe_dump(serialized, allow_unicode=True, sort_keys=True), encoding="utf-8")


def merge_channel_readings(channel: str, updates: Dict[str, ReadingEntry]) -> Dict[str, Dict[str, object]]:
    """Merge new readings into the channel dictionary and persist.

    Returns the merged mapping for further reuse.
    """

    current = load_channel_reading_dict(channel)
    now = datetime.now(timezone.utc).isoformat()

    for surface, entry in updates.items():
        if is_banned_surface(surface):
            # 落とす（文脈で読みが揺れるものや1文字は登録しない）
            continue
        payload = entry.to_dict()
        payload["last_updated"] = now
        current[surface] = payload

    save_channel_reading_dict(channel, current)
    return current


def export_words_for_word_dict(data: Dict[str, Dict[str, object]]) -> Dict[str, str]:
    """Convert reading_dict payload to a simple surface->reading map.

    WordDictionary expects plain readings; this helper keeps compatibility with
    the existing arbiter logic.
    """

    out: Dict[str, str] = {}
    for surface, meta in data.items():
        if is_banned_surface(surface):
            continue
        reading = meta.get("reading_kana") or meta.get("reading_hira")
        if isinstance(reading, str) and reading:
            normalized = normalize_reading_kana(reading)
            if not is_safe_reading(normalized):
                continue
            if normalized == surface:
                continue
            out[surface] = normalized
    return out


def describe_dict_state(channel: str) -> Dict[str, object]:
    """Small helper for logging/debugging state."""

    path = READING_DICT_ROOT / f"{channel}.yaml"
    data = load_channel_reading_dict(channel)
    return {
        "path": str(path),
        "entries": len(data),
    }
