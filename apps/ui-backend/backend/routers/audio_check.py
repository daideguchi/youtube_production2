from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from backend.app.channel_info_store import find_channel_directory, refresh_channel_info
from factory_common.paths import (
    audio_artifacts_root,
    audio_final_dir,
    planning_root as ssot_planning_root,
    script_data_root as ssot_script_data_root,
)

router = APIRouter(prefix="/api/audio-check", tags=["audio-check"])

DATA_ROOT = ssot_script_data_root()
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"


def normalize_channel_code(channel: str) -> str:
    raw = channel.strip()
    if not raw or Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    channel_code = raw.upper()
    if not re.match(r"^CH\\d+$", channel_code):
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    if (DATA_ROOT / channel_code).is_dir():
        return channel_code
    if (CHANNEL_PLANNING_DIR / f"{channel_code}.csv").is_file():
        return channel_code
    if find_channel_directory(channel_code) is not None:
        return channel_code
    # Fallback: allow channels known only via channels_info.json cache.
    if channel_code in refresh_channel_info():
        return channel_code
    raise HTTPException(status_code=404, detail=f"Channel {channel_code} not found")


def normalize_video_number(video: str) -> str:
    raw = video.strip()
    if not raw or Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="Invalid video identifier")
    if not raw.isdigit():
        raise HTTPException(status_code=400, detail="Video identifier must be numeric")
    return raw.zfill(3)


@router.get("/recent")
def list_recent_audio_checks(limit: int = 10) -> List[Dict[str, Any]]:
    """Find recently generated audio logs."""
    results: List[Dict[str, Any]] = []
    audio_root = audio_artifacts_root() / "final"
    if not audio_root.exists():
        return []

    # Search for log.json files in workspaces/audio/final/CHxx/xxx/log.json
    for channel_dir in audio_root.iterdir():
        if not channel_dir.is_dir() or not channel_dir.name.startswith("CH"):
            continue
        for video_dir in channel_dir.iterdir():
            if not video_dir.is_dir() or not video_dir.name.isdigit():
                continue

            log_path = video_dir / "log.json"
            if log_path.exists():
                try:
                    stat = log_path.stat()
                    # To be fast, just use mtime.
                    results.append(
                        {
                            "channel": channel_dir.name,
                            "video": video_dir.name,
                            "mtime": stat.st_mtime,
                            "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                        }
                    )
                except Exception:
                    continue

    # Sort by mtime desc
    results.sort(key=lambda x: x["mtime"], reverse=True)
    return results[:limit]


@router.get("/{channel_id}/{video_id}")
def get_audio_integrity_log(channel_id: str, video_id: str):
    """Retrieve audio integrity logs from log.json."""
    channel_code = normalize_channel_code(channel_id)
    video_no = normalize_video_number(video_id)
    log_path = audio_final_dir(channel_code, video_no) / "log.json"
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Audio log not found. Run TTS first.")

    try:
        with log_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse log.json: {e}")

