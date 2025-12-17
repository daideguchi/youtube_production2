from __future__ import annotations

import base64
import json
import logging
import os
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
        if isinstance(options.extra, dict):
            raw_forced = options.extra.get("model_key")
            if isinstance(raw_forced, str) and raw_forced.strip():
                forced_model_key = raw_forced.strip()

        errors: List[Tuple[str, Exception]] = []
        if forced_model_key:
            model_conf = self._config.get("models", {}).get(forced_model_key)
            if not model_conf:
                raise ImageGenerationError(f"Forced model '{forced_model_key}' not found in image model configuration")

            capabilities = model_conf.get("capabilities", {})
            resolved = self._normalize_options(options, task_conf.get("defaults", {}), capabilities)
            max_attempts = int(task_conf.get("retries_per_model", 1)) + 1
            for sub_attempt in range(max_attempts):
                try:
                    adapter = self._get_adapter(forced_model_key, model_conf)
                    result = adapter.generate(model_conf, resolved)
                    duration_ms = int((time.perf_counter() - started_at) * 1000)
                    self._log_usage(
                        success=True,
                        task=options.task,
                        tier=tier_name,
                        model_key=forced_model_key,
                        provider=model_conf.get("provider"),
                        request_id=result.request_id,
                        duration_ms=duration_ms,
                        prompt_hash=self._hash_prompt(options.prompt),
                        attempt=1,
                    )
                    return result
                except Exception as exc:  # noqa: BLE001
                    errors.append((forced_model_key, exc))
                    logging.warning(
                        "ImageClient: forced model %s failed for %s (attempt %d/%d, %s)",
                        forced_model_key,
                        options.task,
                        sub_attempt + 1,
                        max_attempts,
                        exc,
                    )
                    if sub_attempt + 1 >= max_attempts:
                        break
                    time.sleep(0.25)

            duration_ms = int((time.perf_counter() - started_at) * 1000)
            self._log_usage(
                success=False,
                task=options.task,
                tier=tier_name,
                model_key=forced_model_key,
                provider=model_conf.get("provider"),
                request_id=None,
                duration_ms=duration_ms,
                prompt_hash=self._hash_prompt(options.prompt),
                errors=[{"model": k, "error": str(e)} for k, e in errors],
                attempt=1,
            )
            raise ImageGenerationError(
                f"Forced image model '{forced_model_key}' failed for task '{options.task}': "
                + "; ".join([f"{k}: {e}" for k, e in errors])
            )

        for attempt_idx, model_key in enumerate(self._rotate_candidates(tier_name, candidates)):
            model_conf = self._config.get("models", {}).get(model_key)
            if not model_conf:
                errors.append((model_key, ImageGenerationError(f"Model '{model_key}' not found")))
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
            raise ImageGenerationError(f"OpenRouter error {resp.status_code}: {detail}")

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
