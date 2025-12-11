from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import google.genai as genai
from google.genai import types as genai_types
import yaml


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
        self.config_path = Path(
            config_path
            or Path(__file__).resolve().parents[1] / "configs" / "image_models.yaml"
        )
        self._adapter_overrides = adapter_overrides or {}
        self._config = config_data or self._load_config()

    def generate(self, options: ImageTaskOptions) -> ImageResult:
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

        errors: List[Tuple[str, Exception]] = []
        for model_key in candidates:
            model_conf = self._config.get("models", {}).get(model_key)
            if not model_conf:
                errors.append((model_key, ImageGenerationError(f"Model '{model_key}' not found")))
                continue

            capabilities = model_conf.get("capabilities", {})
            resolved = self._normalize_options(options, task_conf.get("defaults", {}), capabilities)
            try:
                adapter = self._get_adapter(model_key, model_conf)
                return adapter.generate(model_conf, resolved)
            except Exception as exc:  # noqa: BLE001
                errors.append((model_key, exc))
                logging.warning("ImageClient: %s failed for %s (%s)", model_key, options.task, exc)
                continue

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

        raise ImageGenerationError(f"Unsupported image provider: {provider}")


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
            Path(__file__).resolve().parents[1] / ".env",              # project root (/factory_commentary/.env)
            Path(__file__).resolve().parents[2] / ".env",              # parent root fallback
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
