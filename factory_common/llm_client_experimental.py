from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from factory_common.llm_router import get_router


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

    def call(self, messages: List[Dict[str, Any]], options: LLMCallOptions) -> LLMResult:
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
            model="(resolved by router)",
            usage={},
            raw=resp,
        )
