from __future__ import annotations

"""
Settings-related Pydantic models shared across UI backend modules.

created: 2026-01-09
"""

from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel, Field


class LLMConfig(BaseModel):
    caption_provider: str = "openai"
    openai_api_key: Optional[str] = None
    openai_caption_model: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    openrouter_caption_model: Optional[str] = None
    openai_key_configured: bool
    openrouter_key_configured: bool
    openai_models: List[str]
    openrouter_models: List[str]
    openai_key_preview: Optional[str] = None
    openrouter_key_preview: Optional[str] = None
    openai_models_error: Optional[str] = None
    openrouter_models_error: Optional[str] = None
    phase_models: Dict[str, Dict[str, Any]]
    phase_details: Optional[Dict[str, Dict[str, Any]]] = None


class LLMSettingsResponse(BaseModel):
    llm: LLMConfig


class LLMSettingsUpdate(BaseModel):
    caption_provider: Optional[Literal["openai", "openrouter"]] = None
    openai_api_key: Optional[str] = None
    openai_caption_model: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    openrouter_caption_model: Optional[str] = None
    phase_models: Optional[Dict[str, Dict[str, object]]] = None


class CodexExecConfig(BaseModel):
    profile: str
    model: Optional[str] = None
    sandbox: Optional[str] = None
    timeout_s: Optional[int] = None
    profile_source: Optional[str] = None  # env|local|base|default
    model_source: Optional[str] = None  # env|local|base|default
    local_config_path: str
    base_config_path: str


class CodexCliProfile(BaseModel):
    name: str
    model: Optional[str] = None
    model_reasoning_effort: Optional[str] = None


class CodexCliConfig(BaseModel):
    config_path: str
    exists: bool
    profiles: List[CodexCliProfile] = Field(default_factory=list)


class CodexSettingsResponse(BaseModel):
    codex_exec: CodexExecConfig
    codex_cli: CodexCliConfig
    active_profile: CodexCliProfile
    allowed_reasoning_effort: List[str] = Field(default_factory=list)


class CodexSettingsUpdate(BaseModel):
    # `configs/codex_exec.local.yaml` (pipeline)
    profile: Optional[str] = None
    model: Optional[str] = None

    # `~/.codex/config.toml` (Codex CLI profile)
    cli_profile: Optional[str] = None
    cli_model: Optional[str] = None
    model_reasoning_effort: Optional[Literal["low", "medium", "high", "xhigh"]] = None

