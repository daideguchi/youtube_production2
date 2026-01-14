from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException

from backend.app.normalize import CHANNEL_PLANNING_DIR
from backend.core.portalocker_compat import portalocker
from script_pipeline.tools import planning_store

logger = logging.getLogger(__name__)

# Keep behavior compatible with legacy backend.main helpers.
LOCK_TIMEOUT_SECONDS = 5.0


def _normalize_video_number_token(value: str) -> str:
    token = value.strip()
    if not token:
        raise HTTPException(status_code=400, detail="動画番号を入力してください。")
    if not token.isdigit():
        raise HTTPException(status_code=400, detail="動画番号は数字のみ指定してください。")
    return f"{int(token):03d}"


def _maybe_int_from_token(value: str) -> Optional[int]:
    trimmed = "".join(ch for ch in value if ch.isdigit())
    if not trimmed:
        return None
    try:
        return int(trimmed)
    except ValueError:
        return None


def _read_channel_csv_rows(channel_code: str) -> Tuple[List[str], List[Dict[str, str]]]:
    channel_code = channel_code.upper()
    channel_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    if channel_path.exists():
        with channel_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = list(reader.fieldnames or [])
            rows = list(reader)
            if fieldnames:
                return fieldnames, rows
    # fallback: use planning_store fieldnames (union) or legacy master columns
    fieldnames = planning_store.get_fieldnames()
    if not fieldnames:
        raise HTTPException(status_code=404, detail="channels CSV は使用しません。channels CSV を利用してください。")
    return list(fieldnames), []


def _write_csv_with_lock(path: Path, fieldnames: List[str], rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialised = [{key: (row.get(key) or "") for key in fieldnames} for row in rows]
    try:
        with portalocker.Lock(
            str(path),
            mode="w",
            encoding="utf-8",
            timeout=LOCK_TIMEOUT_SECONDS,
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            writer.writerows(serialised)
            handle.flush()
    except portalocker.exceptions.Timeout as exc:  # pragma: no cover - IO guard
        logger.warning("Lock timeout while writing %s", path)
        raise HTTPException(
            status_code=423,
            detail=f"{path.name} が使用中です。数秒後に再試行してください。",
        ) from exc
    except portalocker.exceptions.LockException as exc:  # pragma: no cover - IO guard
        logger.exception("Unexpected lock error for %s", path)
        raise HTTPException(
            status_code=500,
            detail=f"{path.name} の更新中にロックエラーが発生しました。",
        ) from exc
