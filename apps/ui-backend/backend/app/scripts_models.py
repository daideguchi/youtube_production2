from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field, model_validator


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


class NaturalCommandAction(BaseModel):
    type: Literal["replace", "insert_pause"]
    target: Literal["tts", "assembled", "srt"] = "tts"
    original: Optional[str] = None
    replacement: Optional[str] = None
    scope: Literal["first", "all"] = "first"
    update_assembled: bool = True
    regenerate_audio: bool = False
    pause_seconds: Optional[float] = None
    pause_scope: Literal["cursor", "line_end", "section_end"] = "cursor"

    @model_validator(mode="after")
    def _validate_payload(self) -> "NaturalCommandAction":
        if self.type == "replace":
            if not self.original or not self.replacement:
                raise ValueError("Replace action must include original and replacement text.")
        elif self.type == "insert_pause":
            if self.pause_seconds is None:
                raise ValueError("Insert pause action must include pause_seconds.")
            if self.pause_seconds <= 0:
                raise ValueError("pause_seconds must be greater than zero.")
        return self


class NaturalCommandRequest(OptimisticUpdateRequest):
    command: str


class NaturalCommandResponse(BaseModel):
    actions: List[NaturalCommandAction]
    message: Optional[str] = None
