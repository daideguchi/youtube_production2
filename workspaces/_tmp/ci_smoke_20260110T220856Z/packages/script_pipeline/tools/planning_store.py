from __future__ import annotations

import csv
import threading
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set, Tuple
from dataclasses import dataclass

from factory_common.paths import planning_root

CHANNELS_DIR = planning_root() / "channels"

_ROWS_CACHE: Dict[str, Tuple[int, int, List["PlanningRow"]]] = {}
_CACHE_LOCK = threading.Lock()

@dataclass
class PlanningRow:
    raw: Dict[str, str]
    channel_code: str
    script_id: Optional[str] = None
    video_number: Optional[str] = None


def refresh(force: bool = False) -> None:
    if not force:
        return None
    with _CACHE_LOCK:
        _ROWS_CACHE.clear()
    return None


def list_channels() -> Iterable[str]:
    if not CHANNELS_DIR.exists():
        return []
    return [p.stem.upper() for p in CHANNELS_DIR.glob("*.csv") if p.is_file()]


def _load_csv(path: Path, channel_code: str) -> List[PlanningRow]:
    try:
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            rows: List[PlanningRow] = []
            for row in reader:
                script_id = row.get("動画ID") or row.get("ScriptID") or ""
                video_num = row.get("動画番号") or row.get("VideoNumber") or ""
                rows.append(PlanningRow(raw=row, channel_code=channel_code, script_id=script_id, video_number=video_num))
            return rows
    except Exception:
        return []


def get_rows(channel_code: str, force_refresh: bool = False) -> List[PlanningRow]:
    code = str(channel_code or "").strip().upper()
    path = CHANNELS_DIR / f"{code}.csv"
    if not path.exists():
        with _CACHE_LOCK:
            _ROWS_CACHE.pop(code, None)
        return []

    try:
        st = path.stat()
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
        size = int(st.st_size)
    except Exception:
        mtime_ns = -1
        size = -1

    if not force_refresh and mtime_ns >= 0 and size >= 0:
        with _CACHE_LOCK:
            cached = _ROWS_CACHE.get(code)
        if cached and cached[0] == mtime_ns and cached[1] == size:
            return cached[2]

    rows = _load_csv(path, code)
    if mtime_ns >= 0 and size >= 0:
        with _CACHE_LOCK:
            _ROWS_CACHE[code] = (mtime_ns, size, rows)
    return rows


def get_fieldnames() -> List[str]:
    fieldnames: Set[str] = set()
    if CHANNELS_DIR.exists():
        for csv_path in CHANNELS_DIR.glob("*.csv"):
            try:
                with csv_path.open(encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames:
                        fieldnames.update(reader.fieldnames)
            except Exception:
                continue
    return sorted(fieldnames)
