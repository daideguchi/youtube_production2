from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.app.planning_csv_store import _normalize_video_number_token
from backend.app.scripts_models import OptimisticUpdateRequest
from backend.tools.optional_fields_registry import FIELD_KEYS


class PlanningFieldPayload(BaseModel):
    key: str
    column: str
    label: str
    value: Optional[str] = None


class PlanningInfoResponse(BaseModel):
    creation_flag: Optional[str] = None
    fields: List[PlanningFieldPayload] = Field(default_factory=list)


class PlanningUpdateRequest(OptimisticUpdateRequest):
    creation_flag: Optional[str] = Field(
        None,
        description="G列（作成フラグ）の値。空文字またはnullでリセット。",
    )
    fields: Dict[str, Optional[str]] = Field(
        default_factory=dict,
        description="任意フィールドの更新（キー: optional_fields_registry の内部キー）",
    )

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
        invalid = [key for key in value if key not in FIELD_KEYS]
        if invalid:
            joined = ", ".join(sorted(invalid))
            raise HTTPException(status_code=400, detail=f"不明な企画フィールドが指定されました: {joined}")
        return value


class PlanningUpdateResponse(BaseModel):
    status: str
    updated_at: str
    planning: PlanningInfoResponse


class PlanningCreateRequest(BaseModel):
    channel: str = Field(..., description="CHコード（例: CH01）")
    video_number: str = Field(..., description="動画番号（数字）")
    title: str = Field(..., description="企画タイトル")
    no: Optional[str] = Field(None, description="No. 列。省略時は動画番号を使用。")
    creation_flag: Optional[str] = Field("3", description="G列（作成フラグ）の初期値")
    progress: Optional[str] = Field("topic_research: pending", description="進捗列の初期値")
    fields: Dict[str, Optional[str]] = Field(
        default_factory=dict,
        description="optional_fields_registry のキーに対応するフィールド値",
    )

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, value: str) -> str:
        token = value.strip().upper()
        if not token.startswith("CH"):
            raise HTTPException(status_code=400, detail="channel は CH で始まるコードを指定してください。")
        return token

    @field_validator("video_number")
    @classmethod
    def validate_video_number(cls, value: str) -> str:
        _normalize_video_number_token(value)
        return value

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="タイトルを入力してください。")
        return normalized

    @field_validator("fields")
    @classmethod
    def validate_create_fields(cls, value: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
        invalid = [key for key in value if key not in FIELD_KEYS]
        if invalid:
            joined = ", ".join(sorted(invalid))
            raise HTTPException(status_code=400, detail=f"不明な企画フィールドが指定されました: {joined}")
        return value


class PlanningCsvRowResponse(BaseModel):
    channel: str
    video_number: str
    script_id: Optional[str] = None
    title: Optional[str] = None
    script_path: Optional[str] = None
    progress: Optional[str] = None
    quality_check: Optional[str] = None
    character_count: Optional[int] = None
    updated_at: Optional[str] = None
    planning: Optional[PlanningInfoResponse] = None
    columns: Dict[str, Optional[str]] = Field(default_factory=dict)


class PlanningProgressUpdateRequest(BaseModel):
    progress: str = Field(..., description="企画CSVの進捗列を更新する。")
    expected_updated_at: Optional[str] = Field(
        default=None,
        description="競合検知用の更新トークン（CSVの更新日時列）。列が未存在/空の場合はベストエフォートで更新する。",
    )


class PlanningSpreadsheetResponse(BaseModel):
    channel: str
    headers: List[str]
    rows: List[List[Optional[str]]]

