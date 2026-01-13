from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Query

from backend.app.normalize import normalize_channel_code
from backend.main import PlanningCsvRowResponse, PlanningSpreadsheetResponse, _load_channel_spreadsheet, _load_planning_rows

router = APIRouter(prefix="/api", tags=["planning"])


@router.get("/planning", response_model=List[PlanningCsvRowResponse])
def list_planning_rows(channel: Optional[str] = Query(None, description="CHコード (例: CH06)")):
    channel_code = normalize_channel_code(channel) if channel else None
    return _load_planning_rows(channel_code)


@router.get("/planning/spreadsheet", response_model=PlanningSpreadsheetResponse)
def get_planning_spreadsheet(channel: str = Query(..., description="CHコード (例: CH06)")):
    channel_code = normalize_channel_code(channel)
    return _load_channel_spreadsheet(channel_code)

