from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, HTTPException, Query

from backend.app.normalize import normalize_channel_code
from backend.app.redo_models import RedoItemResponse, RedoSummaryItem
from factory_common.paths import planning_root as ssot_planning_root
from factory_common.paths import repo_root
from factory_common.paths import script_data_root as ssot_script_data_root

router = APIRouter(prefix="/api/redo", tags=["redo"])

PROJECT_ROOT = repo_root()
DATA_ROOT = ssot_script_data_root()
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"


def _channel_sort_key(code: str) -> tuple[int, str]:
    upper = code.upper()
    match = re.match(r"^CH(\d+)$", upper)
    if not match:
        return (10**9, upper)
    return (int(match.group(1)), upper)


def list_channel_dirs() -> List[Path]:
    if not DATA_ROOT.exists():
        return []
    return sorted(p for p in DATA_ROOT.iterdir() if p.is_dir() and p.name.upper().startswith("CH"))


def list_video_dirs(channel_code: str) -> List[Path]:
    channel_dir = DATA_ROOT / channel_code
    if not channel_dir.exists():
        return []
    return sorted((p for p in channel_dir.iterdir() if p.is_dir() and p.name.isdigit()), key=lambda p: int(p.name))


def normalize_planning_video_number(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    return digits.zfill(3)


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid JSON: {path}") from exc


def load_status(channel_code: str, video_number: str) -> dict:
    status_path = DATA_ROOT / channel_code / video_number / "status.json"
    if not status_path.exists():
        raise HTTPException(status_code=404, detail="status.json not found")
    return _load_json(status_path)


def load_status_optional(channel_code: str, video_number: str) -> Optional[dict]:
    status_path = DATA_ROOT / channel_code / video_number / "status.json"
    if not status_path.exists():
        return None
    return _load_json(status_path)


@router.get("", response_model=List[RedoItemResponse])
def list_redo_items(
    channel: Optional[str] = Query(None, description="CHコード (例: CH02)"),
    type: Optional[str] = Query(None, description="script|audio|all で絞り込み"),
):
    channel_filter = normalize_channel_code(channel) if channel else None
    type_filter = (type or "").lower()
    want_script = type_filter in ("script", "all", "") or type_filter not in ("audio", "script")
    want_audio = type_filter in ("audio", "all", "") or type_filter not in ("audio", "script")

    results: List[RedoItemResponse] = []
    for ch_dir in list_channel_dirs():
        ch_code = ch_dir.name.upper()
        if channel_filter and ch_code != channel_filter:
            continue
        for vid_dir in list_video_dirs(ch_code):
            video_number = vid_dir.name
            try:
                st = load_status(ch_code, video_number)
            except HTTPException:
                continue
            meta = st.get("metadata", {}) if isinstance(st.get("metadata", {}), dict) else {}
            redo_script = meta.get("redo_script")
            redo_audio = meta.get("redo_audio")
            redo_note = meta.get("redo_note")
            if redo_script is None:
                redo_script = True
            if redo_audio is None:
                redo_audio = True
            # type filter
            if type_filter in ("script", "audio"):
                if type_filter == "script" and not redo_script:
                    continue
                if type_filter == "audio" and not redo_audio:
                    continue
            if not want_script and not want_audio:
                continue
            title = meta.get("sheet_title") or meta.get("title")
            results.append(
                RedoItemResponse(
                    channel=ch_code,
                    video=video_number,
                    redo_script=bool(redo_script),
                    redo_audio=bool(redo_audio),
                    redo_note=redo_note,
                    title=title,
                    status=st.get("status"),
                )
            )
    return results


@router.get("/summary", response_model=List[RedoSummaryItem])
def list_redo_summary(
    channel: Optional[str] = Query(None, description="CHコード (例: CH02)"),
):
    channel_filter = normalize_channel_code(channel) if channel else None

    def _iter_channel_codes() -> List[str]:
        if channel_filter:
            return [channel_filter]
        codes: set[str] = set()
        if CHANNEL_PLANNING_DIR.exists():
            for csv_path in CHANNEL_PLANNING_DIR.glob("CH*.csv"):
                codes.add(csv_path.stem.upper())
        for ch_dir in list_channel_dirs():
            codes.add(ch_dir.name.upper())
        filtered = [code for code in codes if re.match(r"^CH\d+$", code)]
        return sorted(filtered, key=_channel_sort_key)

    def _is_progress_published(value: Any) -> bool:
        s = str(value or "").strip()
        if not s:
            return False
        return "投稿済み" in s or "公開済み" in s or s.lower() in {"published", "posted"}

    summaries: Dict[str, Dict[str, int]] = {}

    for ch_code in _iter_channel_codes():
        # Snapshot redo flags from status.json (if present).
        status_redo: Dict[str, Dict[str, Any]] = {}
        for vid_dir in list_video_dirs(ch_code):
            st = load_status_optional(ch_code, vid_dir.name)
            if not st:
                continue
            meta = st.get("metadata") if isinstance(st, dict) else None
            meta = meta if isinstance(meta, dict) else {}
            redo_script = meta.get("redo_script")
            redo_audio = meta.get("redo_audio")
            published_lock = bool(meta.get("published_lock"))
            status_redo[vid_dir.name] = {
                "redo_script": True if redo_script is None else bool(redo_script),
                "redo_audio": True if redo_audio is None else bool(redo_audio),
                "published_lock": published_lock,
            }

        sums = summaries.setdefault(ch_code, {"redo_script": 0, "redo_audio": 0, "redo_both": 0})

        csv_path = CHANNEL_PLANNING_DIR / f"{ch_code}.csv"
        if csv_path.exists():
            try:
                with csv_path.open("r", encoding="utf-8", newline="") as handle:
                    reader = csv.DictReader(handle)
                    rows = list(reader)
            except Exception:
                rows = []

            for row in rows:
                raw_video = (row.get("動画番号") or row.get("video") or row.get("Video") or "").strip()
                token = normalize_planning_video_number(raw_video)
                if not token:
                    continue
                progress_value = row.get("進捗") or row.get("progress")
                published_locked = _is_progress_published(progress_value)

                snap = status_redo.get(token)
                if snap:
                    published_locked = published_locked or bool(snap.get("published_lock"))
                    redo_script = bool(snap.get("redo_script", True))
                    redo_audio = bool(snap.get("redo_audio", True))
                else:
                    redo_script = True
                    redo_audio = True

                if published_locked:
                    redo_script = False
                    redo_audio = False

                if redo_script:
                    sums["redo_script"] += 1
                if redo_audio:
                    sums["redo_audio"] += 1
                if redo_script and redo_audio:
                    sums["redo_both"] += 1

        # If no CSV exists, fallback to status snapshot only.
        elif status_redo:
            for snap in status_redo.values():
                if snap.get("published_lock"):
                    continue
                redo_script = bool(snap.get("redo_script", True))
                redo_audio = bool(snap.get("redo_audio", True))
                if redo_script:
                    sums["redo_script"] += 1
                if redo_audio:
                    sums["redo_audio"] += 1
                if redo_script and redo_audio:
                    sums["redo_both"] += 1

    out = [
        RedoSummaryItem(
            channel=ch_code,
            redo_script=int(sums.get("redo_script", 0)),
            redo_audio=int(sums.get("redo_audio", 0)),
            redo_both=int(sums.get("redo_both", 0)),
        )
        for ch_code, sums in sorted(summaries.items(), key=lambda item: _channel_sort_key(item[0]))
    ]
    if channel_filter:
        return out
    return [item for item in out if item.redo_script or item.redo_audio or item.redo_both]
