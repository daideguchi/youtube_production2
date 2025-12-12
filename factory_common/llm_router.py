import os
import yaml
import time
import json
import logging
from typing import Dict, Any, List, Optional, Union
from pathlib import Path
from dotenv import load_dotenv

from factory_common.llm_param_guard import sanitize_params

DEFAULT_FALLBACK_POLICY = {
    "transient_statuses": [429, 500, 502, 503, 504, 408],
    "retry_limit": 0,  # 0 means try all
    "backoff_sec": 1.0,
    "per_status_backoff": {},
    "per_status_retry": {},
    "max_total_attempts": 0,
    "max_total_wait_sec": 0,
}
TRANSIENT_STATUSES = set(DEFAULT_FALLBACK_POLICY["transient_statuses"])

# HTTPステータスを例外から推測するための簡易ヘルパ
def _extract_status(exc: Exception) -> Optional[int]:
    for attr in ("http_status", "status_code", "status"):
        if hasattr(exc, attr):
            try:
                val = int(getattr(exc, attr))
                return val
            except Exception:
                continue
    resp = getattr(exc, "response", None)
    if resp is not None:
        for attr in ("status_code", "status"):
            if hasattr(resp, attr):
                try:
                    return int(getattr(resp, attr))
                except Exception:
                    continue
    return None


def _extract_request_id(result: Any) -> Optional[str]:
    for attr in ("id", "request_id"):
        if hasattr(result, attr):
            try:
                val = getattr(result, attr)
                if val:
                    return str(val)
            except Exception:
                continue
    resp = getattr(result, "response", None)
    if resp is not None:
        for attr in ("id", "request_id"):
            if hasattr(resp, attr):
                try:
                    val = getattr(resp, attr)
                    if val:
                        return str(val)
                except Exception:
                    continue
    return None
# Try importing OpenAI
try:
    from openai import OpenAI, AzureOpenAI
except ImportError:
    OpenAI = None
    AzureOpenAI = None

# Try importing Gemini
try:
    import google.generativeai as genai
except ImportError:
    genai = None

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LLMRouter")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "configs" / "llm_router.yaml"
FALLBACK_POLICY_PATH = PROJECT_ROOT / "configs" / "llm_fallback_policy.yaml"
DEFAULT_LOG_PATH = PROJECT_ROOT / "logs" / "llm_usage.jsonl"
TASK_OVERRIDE_PATH = PROJECT_ROOT / "configs" / "llm_task_overrides.yaml"
ENV_PATH = PROJECT_ROOT / ".env"

def _load_env_forced():
    """Load .env file and OVERWRITE existing env vars to ensure SSOT."""
    if ENV_PATH.exists():
        # Using python-dotenv with override=True
        load_dotenv(dotenv_path=ENV_PATH, override=True)
    else:
        logger.warning(f".env not found at {ENV_PATH}")

class LLMRouter:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(LLMRouter, cls).__new__(cls)
            cls._instance._initialized = False
            cls._instance.task_overrides = {}
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        
        _load_env_forced()
        self.config = self._load_config()
        self.fallback_policy = self._load_fallback_policy()
        self.task_overrides = self._load_task_overrides()
        self._setup_clients()
        if self.task_overrides is None:
            self.task_overrides = {}
        self._initialized = True

    def _load_config(self) -> Dict[str, Any]:
        if not CONFIG_PATH.exists():
            raise FileNotFoundError(f"Router config not found at {CONFIG_PATH}")
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _log_usage(self, payload: Dict[str, Any]) -> None:
        if os.getenv("LLM_ROUTER_LOG_DISABLE") == "1":
            return
        log_path = Path(os.getenv("LLM_ROUTER_LOG_PATH") or DEFAULT_LOG_PATH)
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug(f"LLM usage log write failed: {e}")

    def _load_fallback_policy(self) -> Dict[str, Any]:
        policy = DEFAULT_FALLBACK_POLICY.copy()
        if FALLBACK_POLICY_PATH.exists():
            try:
                loaded = yaml.safe_load(FALLBACK_POLICY_PATH.read_text())
                if isinstance(loaded, dict):
                    policy.update({k: loaded.get(k, v) for k, v in policy.items()})
            except Exception as e:
                logger.warning(f"Failed to load fallback policy; using defaults. Error: {e}")
        return policy

    def _load_task_overrides(self) -> Dict[str, Any]:
        overrides: Dict[str, Any] = {}
        if TASK_OVERRIDE_PATH.exists():
            try:
                loaded = yaml.safe_load(TASK_OVERRIDE_PATH.read_text()) or {}
                if isinstance(loaded, dict):
                    overrides = loaded.get("tasks", {})
            except Exception as e:
                logger.warning(f"Failed to load task overrides; ignoring. Error: {e}")
        return overrides

    def _extract_usage(self, result: Any) -> Dict[str, Any]:
        """
        Providerごとの usage 情報を抽出して dict を返す。
        未対応/取得不可の場合は空 dict。
        """
        usage = {}
        # OpenAI/Azure client returns .usage on ChatCompletion
        if hasattr(result, "usage"):
            try:
                usage = {
                    "prompt_tokens": getattr(result.usage, "prompt_tokens", None),
                    "completion_tokens": getattr(result.usage, "completion_tokens", None),
                    "total_tokens": getattr(result.usage, "total_tokens", None),
                }
            except Exception:
                pass
        # Gemini: result may have usage_metadata on the response
        if hasattr(result, "usage_metadata"):
            try:
                meta = result.usage_metadata
                usage.update({
                    "prompt_tokens": getattr(meta, "prompt_token_count", None),
                    "completion_tokens": getattr(meta, "candidates_token_count", None),
                    "total_tokens": getattr(meta, "total_token_count", None),
                })
            except Exception:
                pass
        # Remove None-only entries
        usage = {k: v for k, v in usage.items() if v is not None}
        return usage

    def _setup_clients(self):
        self.clients = {}
        providers = self.config.get("providers", {})

        # Azure
        if "azure" in providers:
            p = providers["azure"]
            ep = os.getenv(p.get("env_endpoint"))
            key = os.getenv(p.get("env_api_key"))
            ver = p.get("default_api_version")
            if ep and key and AzureOpenAI:
                # Handle missing protocol in endpoint if common
                if not ep.startswith("http"):
                    ep = "https://" + ep
                
                # Fix: Strip trailing paths from endpoint for SDK
                # The AzureOpenAI client expects the base endpoint (e.g. https://foo.openai.azure.com/)
                # It appends /openai/deployments/... itself.
                # If users put full path in .env, we should clean it.
                if "/openai/" in ep:
                    ep = ep.split("/openai/")[0]
                
                self.clients["azure"] = AzureOpenAI(
                    api_key=key,
                    api_version=ver,
                    azure_endpoint=ep
                )

        # OpenRouter
        if "openrouter" in providers:
            p = providers["openrouter"]
            key = os.getenv(p.get("env_api_key"))
            base = p.get("base_url")
            if key and OpenAI:
                self.clients["openrouter"] = OpenAI(
                    api_key=key,
                    base_url=base
                )

        # Gemini
        if "gemini" in providers:
            p = providers["gemini"]
            key = os.getenv(p.get("env_api_key"))
            if key and genai:
                genai.configure(api_key=key)
                self.clients["gemini"] = "configured" # Client is static

    def get_models_for_task(self, task: str) -> List[str]:
        task_conf = self.config.get("tasks", {}).get(task, {})
        override_conf = self.task_overrides.get(task, {}) if hasattr(self, "task_overrides") else {}
        tier = override_conf.get("tier") or task_conf.get("tier") or "standard"
        
        tier_models = []
        # explicit models override wins
        if override_conf.get("models"):
            tier_models = override_conf["models"]
        else:
            # base tier models
            tier_models = self.config.get("tiers", {}).get(tier, [])
            # Allow tier override from llm_tier_candidates.yaml if present
            candidates_path = CONFIG_PATH.parents[0] / "llm_tier_candidates.yaml"
            if candidates_path.exists():
                try:
                    candidates = yaml.safe_load(candidates_path.read_text()).get("tiers", {})
                    if tier in candidates and candidates[tier]:
                        tier_models = candidates[tier]
                except Exception as e:
                    logger.warning(f"Failed to load tier candidates override: {e}")
        return tier_models

    def call(
        self,
        task: str,
        messages: List[Dict[str, str]],
        system_prompt_override: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        **kwargs,
    ) -> Any:
        result = self._call_internal(
            task=task,
            messages=messages,
            system_prompt_override=system_prompt_override,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            return_raw=False,
            **kwargs,
        )
        return result["content"]

    def call_with_raw(
        self,
        task: str,
        messages: List[Dict[str, str]],
        system_prompt_override: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Like call(), but returns a dict with content, raw response, usage, request_id,
        model/provider, fallback chain, and latency_ms.
        """
        return self._call_internal(
            task=task,
            messages=messages,
            system_prompt_override=system_prompt_override,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            return_raw=True,
            **kwargs,
        )

    def _call_internal(
        self,
        task: str,
        messages: List[Dict[str, str]],
        system_prompt_override: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        response_format: Optional[str],
        return_raw: bool,
        **kwargs,
    ) -> Dict[str, Any]:

        models = self.get_models_for_task(task)
        if not models:
            raise ValueError(f"No models available for task: {task}")

        last_error = None
        last_status = None
        last_error_class = None

        task_conf = self.config.get("tasks", {}).get(task, {})
        override_conf = self.task_overrides.get(task, {}) if hasattr(self, "task_overrides") else {}

        # System Prompt Injection/Override logic (override > task_conf > existing)
        sp_override = system_prompt_override or override_conf.get("system_prompt_override") or task_conf.get("system_prompt_override")
        if sp_override:
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] = sp_override
            else:
                messages.insert(0, {"role": "system", "content": sp_override})

        # Consolidate options for param guard
        task_options = task_conf.get("options", {})
        override_options = override_conf.get("options", {})
        base_options = {
            **task_options,
            **override_options,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format,
            **kwargs,
        }

        tried = []
        total_wait = 0.0
        status_counts = {}
        for model_key in models:
            model_conf = self.config.get("models", {}).get(model_key)
            if not model_conf:
                continue

            provider_name = model_conf.get("provider")
            client = self.clients.get(provider_name)

            if not client:
                logger.debug(f"Client for {provider_name} not ready. Skipping {model_key}")
                continue

            try:
                safe_options = sanitize_params(model_conf, base_options)
                logger.info(f"Router: Invoking {model_key} for {task}...")
                start = time.time()
                raw_result = self._invoke_provider(
                    provider_name,
                    client,
                    model_conf,
                    messages,
                    return_raw=True,
                    **safe_options,
                )
                content = self._extract_content(provider_name, model_conf, raw_result)
                usage = self._extract_usage(raw_result)
                req_id = _extract_request_id(raw_result)
                latency_ms = int((time.time() - start) * 1000)
                chain = tried + [model_key]
                logger.info(
                    f"Router: {task} succeeded via {model_key} "
                    f"(fallback_chain={chain}, latency_ms={latency_ms}, usage={usage}, request_id={req_id})"
                )
                self._log_usage({
                    "status": "success",
                    "task": task,
                    "model": model_key,
                    "provider": provider_name,
                    "chain": chain,
                    "latency_ms": latency_ms,
                    "usage": usage,
                    "request_id": req_id,
                    "timestamp": time.time(),
                })
                return {
                    "content": content,
                    "raw": raw_result if return_raw else None,
                    "usage": usage,
                    "request_id": req_id,
                    "model": model_key,
                    "provider": provider_name,
                    "chain": chain,
                    "latency_ms": latency_ms,
                }
            except Exception as e:
                status = _extract_status(e)
                logger.warning(f"Failed to call {model_key}: {e} (status={status})")
                last_error = e
                last_status = status
                last_error_class = e.__class__.__name__
                tried.append(model_key)
                # Retry next candidate only for transient-ish statuses; otherwise fail fast
                transient_statuses = set(self.fallback_policy.get("transient_statuses", [])) or TRANSIENT_STATUSES
                backoff_sec = float(self.fallback_policy.get("backoff_sec", 1.0))
                retry_limit = int(self.fallback_policy.get("retry_limit", 0))
                max_attempts = int(self.fallback_policy.get("max_total_attempts", 0))
                max_wait = float(self.fallback_policy.get("max_total_wait_sec", 0))
                per_status_retry = self.fallback_policy.get("per_status_retry", {}) or {}

                if retry_limit and len(tried) >= retry_limit:
                    break

                if max_attempts and len(tried) >= max_attempts:
                    break

                if status not in transient_statuses | {None}:
                    break
                if status in transient_statuses:
                    if status is not None:
                        status_counts[status] = status_counts.get(status, 0) + 1
                        limit = int(per_status_retry.get(str(status), 0))
                        if limit and status_counts[status] >= limit:
                            break
                    per_status = self.fallback_policy.get("per_status_backoff", {}) or {}
                    sleep_for = float(per_status.get(str(status), backoff_sec))
                    if max_wait and (total_wait + sleep_for) > max_wait:
                        break
                    time.sleep(sleep_for)  # short backoff to avoid hammering provider
                    total_wait += sleep_for
                continue

        self._log_usage({
            "status": "fail",
            "task": task,
            "chain": tried,
            "error": str(last_error),
            "error_class": last_error_class,
            "status_code": last_status,
            "timestamp": time.time(),
        })
        raise RuntimeError(f"All models failed for task '{task}'. tried={tried} last_error={last_error}")

    def _invoke_provider(self, provider, client, model_conf, messages, return_raw: bool = False, **kwargs):
        cap = model_conf.get("capabilities", {})
        mode = cap.get("mode", "chat")
        
        # Merge defaults
        defaults = model_conf.get("defaults", {})
        params = {**defaults, **kwargs}
        
        # Params are already sanitized in call(); just forward with minimal mapping
        api_args = {}
        for k, v in params.items():
            if v is None:
                continue
            if k == "response_format" and v == "json_object":
                if cap.get("json_mode"):
                    api_args["response_format"] = {"type": "json_object"}
                else:
                    # モデルが JSON mode 未対応ならそのままプロンプトに任せる
                    pass
                continue
            api_args[k] = v

        # IMAGE GENERATION
        if mode == "image_generation":
            return self._invoke_image_gen(provider, client, model_conf, messages, **kwargs)

        # TEXT/CHAT
        model_name = model_conf.get("deployment") if provider == "azure" else model_conf.get("model_name")
        
        if provider == "azure":
             # Azure specific
             pass
        
        # Common OpenAI/Azure Interface
        if provider in ["azure", "openrouter"]:
            response = client.chat.completions.create(
                model=model_name,
                messages=messages,
                **api_args
            )
            return response if return_raw else response.choices[0].message.content

        # Gemini Chat (Not implemented fully in config yet for Text, but ready)
        if provider == "gemini":
            # Convert messages to Gemini format
            # This is complex, skipping for now as we don't use Gemini for text in this config
            raise NotImplementedError("Gemini Text not supported yet")

    @staticmethod
    def _extract_content(provider: str, model_conf: Dict[str, Any], raw_result: Any) -> Any:
        cap = model_conf.get("capabilities", {}) or {}
        mode = cap.get("mode", "chat")
        if mode == "image_generation":
            return raw_result
        if provider in ["azure", "openrouter"]:
            try:
                return raw_result.choices[0].message.content  # type: ignore[attr-defined]
            except Exception:
                pass
        return raw_result

    def _invoke_image_gen(self, provider, client, model_conf, messages, **kwargs):
        if provider == "gemini":
            # Extract prompt from messages
            # Usually the last user message
            prompt = ""
            for m in reversed(messages):
                if m["role"] == "user":
                    prompt = m["content"]
                    break

            if not prompt:
                raise ValueError("No prompt found for image generation")

            model_name = model_conf.get("model_name")

            # Use the configured genai client
            model = genai.GenerativeModel(model_name)

            # For image generation, just pass the prompt
            # Additional parameters will be handled by the model configuration
            try:
                response = model.generate_content(prompt)
                return response
            except Exception as e:
                logger.error(f"Error in Gemini image generation: {e}")
                raise
        else:
            raise NotImplementedError(f"Image generation not supported for provider: {provider}")

def get_router():
    return LLMRouter()
