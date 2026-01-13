from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from backend.app.normalize import CHANNEL_PLANNING_DIR, normalize_channel_code, normalize_optional_text
from backend.app.planning_csv_store import (
    _maybe_int_from_token,
    _normalize_video_number_token,
    _read_channel_csv_rows,
    _write_csv_with_lock,
)
from backend.app.planning_models import (
    PlanningCreateRequest,
    PlanningCsvRowResponse,
    PlanningSpreadsheetResponse,
)
from backend.main import (
    _load_channel_spreadsheet,
    _load_planning_rows,
    build_planning_payload_from_row,
    current_timestamp,
)
from backend.tools.optional_fields_registry import FIELD_KEYS
from script_pipeline.tools import planning_requirements

router = APIRouter(prefix="/api", tags=["planning"])


@router.get("/planning", response_model=List[PlanningCsvRowResponse])
def list_planning_rows(channel: Optional[str] = Query(None, description="CHコード (例: CH06)")):
    channel_code = normalize_channel_code(channel) if channel else None
    return _load_planning_rows(channel_code)


@router.get("/planning/spreadsheet", response_model=PlanningSpreadsheetResponse)
def get_planning_spreadsheet(channel: str = Query(..., description="CHコード (例: CH06)")):
    channel_code = normalize_channel_code(channel)
    return _load_channel_spreadsheet(channel_code)


@router.post("/planning", response_model=PlanningCsvRowResponse, status_code=201)
def create_planning_entry(payload: PlanningCreateRequest):
    channel_code = normalize_channel_code(payload.channel)
    video_token = _normalize_video_number_token(payload.video_number)
    numeric_video = _maybe_int_from_token(video_token)
    fieldnames, rows = _read_channel_csv_rows(channel_code)
    fields_payload: Dict[str, Optional[str]] = dict(payload.fields)

    def _row_matches(entry: Dict[str, str]) -> bool:
        if (entry.get("チャンネル") or "").strip().upper() != channel_code:
            return False
        raw_value = entry.get("動画番号") or entry.get("No.") or ""
        if not raw_value:
            return False
        try:
            existing_token = _normalize_video_number_token(raw_value)
        except HTTPException:
            existing_token = raw_value.strip()
        return existing_token == video_token

    if any(_row_matches(row) for row in rows):
        raise HTTPException(status_code=409, detail=f"{channel_code}-{video_token} は既に存在します。")

    persona_text = planning_requirements.get_channel_persona(channel_code)
    target_override = normalize_optional_text(fields_payload.pop("target_audience", None))
    if persona_text:
        if target_override and target_override != persona_text:
            raise HTTPException(
                status_code=400,
                detail="ターゲット層はSSOTの共通ペルソナに固定されています。",
            )
    elif target_override:
        persona_text = target_override

    description_defaults = planning_requirements.get_description_defaults(channel_code)
    for key, default_value in description_defaults.items():
        if not normalize_optional_text(fields_payload.get(key)):
            fields_payload[key] = default_value

    required_keys = planning_requirements.resolve_required_field_keys(channel_code, numeric_video)
    missing_keys = [key for key in required_keys if not normalize_optional_text(fields_payload.get(key))]
    if missing_keys:
        missing_columns = [FIELD_KEYS.get(key, key) for key in missing_keys]
        raise HTTPException(
            status_code=400,
            detail=f"必須フィールドが未入力です: {', '.join(missing_columns)}",
        )

    # Add optional/required columns that are about to be written
    dynamic_columns = []
    for field_key in fields_payload.keys():
        column = FIELD_KEYS.get(field_key)
        if column:
            dynamic_columns.append(column)
    if persona_text:
        dynamic_columns.append("ターゲット層")
    for col in dynamic_columns:
        if col not in fieldnames:
            fieldnames.append(col)

    script_id = f"{channel_code}-{video_token}"
    new_row = {column: "" for column in fieldnames}
    if "チャンネル" in new_row:
        new_row["チャンネル"] = channel_code
    if "No." in new_row:
        if payload.no:
            new_row["No."] = payload.no.strip()
        else:
            new_row["No."] = str(int(video_token))
    if "動画番号" in new_row:
        new_row["動画番号"] = video_token
    if "動画ID" in new_row:
        new_row["動画ID"] = script_id
    if "台本番号" in new_row:
        new_row["台本番号"] = script_id
    new_row["タイトル"] = payload.title.strip()
    new_row["台本"] = new_row.get("台本", "")
    new_row["作成フラグ"] = payload.creation_flag or ""
    new_row["進捗"] = payload.progress or "topic_research: pending"
    new_row["品質チェック結果"] = new_row.get("品質チェック結果") or "未完了"
    new_row["文字数"] = new_row.get("文字数", "")
    new_row["納品"] = new_row.get("納品", "")
    new_row["更新日時"] = current_timestamp()

    for field_key, value in fields_payload.items():
        column = FIELD_KEYS.get(field_key)
        if column:
            text_value = normalize_optional_text(value) or ""
            new_row[column] = text_value

    if persona_text and "ターゲット層" in new_row:
        new_row["ターゲット層"] = persona_text

    rows.append(new_row)
    CHANNEL_PLANNING_DIR.mkdir(parents=True, exist_ok=True)
    channel_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    _write_csv_with_lock(channel_path, fieldnames, rows)

    planning_payload = build_planning_payload_from_row(new_row)
    character_count_raw = new_row.get("文字数")
    try:
        character_value = int(character_count_raw) if character_count_raw else None
    except ValueError:
        character_value = None

    return PlanningCsvRowResponse(
        channel=channel_code,
        video_number=video_token,
        script_id=script_id,
        title=new_row.get("タイトル"),
        script_path=new_row.get("台本"),
        progress=new_row.get("進捗"),
        quality_check=new_row.get("品質チェック結果"),
        character_count=character_value,
        updated_at=new_row.get("更新日時"),
        planning=planning_payload,
        columns=new_row,
    )
