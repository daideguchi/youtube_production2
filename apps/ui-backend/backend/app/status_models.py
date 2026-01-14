from __future__ import annotations

from typing import Dict

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from backend.app.scripts_models import OptimisticUpdateRequest

VALID_STAGE_STATUSES = {"pending", "in_progress", "blocked", "review", "completed"}
MAX_STATUS_LENGTH = 64


class StageStatus(BaseModel):
    status: str = Field("pending")

    @field_validator("status")
    @classmethod
    def validate_stage_status(cls, value: str) -> str:
        if value not in VALID_STAGE_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid stage status: {value!r}",
            )
        return value


class StageUpdateRequest(OptimisticUpdateRequest):
    stages: Dict[str, StageStatus]


class ReadyUpdateRequest(OptimisticUpdateRequest):
    ready: bool


class StatusUpdateRequest(OptimisticUpdateRequest):
    status: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="status は空にできません。")
        if len(normalized) > MAX_STATUS_LENGTH:
            raise HTTPException(status_code=400, detail="status が長すぎます。64文字以内にしてください。")
        return normalized

