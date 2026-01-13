from __future__ import annotations

import csv
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from backend.app.normalize import normalize_optional_text
from backend.app.planning_models import PlanningCsvRowResponse, PlanningSpreadsheetResponse
from backend.app.planning_payload import build_planning_payload_from_row
from factory_common.paths import script_pkg_root
from script_pipeline.tools import planning_store

logger = logging.getLogger(__name__)

SCRIPT_PIPELINE_ROOT = script_pkg_root()
SPREADSHEET_EXPORT_DIR = SCRIPT_PIPELINE_ROOT / "exports" / "spreadsheets"


def current_timestamp() -> str:
    """Return an ISO8601 UTC timestamp with ``Z`` suffix."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_csv_file(path: Path) -> Tuple[List[str], List[List[str]]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            headers = next(reader)
        except StopIteration:
            return [], []
        rows = [row for row in reader]
    return headers, rows


def _ensure_planning_store_ready() -> None:
    if planning_store.list_channels():
        return
    detail = "channels CSV がまだ生成されていません。ssot_sync を実行してください。"
    raise HTTPException(status_code=503, detail=detail)


def _build_spreadsheet_from_planning(channel_code: str) -> PlanningSpreadsheetResponse:
    _ensure_planning_store_ready()
    rows = planning_store.get_rows(channel_code, force_refresh=False)
    headers = planning_store.get_fieldnames()
    if not headers:
        return PlanningSpreadsheetResponse(channel=channel_code, headers=[], rows=[])
    result_rows: List[List[Optional[str]]] = []
    for entry in rows:
        raw = dict(entry.raw)
        if entry.script_id:
            raw.setdefault("動画ID", entry.script_id)
        raw.setdefault("チャンネル", entry.channel_code)
        if entry.video_number:
            raw.setdefault("動画番号", entry.video_number)
        row_values = [raw.get(column, "") for column in headers]
        result_rows.append(row_values)
    return PlanningSpreadsheetResponse(channel=channel_code, headers=headers, rows=result_rows)


def _parse_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _iter_planning_rows(channel_filter: Optional[str]):
    _ensure_planning_store_ready()
    if channel_filter:
        targets = [channel_filter]
    else:
        targets = list(planning_store.list_channels())
    for code in targets:
        for entry in planning_store.get_rows(code, force_refresh=False):
            yield entry


def _load_planning_rows(channel_filter: Optional[str]) -> List[PlanningCsvRowResponse]:
    rows: List[PlanningCsvRowResponse] = []
    for entry in _iter_planning_rows(channel_filter):
        raw = dict(entry.raw)
        script_id = entry.script_id or raw.get("動画ID") or raw.get("台本番号") or ""
        script_id = script_id.strip()
        channel_code = entry.channel_code
        raw.setdefault("チャンネル", channel_code)
        if script_id:
            raw.setdefault("動画ID", script_id)
        video_number = entry.video_number or ""
        if not video_number and script_id and "-" in script_id:
            video_number = script_id.split("-", 1)[1]
        if video_number:
            raw.setdefault("動画番号", video_number)
        planning_payload = build_planning_payload_from_row(raw)
        # columns を UI 用にサニタイズ（pydantic ValidationError 回避）
        columns_sanitized: Dict[str, Optional[str]] = {}
        for key, value in raw.items():
            if key is None:
                continue
            k = str(key)
            v: Optional[str]
            if isinstance(value, list):
                v = "\n".join(str(x) for x in value if x is not None)
            else:
                v = str(value) if value not in ("", None) else None
            columns_sanitized[k] = v
        rows.append(
            PlanningCsvRowResponse(
                channel=channel_code,
                video_number=video_number,
                script_id=script_id or None,
                title=normalize_optional_text(raw.get("タイトル")),
                script_path=normalize_optional_text(raw.get("台本")),
                progress=normalize_optional_text(raw.get("進捗")),
                quality_check=normalize_optional_text(raw.get("品質チェック結果")),
                character_count=_parse_int(raw.get("文字数")),
                updated_at=normalize_optional_text(raw.get("更新日時")),
                planning=planning_payload,
                columns=columns_sanitized,
            )
        )
    rows.sort(key=lambda item: (item.channel, item.video_number))
    return rows


def _looks_like_html(headers: List[str], rows: List[List[str]]) -> bool:
    def _sample(values: List[str]) -> str:
        return " ".join(values[:3]).lower()

    if any("<!doctype" in cell.lower() or "<html" in cell.lower() for cell in headers if cell):
        return True
    for row in rows[:2]:
        if any("<!doctype" in cell.lower() or "<html" in cell.lower() for cell in row if cell):
            return True
    combined = _sample(headers)
    if combined and ("login" in combined and "google" in combined):
        return True
    return False


def _load_channel_spreadsheet(channel_code: str) -> PlanningSpreadsheetResponse:
    csv_path = SPREADSHEET_EXPORT_DIR / f"{channel_code}.csv"
    if csv_path.exists():
        headers, rows = _read_csv_file(csv_path)
        if headers and not _looks_like_html(headers, rows):
            return PlanningSpreadsheetResponse(channel=channel_code, headers=headers, rows=rows)
        logger.warning("%s は有効な CSV として解析できません。planning SoT から再構成します。", csv_path)
    return _build_spreadsheet_from_planning(channel_code)

