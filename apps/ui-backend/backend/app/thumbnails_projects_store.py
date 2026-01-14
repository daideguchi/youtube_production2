from __future__ import annotations

"""
Thumbnail projects document store helpers (projects.json).

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

THUMBNAIL_PROJECTS_CANDIDATES = [
    ssot_thumbnails_root() / "projects.json",
]

THUMBNAIL_PROJECTS_LOCK = threading.Lock()


def _resolve_thumbnail_projects_path() -> Path:
    for candidate in THUMBNAIL_PROJECTS_CANDIDATES:
        if candidate.exists():
            return candidate
    return THUMBNAIL_PROJECTS_CANDIDATES[0]


def _load_thumbnail_projects_document() -> tuple[Path, dict]:
    path = _resolve_thumbnail_projects_path()
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
    projects = payload.get("projects")
    if not isinstance(projects, list):
        projects = []
    payload["projects"] = projects
    return path, payload


def _write_thumbnail_projects_document(path: Path, payload: dict) -> None:
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

