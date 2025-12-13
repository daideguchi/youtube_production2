from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from factory_common.llm_config import load_llm_config, resolve_task
from factory_common.agent_mode import maybe_handle_agent_mode
from factory_common.llm_api_failover import maybe_failover_to_think
from factory_common.llm_api_cache import (
    cache_enabled_for_task as _api_cache_enabled_for_task,
    cache_path as _api_cache_path,
    make_task_id as _api_cache_task_id,
    read_cache as _api_cache_read,
    write_cache as _api_cache_write,
)

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

        # App-level API cache (cost-saving reruns).
        if _api_cache_enabled_for_task(task):
            cached = _api_cache_read(task, messages, merged_options)
            if isinstance(cached, dict):
                meta = cached.get("meta") or {}
                usage = cached.get("usage") or {}
                content = cached.get("content", "")
                task_id = cached.get("task_id") or _api_cache_task_id(task, messages, merged_options)
                cache_file = _api_cache_path(str(task_id))
                result = LLMResult(
                    content=str(content),
                    provider=str(meta.get("provider") or "cache"),
                    model=str(meta.get("model") or meta.get("model_key") or "cache"),
                    usage=dict(usage) if isinstance(usage, dict) else {},
                    raw=None,
                )
                self._log_usage(
                    task,
                    result,
                    extra={
                        "cache": {"hit": True, "path": str(cache_file), "task_id": str(task_id)},
                        "finish_reason": meta.get("finish_reason"),
                        "retry": meta.get("retry"),
                    },
                )
                return result

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
                content, usage, finish_reason, retry_meta = self._invoke(provider, client, model_conf, messages, payload)
                result = LLMResult(
                    content=content,
                    provider=provider,
                    model=model_conf.get("deployment") or model_conf.get("model") or "",
                    usage=usage or {},
                )
                cache_write_path = None
                if finish_reason != "length":
                    cache_write_path = _api_cache_write(
                        task,
                        messages,
                        merged_options,
                        payload={
                            "content": content,
                            "usage": usage or {},
                            "meta": {
                                "provider": provider,
                                "model": model_conf.get("deployment") or model_conf.get("model") or "",
                                "model_key": model_key,
                                "finish_reason": finish_reason,
                                "retry": retry_meta,
                            },
                        },
                    )
                extra = {}
                if cache_write_path:
                    extra["cache"] = {"write": True, "path": str(cache_write_path)}
                if finish_reason:
                    extra["finish_reason"] = finish_reason
                if retry_meta:
                    extra["retry"] = retry_meta
                extra = extra or None
                self._log_usage(task, result, extra=extra)
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
    ) -> tuple[str, Dict[str, Any], str | None, Dict[str, Any] | None]:
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

        resp = client.chat.completions.create(model=model_name, messages=messages, **call_params)
        finish_reason = getattr(resp.choices[0], "finish_reason", None) if getattr(resp, "choices", None) else None

        # Retry-on-truncation (finish_reason == "length") to keep low default caps safe.
        retry_meta: Dict[str, Any] | None = None
        retry_enabled = (os.getenv("LLM_RETRY_ON_LENGTH") or "1").strip().lower() not in {"0", "false", "no", "off"}
        if retry_enabled and finish_reason == "length":
            # Determine current cap key
            cap_key = "max_output_tokens" if provider == "azure" else "max_tokens"
            cur_max = call_params.get(cap_key)
            if isinstance(cur_max, int) and cur_max > 0:
                caps = model_conf.get("capabilities", {}) or {}
                hard_cap = caps.get("max_output_tokens")
                try:
                    hard_cap = int(hard_cap) if hard_cap is not None else None
                except Exception:
                    hard_cap = None
                mult_raw = (os.getenv("LLM_LENGTH_RETRY_MULTIPLIER") or "2").strip()
                try:
                    mult = max(1.0, float(mult_raw))
                except Exception:
                    mult = 2.0
                env_cap_raw = (os.getenv("LLM_LENGTH_RETRY_MAX_TOKENS") or "").strip()
                env_cap = None
                if env_cap_raw:
                    try:
                        env_cap = int(env_cap_raw)
                    except Exception:
                        env_cap = None
                if hard_cap is not None and env_cap is not None:
                    hard_cap = min(hard_cap, env_cap)
                elif env_cap is not None:
                    hard_cap = env_cap

                new_max = int(max(cur_max + 1, round(cur_max * mult)))
                if hard_cap is not None:
                    new_max = min(new_max, hard_cap)
                if new_max <= cur_max:
                    raise RuntimeError(
                        f"finish_reason=length but cannot increase {cap_key} "
                        f"(current={cur_max}, cap={hard_cap})"
                    )
                logger.warning(
                    "LLMClient: %s returned finish_reason=length; retrying with %s=%s (was %s)",
                    model_name,
                    cap_key,
                    new_max,
                    cur_max,
                )
                call_params[cap_key] = new_max
                resp = client.chat.completions.create(model=model_name, messages=messages, **call_params)
                finish_reason2 = getattr(resp.choices[0], "finish_reason", None) if getattr(resp, "choices", None) else None
                retry_meta = {
                    "reason": "finish_reason_length",
                    "max_key": cap_key,
                    "from": cur_max,
                    "to": new_max,
                    "finish_reason": finish_reason2,
                }
                finish_reason = finish_reason2
                if finish_reason == "length":
                    raise RuntimeError(f"finish_reason=length after retry (max={new_max}); try next model")

        content = resp.choices[0].message.content
        usage = getattr(resp, "usage", {}) or {}
        return content, dict(usage), (str(finish_reason) if finish_reason is not None else None), retry_meta

    def _log_usage(self, task: str, result: LLMResult, extra: Optional[Dict[str, Any]] = None) -> None:
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
            from factory_common.paths import logs_root

            log_path = logs_root() / "llm_usage.jsonl"
        else:
            log_path = Path(log_path)

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "task": task,
            "provider": result.provider,
            "model": result.model,
            "usage": result.usage,
        }
        if extra:
            entry.update(extra)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:  # best-effort logging
            pass
