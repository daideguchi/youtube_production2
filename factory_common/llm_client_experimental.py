from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from factory_common.llm_router import get_router
from commentary_02_srt2images_timeline.src.config.llm_resolver import resolve_task, get_model_config  # type: ignore


@dataclass
class LLMCallOptions:
    task: str
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    response_format: Optional[str] = None
    timeout: Optional[int] = None
    extra: Dict[str, Any] = None
    stop: Optional[List[str]] = None


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
            caps = (model_conf.get("capabilities") or {})
            # Capability-based clamps to avoid invalid params hitting legacy router
            if caps.get("allow_temperature") is False:
                options.temperature = None
            if caps.get("allow_stop") is False:
                options.stop = None
                if options.extra:
                    options.extra.pop("stop", None)
            if caps.get("allow_json_mode") is False and options.response_format == "json_object":
                options.response_format = None
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
