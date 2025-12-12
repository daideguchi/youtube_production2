from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from factory_common.llm_config import load_llm_config, resolve_task
from factory_common.agent_mode import maybe_handle_agent_mode
from factory_common.llm_api_failover import maybe_failover_to_think

try:
    from openai import AzureOpenAI, OpenAI
except ImportError:  # pragma: no cover - environment may not have openai
    AzureOpenAI = None
    OpenAI = None

logger = logging.getLogger("LLMClient")


@dataclass
class LLMResult:
    content: str
    provider: str
    model: str
    usage: Dict[str, Any]
    raw: Any = None


class LLMClient:
    """
    Capability-aware LLM caller with task→tier→model resolution.
    - Uses configs/llm.yml as the primary source.
    - Optional overrides via tier mapping/candidates files.
    - Supports Azure (responses/chat) and OpenRouter chat; Gemini text is not yet wired.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        tier_mapping_path: Optional[str] = None,
        tier_candidates_path: Optional[str] = None,
        provider_clients: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.config = load_llm_config(
            config_path=config_path,
            tier_mapping_path=tier_mapping_path,
            tier_candidates_path=tier_candidates_path,
        )
        self.clients = provider_clients or self._build_clients()

    def _build_clients(self) -> Dict[str, Any]:
        clients: Dict[str, Any] = {}
        providers = self.config.get("providers", {})

        # Azure
        azure_conf = providers.get("azure")
        if azure_conf and AzureOpenAI:
            endpoint = azure_conf.get("env_endpoint")
            api_key = azure_conf.get("env_api_key")
            ver = azure_conf.get("default_api_version")
            if endpoint and api_key:
                import os

                ep = os.getenv(endpoint)
                key = os.getenv(api_key)
                if ep and key:
                    if not ep.startswith("http"):
                        ep = "https://" + ep
                    if "/openai/" in ep:
                        ep = ep.split("/openai/")[0]
                    clients["azure"] = AzureOpenAI(
                        api_key=key,
                        api_version=ver,
                        azure_endpoint=ep,
                    )

        # OpenRouter
        or_conf = providers.get("openrouter")
        if or_conf and OpenAI:
            api_key = or_conf.get("env_api_key")
            base = or_conf.get("base_url")
            if api_key and base:
                import os

                key = os.getenv(api_key)
                if key:
                    clients["openrouter"] = OpenAI(api_key=key, base_url=base)

        # Gemini text is not wired here; image handled via ImageClient.
        return clients

    def call(
        self,
        task: str,
        messages: List[Dict[str, str]],
        **options: Any,
    ) -> LLMResult:
        resolved = resolve_task(self.config, task)
        models = resolved.get("models", [])
        defaults = resolved.get("defaults", {}) or {}

        merged_options: Dict[str, Any] = {**defaults, **options}
        response_format = merged_options.get("response_format")
        agent_result = maybe_handle_agent_mode(
            task=task,
            messages=messages,
            options=merged_options,
            response_format=str(response_format) if response_format is not None else None,
            return_raw=False,
        )
        if agent_result is not None:
            return LLMResult(
                content=str(agent_result.get("content", "")),
                provider=str(agent_result.get("provider", "agent")),
                model=str(agent_result.get("model", "agent")),
                usage=dict(agent_result.get("usage") or {}),
                raw=agent_result.get("raw"),
            )

        last_error: Exception | None = None
        for model_key in models:
            model_conf = (self.config.get("models") or {}).get(model_key) or {}
            provider = model_conf.get("provider")
            if not provider:
                continue

            client = self.clients.get(provider)
            if not client:
                continue

            try:
                payload = self._normalize_params(model_conf, defaults, options)
                content, usage = self._invoke(provider, client, model_conf, messages, payload)
                result = LLMResult(
                    content=content,
                    provider=provider,
                    model=model_conf.get("deployment") or model_conf.get("model") or "",
                    usage=usage or {},
                )
                self._log_usage(task, result)
                return result
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLMClient: %s failed for %s (%s)", model_key, task, exc)
                last_error = exc
                continue

        failover = maybe_failover_to_think(
            task=task,
            messages=messages,
            options=merged_options,
            response_format=str(response_format) if response_format is not None else None,
            return_raw=False,
            failure={
                "error": str(last_error) if last_error is not None else None,
                "error_class": last_error.__class__.__name__ if last_error is not None else None,
                "status_code": None,
                "chain": models,
            },
        )
        if failover is not None:
            return LLMResult(
                content=str(failover.get("content", "")),
                provider=str(failover.get("provider", "agent")),
                model=str(failover.get("model", "agent")),
                usage=dict(failover.get("usage") or {}),
                raw=failover.get("raw"),
            )

        raise RuntimeError(f"All models failed for task '{task}'. last_error={last_error}")

    def _normalize_params(
        self,
        model_conf: Dict[str, Any],
        defaults: Dict[str, Any],
        options: Dict[str, Any],
    ) -> Dict[str, Any]:
        cap = model_conf.get("capabilities", {}) or {}
        merged: Dict[str, Any] = {**defaults, **options}

        # Temperature/stop allowed?
        if not cap.get("allow_temperature", False):
            merged.pop("temperature", None)
        if not cap.get("allow_stop", False):
            merged.pop("stop", None)

        # JSON mode
        if merged.get("response_format") == "json_object":
            if cap.get("allow_json_mode"):
                merged["response_format"] = {"type": "json_object"}
            else:
                merged.pop("response_format", None)

        # Reasoning/thinking
        thinking = merged.pop("thinking_level", None) or merged.pop("reasoning_effort", None)
        if thinking and cap.get("allow_reasoning"):
            merged["reasoning"] = {"effort": thinking}

        # Max tokens mapping
        if "max_output_tokens" in merged:
            merged["max_tokens"] = merged.pop("max_output_tokens")
        if "max_completion_tokens" in merged:
            merged["max_tokens"] = merged.pop("max_completion_tokens")

        # Explicit clamp to provider key will be done in _invoke
        return merged

    def _invoke(
        self,
        provider: str,
        client: Any,
        model_conf: Dict[str, Any],
        messages: List[Dict[str, str]],
        params: Dict[str, Any],
    ) -> tuple[str, Dict[str, Any]]:
        api_type = model_conf.get("api_type", "chat")
        if api_type not in ("chat", "responses"):
            raise NotImplementedError(f"Unsupported api_type {api_type} for provider {provider}")

        model_name = model_conf.get("deployment") if provider == "azure" else model_conf.get("model")
        if not model_name:
            raise ValueError("Model name/deployment missing in config")

        call_params = dict(params)
        if provider == "azure":
            # Azure uses max_output_tokens for responses; the SDK will map.
            if "max_tokens" in call_params:
                call_params["max_output_tokens"] = call_params.pop("max_tokens")
        else:
            # OpenRouter uses max_tokens
            call_params.pop("max_output_tokens", None)

        resp = client.chat.completions.create(
            model=model_name,
            messages=messages,
            **call_params,
        )
        content = resp.choices[0].message.content
        usage = getattr(resp, "usage", {}) or {}
        return content, dict(usage)

    def _log_usage(self, task: str, result: LLMResult) -> None:
        """
        Append usage info to JSONL log if enabled.
        Env:
          - LLM_USAGE_LOG_PATH: override log path (default: logs/llm_usage.jsonl)
          - LLM_USAGE_LOG_DISABLE: if "1", skip logging
        """
        import os

        if os.getenv("LLM_USAGE_LOG_DISABLE") == "1":
            return
        log_path = os.getenv("LLM_USAGE_LOG_PATH")
        if not log_path:
            project_root = Path(__file__).resolve().parents[1]
            log_path = project_root / "logs" / "llm_usage.jsonl"
        else:
            log_path = Path(log_path)

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task": task,
            "provider": result.provider,
            "model": result.model,
            "usage": result.usage,
        }
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:  # best-effort logging
            pass
