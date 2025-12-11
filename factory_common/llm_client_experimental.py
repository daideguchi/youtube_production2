from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from factory_common.llm_router import get_router
from commentary_02_srt2images_timeline.src.config.llm_resolver import (
    resolve_task,
    get_model_config,
    get_capabilities,
    get_defaults,
)  # type: ignore


@dataclass
class LLMCallOptions:
    task: str
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    response_format: Optional[str] = None
    timeout: Optional[int] = None
    extra: Dict[str, Any] = None
    stop: Optional[List[str]] = None
    thinking_level: Optional[str] = None


@dataclass
class LLMResult:
    content: str
    provider: str
    model: str
    usage: Dict[str, Any]
    raw: Any


class LLMClient:
    """
    Transitional LLM client that delegates to existing llm_router
    to avoid breaking current flows. Future work: resolve tasks/tiers
    via configs/llm.yml and normalize params per capabilities.
    """

    def __init__(self, config_path: Path | str | None = None) -> None:
        self.config_path = Path(config_path) if config_path else None
        self.router = get_router()
        self.use_llm_yaml = bool(int(os.getenv("LLM_EXPERIMENTAL_USE_LLMYML", "0")))

    def call(self, messages: List[Dict[str, Any]], options: LLMCallOptions) -> LLMResult:
        resolved_model = None
        resolved_tier = None
        if self.use_llm_yaml:
            resolved = resolve_task(options.task)
            if not resolved:
                raise ValueError(f"Task '{options.task}' not found in llm.yml")
            resolved_model = resolved.get("model")
            resolved_tier = resolved.get("tier")
            if not resolved_model:
                raise ValueError(f"No model resolved for task '{options.task}' from llm.yml")

            model_conf = get_model_config(resolved_model) or {}
            provider = model_conf.get("provider")
            caps = get_capabilities(resolved_model)
            defaults = get_defaults(options.task)

            # Apply task defaults from llm.yml if caller left them None
            if options.temperature is None and "temperature" in defaults:
                options.temperature = defaults.get("temperature")
            if options.max_output_tokens is None and "max_output_tokens" in defaults:
                options.max_output_tokens = defaults.get("max_output_tokens")
            if options.response_format is None and "response_format" in defaults:
                options.response_format = defaults.get("response_format")
            if options.thinking_level is None and "thinking_level" in defaults:
                options.thinking_level = defaults.get("thinking_level")

            # Capability-based clamps to avoid invalid params hitting legacy router
            if caps.get("allow_temperature") is False:
                options.temperature = None
            if caps.get("allow_stop") is False:
                options.stop = None
                if options.extra:
                    options.extra.pop("stop", None)
            if caps.get("allow_json_mode") is False and options.response_format == "json_object":
                options.response_format = None
            if caps.get("allow_reasoning_effort") is False:
                options.thinking_level = None
            # Temporary: legacy router Azure path does not accept max_output_tokens param
            if provider == "azure":
                options.max_output_tokens = None

        resp = self.router.call(
            task=options.task,
            messages=messages,
            temperature=options.temperature,
            max_output_tokens=options.max_output_tokens,
            response_format=options.response_format,
            timeout=options.timeout,
            stop=options.stop,
        )
        return LLMResult(
            content=resp,
            provider="legacy_router",
            model=resolved_model or "(resolved by router)",
            usage={},
            raw=resp,
        )
