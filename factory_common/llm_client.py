from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

# Legacy router (kept for backward compatibility)
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
    Transitional LLM client.

    - Today: delegates to legacy llm_router for compatibility.
    - Future: will resolve tasks/tiers/models via configs/llm.yml and normalize params.
    """

    def __init__(self, config_path: Path | str | None = None) -> None:
        # config_path is reserved for future use (llm.yml). Currently unused.
        self.config_path = Path(config_path) if config_path else None
        self.router = get_router()

    def call(self, messages: List[Dict[str, Any]], options: LLMCallOptions) -> LLMResult:
        """
        Invoke LLM for the given task using the legacy router; wrap into LLMResult.
        """
        resp = self.router.call(
            task=options.task,
            messages=messages,
            temperature=options.temperature,
            max_output_tokens=options.max_output_tokens,
            response_format=options.response_format,
            timeout=options.timeout,
        )
        # Legacy router returns plain content (str). Usage/metadata not available yet.
        return LLMResult(
            content=resp,
            provider="legacy_router",
            model="(resolved by router)",
            usage={},
            raw=resp,
        )
