from __future__ import annotations

"""
Thumbnail templates document store helpers (templates.json).

created: 2026-01-14
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from factory_common.paths import thumbnails_root as ssot_thumbnails_root

LOGGER_NAME = "ui_backend"
logger = logging.getLogger(LOGGER_NAME)

THUMBNAIL_TEMPLATES_CANDIDATES = [
    ssot_thumbnails_root() / "templates.json",
]

THUMBNAIL_TEMPLATES_LOCK = threading.Lock()


def _resolve_thumbnail_templates_path() -> Path:
    for candidate in THUMBNAIL_TEMPLATES_CANDIDATES:
        if candidate.exists():
            return candidate
    return THUMBNAIL_TEMPLATES_CANDIDATES[0]


def _load_thumbnail_templates_document() -> tuple[Path, dict]:
    path = _resolve_thumbnail_templates_path()
    payload: dict
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse %s: %s. Recreating file.", path, exc)
            payload = {}
    else:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("version", 1)
    channels = payload.get("channels")
    if not isinstance(channels, dict):
        channels = {}
    payload["channels"] = channels
    return path, payload


def _write_thumbnail_templates_document(path: Path, payload: dict) -> None:
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

