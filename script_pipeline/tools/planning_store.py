from __future__ import annotations
import csv
from pathlib import Path
from typing import List, Dict, Iterable, Optional, Set
from dataclasses import dataclass

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CHANNELS_DIR = PROJECT_ROOT / "progress" / "channels"

@dataclass
class PlanningRow:
    raw: Dict[str, str]
    channel_code: str
    script_id: Optional[str] = None
    video_number: Optional[str] = None


def refresh(force: bool = False) -> None:
    # no-op refresh; CSVは都度読み込み
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
    path = CHANNELS_DIR / f"{channel_code.upper()}.csv"
    if not path.exists():
        return []
    return _load_csv(path, channel_code.upper())


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
