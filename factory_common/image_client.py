from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import google.genai as genai
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

    def __init__(self, config_path: str | Path | None = None):
        self.config_path = Path(
            config_path
            or Path(__file__).resolve().parents[1] / "configs" / "image_models.yaml"
        )
        self._config = self._load_config()

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

        model_key = candidates[0]
        model_conf = self._config.get("models", {}).get(model_key)
        if not model_conf:
            raise ImageGenerationError(f"Model '{model_key}' not found in configuration")

        capabilities = model_conf.get("capabilities", {})
        resolved = self._normalize_options(options, task_conf.get("defaults", {}), capabilities)
        adapter = self._get_adapter(model_conf)

        return adapter.generate(model_conf, resolved)

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
            merged.pop("aspect_ratio", None)

        if not capabilities.get("supports_size", True):
            merged.pop("size", None)

        if not capabilities.get("supports_negative_prompt", True):
            merged.pop("negative_prompt", None)

        if not capabilities.get("supports_seed", True):
            merged.pop("seed", None)

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

    def _get_adapter(self, model_conf: Dict[str, Any]):
        provider = model_conf.get("provider")
        if provider == "gemini":
            return GeminiImageAdapter(self._config.get("providers", {}))

        raise ImageGenerationError(f"Unsupported image provider: {provider}")


class GeminiImageAdapter:
    def __init__(self, provider_conf: Dict[str, Any]):
        self.provider_conf = provider_conf.get("gemini", {})
        api_key_env = self.provider_conf.get("env_api_key", "")
        api_key = os.getenv(api_key_env)

        if not api_key:
            raise ImageGenerationError(
                f"Gemini API key not found. Please set environment variable '{api_key_env}'."
            )

        genai.configure(api_key=api_key)

    def generate(
        self, model_conf: Dict[str, Any], options: ImageTaskOptions
    ) -> ImageResult:
        model_name = model_conf.get("model_name")
        if not model_name:
            raise ImageGenerationError("Gemini model name is missing from configuration")

        model = genai.GenerativeModel(model_name)
        images: List[bytes] = []
        metadata: Dict[str, Any] = {
            "aspect_ratio": options.aspect_ratio,
            "size": options.size,
            "seed": options.seed,
            "negative_prompt": options.negative_prompt,
        }

        generation_config: Dict[str, Any] = {}
        if options.aspect_ratio:
            generation_config["aspect_ratio"] = options.aspect_ratio
        if options.size:
            generation_config["size"] = options.size
        if options.seed is not None:
            generation_config["seed"] = options.seed
        if options.negative_prompt:
            generation_config["negative_prompt"] = options.negative_prompt

        request_id: str | None = None
        for _ in range(options.n):
            response = model.generate_content(
                options.prompt, generation_config=generation_config or None
            )
            request_id = getattr(response, "request_id", request_id)
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
        for candidate in candidates:
            content = getattr(candidate, "content", None)
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

