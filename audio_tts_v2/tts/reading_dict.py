from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import yaml


READING_DICT_ROOT = Path(__file__).resolve().parents[1] / "data" / "reading_dict"


@dataclass
class ReadingEntry:
    surface: str
    reading_hira: str
    reading_kana: str
    accent_moras: Optional[list[str]] = None
    source: str = "manual"
    last_updated: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "reading_hira": self.reading_hira,
            "reading_kana": self.reading_kana,
            "accent_moras": self.accent_moras,
            "source": self.source,
            "last_updated": self.last_updated,
        }


def _load_yaml(path: Path) -> Dict[str, Dict[str, object]]:
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}
    return {}


def load_channel_reading_dict(channel: str) -> Dict[str, Dict[str, object]]:
    """Load (surface -> entry) mapping for the channel.

    The dictionary is stored as YAML for easy manual edits. Missing files return an
    empty mapping.
    """

    path = READING_DICT_ROOT / f"{channel}.yaml"
    return _load_yaml(path)


def _ensure_root() -> None:
    READING_DICT_ROOT.mkdir(parents=True, exist_ok=True)


def save_channel_reading_dict(channel: str, data: Dict[str, Dict[str, object]]) -> None:
    _ensure_root()
    path = READING_DICT_ROOT / f"{channel}.yaml"
    serialized = {str(k): v for k, v in data.items()}
    path.write_text(yaml.safe_dump(serialized, allow_unicode=True, sort_keys=True), encoding="utf-8")


def merge_channel_readings(channel: str, updates: Dict[str, ReadingEntry]) -> Dict[str, Dict[str, object]]:
    """Merge new readings into the channel dictionary and persist.

    Returns the merged mapping for further reuse.
    """

    current = load_channel_reading_dict(channel)
    now = datetime.now(timezone.utc).isoformat()

    for surface, entry in updates.items():
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
        reading = meta.get("reading_kana") or meta.get("reading_hira")
        if isinstance(reading, str) and reading:
            out[surface] = reading
    return out


def describe_dict_state(channel: str) -> Dict[str, object]:
    """Small helper for logging/debugging state."""

    path = READING_DICT_ROOT / f"{channel}.yaml"
    data = load_channel_reading_dict(channel)
    return {
        "path": str(path),
        "entries": len(data),
    }
