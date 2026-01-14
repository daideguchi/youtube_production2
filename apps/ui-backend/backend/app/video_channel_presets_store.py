from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from factory_common.paths import video_pkg_root

logger = logging.getLogger("ui_backend")

VIDEO_CHANNEL_PRESETS_PATH = video_pkg_root() / "config" / "channel_presets.json"
VIDEO_CHANNEL_PRESETS_LOCK = threading.Lock()


def _load_video_channel_presets_document() -> tuple[Path, dict]:
    path = VIDEO_CHANNEL_PRESETS_PATH
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
    channels = payload.get("channels")
    if not isinstance(channels, dict):
        channels = {}
    payload["channels"] = channels
    return path, payload


def _write_video_channel_presets_document(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)

