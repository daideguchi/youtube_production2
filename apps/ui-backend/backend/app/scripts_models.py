from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class OptimisticUpdateRequest(BaseModel):
    expected_updated_at: Optional[str] = Field(None, description="最新バージョンの updated_at 値")


class TextUpdateRequest(OptimisticUpdateRequest):
    content: str
    regenerate_audio: Optional[bool] = Field(None, description="音声と字幕を再生成するか")
    update_assembled: Optional[bool] = Field(None, description="assembled.md も同期更新するか")


class HumanScriptUpdateRequest(OptimisticUpdateRequest):
    assembled_human: Optional[str] = None
    script_audio_human: Optional[str] = None
    audio_reviewed: Optional[bool] = None


class HumanScriptResponse(BaseModel):
    assembled_path: Optional[str] = None
    assembled_content: Optional[str] = None
    assembled_human_path: Optional[str] = None
    assembled_human_content: Optional[str] = None
    script_audio_path: Optional[str] = None
    script_audio_content: Optional[str] = None
    script_audio_human_path: Optional[str] = None
    script_audio_human_content: Optional[str] = None
    audio_reviewed: bool = False
    updated_at: Optional[str] = None
    warnings: List[str] = Field(default_factory=list)


class ScriptTextResponse(BaseModel):
    path: Optional[str]
    content: str
    updated_at: Optional[str] = None

