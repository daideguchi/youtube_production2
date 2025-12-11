from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from factory_common.llm_router import get_router
from commentary_02_srt2images_timeline.src.config.llm_resolver import resolve_task  # type: ignore


@dataclass
class LLMCallOptions:
    task: str
    temperature: Optional[float] = None
    max_output_tokens: Optional[int] = None
    response_format: Optional[str] = None
    timeout: Optional[int] = None
    extra: Dict[str, Any] = None


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

        resp = self.router.call(
            task=options.task,
            messages=messages,
            temperature=options.temperature,
            max_output_tokens=options.max_output_tokens,
            response_format=options.response_format,
            timeout=options.timeout,
        )
        return LLMResult(
            content=resp,
            provider="legacy_router",
            model=resolved_model or "(resolved by router)",
            usage={},
            raw=resp,
        )
