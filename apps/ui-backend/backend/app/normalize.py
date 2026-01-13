from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException

from backend.app.channel_info_store import find_channel_directory, refresh_channel_info
from factory_common.paths import planning_root as ssot_planning_root
from factory_common.paths import script_data_root as ssot_script_data_root

DATA_ROOT = ssot_script_data_root()
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"


def normalize_channel_code(channel: str) -> str:
    raw = channel.strip()
    if not raw or Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    channel_code = raw.upper()
    if not re.match(r"^CH\d+$", channel_code):
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
