from __future__ import annotations

import base64
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import time

import google.genai as genai
from google.genai import types as genai_types
import requests
import yaml

from factory_common import paths as repo_paths


class ImageGenerationError(Exception):
    """Raised when image generation fails or returns no usable data."""


class ImageProviderRateLimitError(ImageGenerationError):
    """Raised when provider indicates quota / rate limit exhaustion."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: int,
        retry_after_sec: Optional[int] = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.http_status = status_code
        self.retry_after_sec = retry_after_sec


_COOLDOWN_LOCK = threading.Lock()
_COOLDOWN_UNTIL_BY_PROVIDER: Dict[str, float] = {}
_COOLDOWN_FILE_MTIME: Optional[float] = None
_COOLDOWN_PATH: Optional[Path] = None


def _cooldown_state_path() -> Path:
    env_path = (os.getenv("IMAGE_CLIENT_COOLDOWN_PATH") or "").strip()
    if env_path:
        return Path(env_path)
    return repo_paths.logs_root() / "image_provider_cooldowns.json"


def _refresh_cooldowns_from_disk() -> None:
    global _COOLDOWN_FILE_MTIME, _COOLDOWN_PATH
    path = _cooldown_state_path()
    if _COOLDOWN_PATH is None or path != _COOLDOWN_PATH:
        _COOLDOWN_PATH = path
        _COOLDOWN_FILE_MTIME = None
        _COOLDOWN_UNTIL_BY_PROVIDER.clear()

    try:
        stat = path.stat()
    except FileNotFoundError:
        _COOLDOWN_FILE_MTIME = None
        _COOLDOWN_UNTIL_BY_PROVIDER.clear()
        return
    except Exception:
        return

    mtime = float(stat.st_mtime)
    if _COOLDOWN_FILE_MTIME is not None and mtime <= _COOLDOWN_FILE_MTIME:
        return

    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return

    if not isinstance(raw, dict):
        return

    now = time.time()
    next_state: Dict[str, float] = {}
    for provider, until in raw.items():
        try:
            p = str(provider)
            ts = float(until)
        except Exception:
            continue
        if ts > now:
            next_state[p] = ts

    _COOLDOWN_UNTIL_BY_PROVIDER.clear()
    _COOLDOWN_UNTIL_BY_PROVIDER.update(next_state)
    _COOLDOWN_FILE_MTIME = mtime


def _persist_cooldowns_to_disk() -> None:
    path = _cooldown_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(_COOLDOWN_UNTIL_BY_PROVIDER, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except Exception:
        # Fail-soft: cooldown persistence should never break generation.
        return


def _provider_cooldown_until(provider: str) -> Optional[float]:
    if not provider:
        return None
    with _COOLDOWN_LOCK:
        _refresh_cooldowns_from_disk()
        until = _COOLDOWN_UNTIL_BY_PROVIDER.get(provider)
        if until is None:
            return None
        if until <= time.time():
            _COOLDOWN_UNTIL_BY_PROVIDER.pop(provider, None)
            _persist_cooldowns_to_disk()
            return None
        return until


def _set_provider_cooldown(provider: str, *, cooldown_sec: int) -> None:
    if not provider or cooldown_sec <= 0:
        return
    now = time.time()
    until = now + float(cooldown_sec)
    with _COOLDOWN_LOCK:
        _refresh_cooldowns_from_disk()
        prev = _COOLDOWN_UNTIL_BY_PROVIDER.get(provider)
        if prev is None or until > prev:
            _COOLDOWN_UNTIL_BY_PROVIDER[provider] = until
            _persist_cooldowns_to_disk()


def _extract_http_status(exc: Exception) -> Optional[int]:
    for attr in ("http_status", "status_code", "status"):
        if hasattr(exc, attr):
            try:
                return int(getattr(exc, attr))
            except Exception:
                pass
    resp = getattr(exc, "response", None)
    if resp is not None:
        for attr in ("status_code", "status"):
            if hasattr(resp, attr):
                try:
                    return int(getattr(resp, attr))
                except Exception:
                    pass
    return None


def _looks_like_quota_limit(exc: Exception, status: Optional[int]) -> bool:
    if status in {402, 429}:
        return True
    msg = str(exc)
    upper = msg.upper()
    lower = msg.lower()
    if "RESOURCE_EXHAUSTED" in upper:
        return True
    if "quota" in lower or "rate limit" in lower or "too many requests" in lower:
        return True
    return False


def _cooldown_seconds_for_quota(exc: Exception, status: Optional[int]) -> int:
    retry_after = getattr(exc, "retry_after_sec", None)
    if isinstance(retry_after, (int, float)) and retry_after > 0:
        return int(retry_after)

    if status == 429:
        raw = (os.getenv("IMAGE_CLIENT_COOLDOWN_429_SEC") or "").strip()
        if raw.isdigit():
            return max(1, int(raw))
        return 30
    if status == 402:
        raw = (os.getenv("IMAGE_CLIENT_COOLDOWN_402_SEC") or "").strip()
        if raw.isdigit():
            return max(1, int(raw))
        return 600

    raw = (os.getenv("IMAGE_CLIENT_COOLDOWN_QUOTA_SEC") or "").strip()
    if raw.isdigit():
        return max(1, int(raw))
    return 60

@dataclass
class ImageTaskOptions:
    task: str
    prompt: str
    aspect_ratio: str | None = None
    size: str | None = None
    n: int = 1
    seed: int | None = None
    negative_prompt: str | None = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageResult:
    images: List[bytes]
    provider: str
    model: str
    request_id: str | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class ImageClient:
    """Task/tier based image generation client.

    The client reads `configs/image_models.yaml` to resolve tasks to tiers, tiers to
    models, and then normalizes the provided options based on model capabilities.
    Currently supports Gemini image generation; additional adapters can be added
    by extending `_get_adapter`.
    """

    def __init__(
        self,
        config_path: str | Path | None = None,
        config_data: Dict[str, Any] | None = None,
        adapter_overrides: Dict[str, Any] | None = None,
    ):
        root = repo_paths.repo_root()
        self.config_path = Path(config_path) if config_path else (root / "configs" / "image_models.yaml")
        self._adapter_overrides = adapter_overrides or {}
        self._config = config_data or self._load_config()

    def generate(self, options: ImageTaskOptions) -> ImageResult:
        started_at = time.perf_counter()
        task_conf = self._config.get("tasks", {}).get(options.task)
        if not task_conf:
            raise ImageGenerationError(
                f"Task '{options.task}' not found in image model configuration"
            )

        tier_name = task_conf.get("tier")
        if not tier_name:
            raise ImageGenerationError(f"Tier is not defined for task '{options.task}'")

        candidates = self._config.get("tiers", {}).get(tier_name)
        if not candidates:
            raise ImageGenerationError(f"No tier candidates found for tier '{tier_name}'")

        forced_model_key: Optional[str] = None
        allow_fallback = True
        if isinstance(options.extra, dict):
            raw_forced = options.extra.get("model_key")
            if isinstance(raw_forced, str) and raw_forced.strip():
                forced_model_key = raw_forced.strip()
            raw_allow_fallback = options.extra.get("allow_fallback")
            if raw_allow_fallback is not None:
                allow_fallback = bool(raw_allow_fallback)

        errors: List[Tuple[str, Exception]] = []
        if forced_model_key:
            forced_conf = self._config.get("models", {}).get(forced_model_key)
            if not forced_conf:
                raise ImageGenerationError(f"Forced model '{forced_model_key}' not found in image model configuration")

            candidate_keys: List[str] = [forced_model_key]
            if allow_fallback and isinstance(candidates, list):
                for key in candidates:
                    if isinstance(key, str) and key not in candidate_keys:
                        candidate_keys.append(key)

            for attempt_idx, model_key in enumerate(candidate_keys):
                model_conf = self._config.get("models", {}).get(model_key)
                if not model_conf:
                    errors.append((model_key, ImageGenerationError(f"Model '{model_key}' not found")))
                    continue

                provider_name = str(model_conf.get("provider") or "").strip()
                cooldown_until = _provider_cooldown_until(provider_name)
                if cooldown_until is not None:
                    remaining = max(1, int(round(cooldown_until - time.time())))
                    errors.append(
                        (
                            model_key,
                            ImageGenerationError(
                                f"Provider '{provider_name}' is in cooldown for ~{remaining}s (quota/rate limit protection)"
                            ),
                        )
                    )
                    continue

                capabilities = model_conf.get("capabilities", {})
                resolved = self._normalize_options(options, task_conf.get("defaults", {}), capabilities)
                max_attempts = int(task_conf.get("retries_per_model", 1)) + 1
                for sub_attempt in range(max_attempts):
                    try:
                        adapter = self._get_adapter(model_key, model_conf)
                        result = adapter.generate(model_conf, resolved)
                        duration_ms = int((time.perf_counter() - started_at) * 1000)
                        self._log_usage(
                            success=True,
                            task=options.task,
                            tier=tier_name,
                            model_key=model_key,
                            provider=model_conf.get("provider"),
                            request_id=result.request_id,
                            duration_ms=duration_ms,
                            prompt_hash=self._hash_prompt(options.prompt),
                            attempt=attempt_idx + 1,
                        )
                        return result
                    except Exception as exc:  # noqa: BLE001
                        errors.append((model_key, exc))
                        logging.warning(
                            "ImageClient: model %s failed for %s (attempt %d/%d, %s)",
                            model_key,
                            options.task,
                            sub_attempt + 1,
                            max_attempts,
                            exc,
                        )
                        status = _extract_http_status(exc)
                        if _looks_like_quota_limit(exc, status):
                            cooldown_sec = _cooldown_seconds_for_quota(exc, status)
                            _set_provider_cooldown(provider_name, cooldown_sec=cooldown_sec)
                            break
                        if sub_attempt + 1 >= max_attempts:
                            break
                        time.sleep(0.25)

            duration_ms = int((time.perf_counter() - started_at) * 1000)
            self._log_usage(
                success=False,
                task=options.task,
                tier=tier_name,
                model_key=forced_model_key,
                provider=forced_conf.get("provider"),
                request_id=None,
                duration_ms=duration_ms,
                prompt_hash=self._hash_prompt(options.prompt),
                errors=[{"model": k, "error": str(e)} for k, e in errors],
                attempt=1,
            )
            raise ImageGenerationError(
                f"Image generation failed for task '{options.task}' (requested model: '{forced_model_key}'): "
                + "; ".join([f"{k}: {e}" for k, e in errors])
            )

        for attempt_idx, model_key in enumerate(self._rotate_candidates(tier_name, candidates)):
            model_conf = self._config.get("models", {}).get(model_key)
            if not model_conf:
                errors.append((model_key, ImageGenerationError(f"Model '{model_key}' not found")))
                continue

            provider_name = str(model_conf.get("provider") or "").strip()
            cooldown_until = _provider_cooldown_until(provider_name)
            if cooldown_until is not None:
                remaining = max(1, int(round(cooldown_until - time.time())))
                errors.append(
                    (
                        model_key,
                        ImageGenerationError(
                            f"Provider '{provider_name}' is in cooldown for ~{remaining}s (quota/rate limit protection)"
                        ),
                    )
                )
                continue

            capabilities = model_conf.get("capabilities", {})
            resolved = self._normalize_options(options, task_conf.get("defaults", {}), capabilities)
            max_attempts = int(task_conf.get("retries_per_model", 1)) + 1
            for sub_attempt in range(max_attempts):
                try:
                    adapter = self._get_adapter(model_key, model_conf)
                    result = adapter.generate(model_conf, resolved)
                    duration_ms = int((time.perf_counter() - started_at) * 1000)
                    # round-robin: next call starts after the successful model
                    self._persist_round_robin_index(tier_name, model_key, candidates)
                    self._log_usage(
                        success=True,
                        task=options.task,
                        tier=tier_name,
                        model_key=model_key,
                        provider=model_conf.get("provider"),
                        request_id=result.request_id,
                        duration_ms=duration_ms,
                        prompt_hash=self._hash_prompt(options.prompt),
                        attempt=attempt_idx + 1,
                    )
                    return result
                except Exception as exc:  # noqa: BLE001
                    errors.append((model_key, exc))
                    logging.warning(
                        "ImageClient: %s failed for %s (attempt %d/%d, %s)",
                        model_key,
                        options.task,
                        sub_attempt + 1,
                        max_attempts,
                        exc,
                    )
                    status = _extract_http_status(exc)
                    if _looks_like_quota_limit(exc, status):
                        cooldown_sec = _cooldown_seconds_for_quota(exc, status)
                        _set_provider_cooldown(provider_name, cooldown_sec=cooldown_sec)
                        break
                    # last attempt for this model: break to next candidate
                    if sub_attempt + 1 >= max_attempts:
                        break
                    time.sleep(0.25)

        duration_ms = int((time.perf_counter() - started_at) * 1000)
        self._log_usage(
            success=False,
            task=options.task,
            tier=tier_name,
            model_key=None,
            provider=None,
            request_id=None,
            duration_ms=duration_ms,
            prompt_hash=self._hash_prompt(options.prompt),
            errors=[{"model": k, "error": str(e)} for k, e in errors],
        )
        raise ImageGenerationError(
            f"All image models failed for task '{options.task}': "
            + "; ".join([f"{k}: {e}" for k, e in errors])
        )

    def _load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            raise ImageGenerationError(f"Image model config not found: {self.config_path}")

        with self.config_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data

    def _normalize_options(
        self,
        options: ImageTaskOptions,
        defaults: Dict[str, Any],
        capabilities: Dict[str, Any],
    ) -> ImageTaskOptions:
        merged: Dict[str, Any] = {**defaults}

        dropped: Dict[str, Any] = {}

        for field_name in [
            "aspect_ratio",
            "size",
            "n",
            "seed",
            "negative_prompt",
        ]:
            value = getattr(options, field_name)
            if value is not None:
                merged[field_name] = value

        merged["n"] = max(1, int(merged.get("n", 1)))

        if not capabilities.get("supports_aspect_ratio", True):
            if "aspect_ratio" in merged:
                dropped["aspect_ratio"] = merged.pop("aspect_ratio")

        if not capabilities.get("supports_size", True):
            if "size" in merged:
                dropped["size"] = merged.pop("size")

        if not capabilities.get("supports_negative_prompt", True):
            if "negative_prompt" in merged:
                dropped["negative_prompt"] = merged.pop("negative_prompt")

        if not capabilities.get("supports_seed", True):
            if "seed" in merged:
                dropped["seed"] = merged.pop("seed")

        if dropped:
            logging.debug("ImageClient: dropped unsupported params for model (%s)", dropped)

        return ImageTaskOptions(
            task=options.task,
            prompt=options.prompt,
            aspect_ratio=merged.get("aspect_ratio"),
            size=merged.get("size"),
            n=merged.get("n", 1),
            seed=merged.get("seed"),
            negative_prompt=merged.get("negative_prompt"),
            extra=options.extra,
        )

    def _get_adapter(self, model_key: str, model_conf: Dict[str, Any]):
        if model_key in self._adapter_overrides:
            return self._adapter_overrides[model_key]
        provider = model_conf.get("provider")
        if provider == "gemini":
            return GeminiImageAdapter(self._config.get("providers", {}))
        if provider == "openrouter":
            return OpenRouterImageAdapter(self._config.get("providers", {}))

        raise ImageGenerationError(f"Unsupported image provider: {provider}")

    @staticmethod
    def _hash_prompt(prompt: str) -> str:
        """Hash prompt to avoid logging raw text."""
        return hashlib.sha256(prompt.encode("utf-8")).hexdigest()

    def _log_usage(
        self,
        *,
        success: bool,
            task: str,
            tier: str,
            model_key: Optional[str],
            provider: Optional[str],
            request_id: Optional[str],
            duration_ms: int,
            prompt_hash: str,
            errors: Optional[List[Dict[str, str]]] = None,
            attempt: Optional[int] = None,
        ) -> None:
        log_path_env = os.getenv("IMAGE_CLIENT_USAGE_LOG", "").strip()
        log_path = Path(log_path_env) if log_path_env else repo_paths.logs_root() / "image_usage.log"
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "success": success,
                "task": task,
                "tier": tier,
                "model": model_key,
                "provider": provider,
                "request_id": request_id,
                "duration_ms": duration_ms,
                "prompt_sha256": prompt_hash,
            }
            if attempt is not None:
                payload["attempt"] = attempt
            if errors:
                payload["errors"] = errors
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except Exception as exc:  # pragma: no cover - logging must not break generation
            logging.debug("ImageClient: failed to write usage log (%s)", exc)

    # ---- round-robin helpers -------------------------------------------------
    @property
    def _rr_state_path(self) -> Path:
        return repo_paths.logs_root() / "image_rr_state.json"

    def _rotate_candidates(self, tier_name: str, candidates: List[str]) -> List[str]:
        """
        Round-robin starting point per tier to avoid pinning to the first model.
        Falls back to the original order if state cannot be read.
        """
        if not candidates:
            return candidates
        try:
            state = {}
            if self._rr_state_path.exists():
                state = json.loads(self._rr_state_path.read_text(encoding="utf-8") or "{}")
            idx = int(state.get(tier_name, 0)) % len(candidates)
            return candidates[idx:] + candidates[:idx]
        except Exception:
            return candidates

    def _persist_round_robin_index(self, tier_name: str, model_key: str, candidates: List[str]) -> None:
        """
        After a success, advance the starting index so the next call tries the following model.
        """
        if not candidates:
            return
        try:
            self._rr_state_path.parent.mkdir(parents=True, exist_ok=True)
            state = {}
            if self._rr_state_path.exists():
                state = json.loads(self._rr_state_path.read_text(encoding="utf-8") or "{}")
            if model_key in candidates:
                next_idx = (candidates.index(model_key) + 1) % len(candidates)
                state[tier_name] = next_idx
                self._rr_state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            # state persistence failures must not break generation
            logging.debug("ImageClient: failed to persist RR state", exc_info=True)


class GeminiImageAdapter:
    def __init__(self, provider_conf: Dict[str, Any]):
        self.provider_conf = provider_conf.get("gemini", {})
        api_key_env = self.provider_conf.get("env_api_key", "")
        api_key = self._resolve_api_key(api_key_env)

        if not api_key:
            raise ImageGenerationError(
                f"Gemini API key not found. Please set environment variable '{api_key_env}'."
            )

        self.client = genai.Client(api_key=api_key)

    @staticmethod
    def _resolve_api_key(env_name: str) -> Optional[str]:
        """
        Attempt to resolve API key from environment; if missing, load common .env locations.
        This reduces friction when shells do not preload .env.
        """
        if not env_name:
            return None

        key = os.getenv(env_name)
        if key:
            return key

        candidates = [
            repo_paths.repo_root() / ".env",              # project root (/factory_commentary/.env)
            repo_paths.repo_root().parent / ".env",       # parent root fallback
            Path.home() / ".env",                                     # user home
        ]
        for env_path in candidates:
            if not env_path.exists():
                continue
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    if line.strip().startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    if k.strip() == env_name:
                        val = v.strip().strip("\"'")
                        os.environ.setdefault(env_name, val)
                        return val
            except Exception:
                continue
        return os.getenv(env_name)

    def generate(
        self, model_conf: Dict[str, Any], options: ImageTaskOptions
    ) -> ImageResult:
        model_name = model_conf.get("model_name")
        if not model_name:
            raise ImageGenerationError("Gemini model name is missing from configuration")

        images: List[bytes] = []
        metadata: Dict[str, Any] = {
            "aspect_ratio": options.aspect_ratio,
            "image_size": options.size,
            "seed": options.seed,
            "negative_prompt": options.negative_prompt,
            "n": options.n,
        }
        # NOTE: Gemini 2.5 image API (2025-12) returns image bytes via generate_content.
        # - generate_images is not available for model=gemini-2.5-flash-image (404 on this SDK).
        # - response_modalities / aspect_ratio / image_size parameters cause server errors, so
        #   we keep the minimal call and extract inline_data parts.
        try:
            response = self.client.models.generate_content(
                model=model_name,
                contents=[options.prompt],
                # API は aspect_ratio/size を受け付けないため送らない
            )
        except Exception as e:  # pragma: no cover
            raise ImageGenerationError(str(e)) from e

        request_id: str | None = getattr(response, "response_id", None)
        images.extend(self._extract_images(response))

        if not images:
            raise ImageGenerationError("Gemini response did not return any image data")

        return ImageResult(
            images=images,
            provider="gemini",
            model=model_name,
            request_id=request_id,
            metadata=metadata,
        )

    @staticmethod
    def _extract_images(response: Any) -> List[bytes]:
        extracted: List[bytes] = []
        candidates = getattr(response, "candidates", []) or []
        for cand in candidates:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", []) if content else []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                if not inline:
                    continue
                data = getattr(inline, "data", None)
                if isinstance(data, str):
                    extracted.append(base64.b64decode(data))
                elif isinstance(data, (bytes, bytearray)):
                    extracted.append(bytes(data))
                else:
                    logging.warning("Unexpected image payload type from Gemini: %s", type(data))

        return extracted


class OpenRouterImageAdapter:
    def __init__(self, provider_conf: Dict[str, Any]):
        conf = provider_conf.get("openrouter", {}) or {}
        if not isinstance(conf, dict):
            conf = {}
        self.provider_conf = conf
        api_key_env = str(self.provider_conf.get("env_api_key") or "OPENROUTER_API_KEY")
        api_key = GeminiImageAdapter._resolve_api_key(api_key_env)
        if not api_key:
            raise ImageGenerationError(
                f"OpenRouter API key not found. Please set environment variable '{api_key_env}'."
            )
        self.api_key = api_key
        self.base_url = str(self.provider_conf.get("base_url") or "https://openrouter.ai/api/v1").rstrip("/")

    def generate(self, model_conf: Dict[str, Any], options: ImageTaskOptions) -> ImageResult:
        model_name = model_conf.get("model_name")
        if not model_name:
            raise ImageGenerationError("OpenRouter model name is missing from configuration")

        timeout_sec = 120
        if isinstance(options.extra, dict):
            raw_timeout = options.extra.get("timeout_sec")
            if isinstance(raw_timeout, (int, float)) and raw_timeout > 0:
                timeout_sec = int(raw_timeout)

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": "user", "content": options.prompt}],
            "modalities": ["image", "text"],
        }
        if options.aspect_ratio:
            payload["image_config"] = {"aspect_ratio": options.aspect_ratio}
        if options.size:
            payload["size"] = options.size
        if options.seed is not None:
            payload["seed"] = options.seed
        if options.negative_prompt:
            payload["negative_prompt"] = options.negative_prompt

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout_sec)
        except requests.RequestException as exc:  # pragma: no cover - network faults
            raise ImageGenerationError(f"OpenRouter request failed: {exc}") from exc

        request_id: Optional[str] = None
        try:
            data = resp.json()
        except ValueError:  # pragma: no cover - unexpected non-json
            data = None

        if resp.status_code >= 400:
            detail = None
            if isinstance(data, dict):
                detail = data.get("error") or data
            if detail is None:
                detail = (resp.text or "").strip()
            retry_after_sec: Optional[int] = None
            retry_after_header = resp.headers.get("Retry-After")
            if retry_after_header:
                try:
                    retry_after_sec = int(float(str(retry_after_header).strip()))
                except Exception:
                    retry_after_sec = None

            msg = f"OpenRouter error {resp.status_code}: {detail}"
            if resp.status_code in (402, 429):
                raise ImageProviderRateLimitError(
                    msg,
                    provider="openrouter",
                    status_code=int(resp.status_code),
                    retry_after_sec=retry_after_sec,
                )
            raise ImageGenerationError(msg)

        if not isinstance(data, dict):
            raise ImageGenerationError("OpenRouter returned invalid response payload")

        request_id = str(data.get("id") or "").strip() or None
        images = self._extract_images(data, timeout_sec=timeout_sec)
        if not images:
            raise ImageGenerationError("OpenRouter response did not return any image data")

        return ImageResult(
            images=images,
            provider="openrouter",
            model=str(model_name),
            request_id=request_id,
            metadata={
                "n": len(images),
                "aspect_ratio": options.aspect_ratio,
                "size": options.size,
                "seed": options.seed,
                "negative_prompt": options.negative_prompt,
            },
        )

    @staticmethod
    def _extract_images(payload: Dict[str, Any], *, timeout_sec: int) -> List[bytes]:
        extracted: List[bytes] = []
        choices = payload.get("choices") or []
        if not isinstance(choices, list):
            return extracted
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            if not isinstance(message, dict):
                continue
            images = message.get("images") or []
            if not isinstance(images, list):
                continue
            for image in images:
                if not isinstance(image, dict):
                    continue
                image_url = image.get("image_url") or {}
                if not isinstance(image_url, dict):
                    continue
                url = image_url.get("url")
                if not isinstance(url, str) or not url:
                    continue
                data = OpenRouterImageAdapter._decode_image_url(url, timeout_sec=timeout_sec)
                if data:
                    extracted.append(data)
        return extracted

    @staticmethod
    def _decode_image_url(url: str, *, timeout_sec: int) -> Optional[bytes]:
        trimmed = url.strip()
        if not trimmed:
            return None
        if trimmed.startswith("data:"):
            try:
                header, b64_data = trimmed.split(",", 1)
            except ValueError:
                return None
            if ";base64" not in header:
                return None
            try:
                return base64.b64decode(b64_data)
            except Exception:
                return None

        # Some providers return presigned HTTPS URLs. Fetch them when possible.
        if trimmed.startswith("http://") or trimmed.startswith("https://"):
            try:
                resp = requests.get(trimmed, timeout=min(30, timeout_sec))
                resp.raise_for_status()
                return resp.content
            except Exception:
                return None

        return None
