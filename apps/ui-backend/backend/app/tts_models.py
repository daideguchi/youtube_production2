from __future__ import annotations

"""
TTS-related Pydantic models shared across UI backend modules.

created: 2026-01-14
"""

from typing import Any, Dict, List, Literal, Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.scripts_models import OptimisticUpdateRequest


class TTSIssue(BaseModel):
    type: str
    line: Optional[int] = None
    detail: Optional[str] = None


class TTSValidateRequest(BaseModel):
    content: str


class TTSValidateResponse(BaseModel):
    sanitized_content: str
    issues: List[TTSIssue]
    valid: bool


class TtsUpdateRequest(OptimisticUpdateRequest):
    content: Optional[str] = Field(None, description="ポーズタグを含まないプレーンテキスト（レガシー互換）")
    tagged_content: Optional[str] = Field(
        None, description="ポーズタグ付きテキスト（[0.5s] などのタグを含む場合はこちらを指定）"
    )
    content_mode: Optional[Literal["plain", "tagged"]] = Field(
        None, description="どちらのテキストを編集したか。未指定の場合は自動推定します。"
    )
    regenerate_audio: Optional[bool] = Field(None, description="音声と字幕を再生成するか")
    update_assembled: Optional[bool] = Field(None, description="assembled.md も同期更新するか")

    @model_validator(mode="after")
    def _validate_payload(self) -> "TtsUpdateRequest":
        if self.content is None and self.tagged_content is None:
            raise HTTPException(status_code=400, detail="content または tagged_content を指定してください。")
        return self


class TtsReplaceRequest(OptimisticUpdateRequest):
    original: str = Field(..., description="置換対象の文字列")
    replacement: str = Field(..., description="置換後の文字列")
    scope: Optional[str] = Field("first", description="first または all")
    update_assembled: bool = Field(False, description="assembled.md も同時に置換するか")
    regenerate_audio: bool = Field(True, description="音声とSRTを再生成するか")

    @field_validator("original")
    @classmethod
    def validate_original(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="original は必須です。")
        return normalized

    @field_validator("replacement")
    @classmethod
    def validate_replacement(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise HTTPException(status_code=400, detail="replacement は必須です。")
        return normalized

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: Optional[str]) -> str:
        allowed = {"first", "all"}
        scope = (value or "first").lower()
        if scope not in allowed:
            raise HTTPException(status_code=400, detail=f"scope は {allowed} のいずれかを指定してください。")
        return scope


class TtsReplaceResponse(BaseModel):
    replaced: int
    content: str
    plain_content: str
    tagged_content: Optional[str] = None
    pause_map: Optional[List[Dict[str, Any]]] = None
    audio_regenerated: bool
    message: Optional[str] = None
