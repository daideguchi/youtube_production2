from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, HTTPException

from backend.app.normalize import normalize_channel_code, normalize_video_number
from factory_common.paths import (
    audio_artifacts_root,
    audio_final_dir,
)

router = APIRouter(prefix="/api/audio-check", tags=["audio-check"])


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
