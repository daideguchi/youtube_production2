from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Dict, List, Optional

from backend.app.normalize import normalize_planning_video_number
from factory_common.paths import planning_root as ssot_planning_root
from factory_common.paths import script_data_root as ssot_script_data_root
from factory_common.paths import script_pkg_root

DATA_ROOT = ssot_script_data_root()
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"
CHANNELS_DIR = script_pkg_root() / "channels"


def list_channel_dirs() -> List[Path]:
    if not DATA_ROOT.exists():
        return []
    return sorted(p for p in DATA_ROOT.iterdir() if p.is_dir() and p.name.upper().startswith("CH"))


def list_video_dirs(channel_code: str) -> List[Path]:
    channel_code = channel_code.upper()
    channel_dir = DATA_ROOT / channel_code
    if not channel_dir.exists():
        return []
    return sorted((p for p in channel_dir.iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name))


def list_planning_video_numbers(channel_code: str) -> List[str]:
    """
    Return normalized video numbers from Planning SoT (`workspaces/planning/channels/CHxx.csv`).

    Notes:
    - Non-numeric video numbers are ignored (best-effort: digit extraction).
    - This relies on `CHANNEL_PLANNING_DIR` (tests should monkeypatch this module, not `backend.main`).
    """

    channel_code = channel_code.upper()
    csv_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    if not csv_path.exists():
        return []

    numbers: List[str] = []
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                if not isinstance(row, dict):
                    continue
                token = normalize_planning_video_number(row.get("動画番号") or row.get("VideoNumber") or "")
                if token:
                    numbers.append(token)
    except Exception:
        return []

    seen: set[str] = set()
    unique: List[str] = []
    for number in numbers:
        if number in seen:
            continue
        seen.add(number)
        unique.append(number)
    return unique


def _channel_sort_key(code: str) -> tuple[int, str]:
    upper = code.upper()
    match = re.match(r"^CH(\d+)$", upper)
    if not match:
        return (10**9, upper)
    return (int(match.group(1)), upper)


def list_known_channel_codes(channel_info_map: Optional[Dict[str, dict]] = None) -> List[str]:
    """
    Return a stable list of known channel codes.

    UI should be able to show channels even when `workspaces/scripts/CHxx/` is missing.
    Sources (union):
    - `workspaces/planning/channels/CHxx.csv` (Planning SoT)
    - `packages/script_pipeline/channels/CHxx-*/` (channel profiles)
    - `workspaces/scripts/CHxx/` (existing script data)
    - `channel_info_map` keys (already loaded from channels_info.json / channel_info.json)
    """

    codes: set[str] = set()

    if channel_info_map:
        codes.update(code.upper() for code in channel_info_map.keys())

    if CHANNEL_PLANNING_DIR.exists():
        for csv_path in CHANNEL_PLANNING_DIR.glob("CH*.csv"):
            codes.add(csv_path.stem.upper())

    for channel_dir in list_channel_dirs():
        codes.add(channel_dir.name.upper())

    if CHANNELS_DIR.exists():
        for child in CHANNELS_DIR.iterdir():
            if not child.is_dir():
                continue
            code = child.name.split("-", 1)[0].upper()
            if code:
                codes.add(code)

    filtered = [code for code in codes if re.match(r"^CH\d+$", code)]
    return sorted(filtered, key=_channel_sort_key)
