from __future__ import annotations

import base64
import io
import json
import logging
import os
import inspect
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import time

try:
    import google.genai as genai
    from google.genai import types as genai_types
except ModuleNotFoundError:  # pragma: no cover - optional dependency
    genai = None
    genai_types = None
import requests
import yaml

from factory_common import paths as repo_paths
from factory_common import fireworks_keys
from factory_common.routing_lockdown import lockdown_active

IMAGE_MODEL_KEY_BLOCKLIST = {
    # Policy: Gemini 3 image models are forbidden for video images (visual_image_gen).
    "gemini_3_pro_image_preview",
    "openrouter_gemini_3_pro_image_preview",
}
IMAGE_MODEL_KEY_BLOCKLIST_TASKS = {
    # Video images (SRT→images). Thumbnails are allowed to use Gemini 3 when explicitly selected.
    "visual_image_gen",
    "image_generation",
}

IMAGE_TRACE_SCHEMA_V1 = "ytm.image_trace.v1"
def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep-merge dicts (override wins), keeping base keys when override is partial.

    Used for `.local` config overlays to avoid drift when only a small subset is customized.
    """
    out: Dict[str, Any] = dict(base or {})
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge_dict(out.get(k) or {}, v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def _trace_image_disabled() -> bool:
    val = (os.getenv("YTM_TRACE_IMAGE") or "").strip().lower()
    if val in ("0", "false", "no", "off"):
        return True
    # Allow global switch via YTM_TRACE_LLM=0 (single knob).
    val2 = (os.getenv("YTM_TRACE_LLM") or "").strip().lower()
    return val2 in ("0", "false", "no", "off")


def _trace_key() -> str:
    return (os.getenv("LLM_ROUTING_KEY") or os.getenv("YTM_TRACE_KEY") or "").strip()


def _safe_trace_key(key: str) -> str:
    import re

    k = (key or "").strip()
    if not k:
        return ""
    return re.sub(r"[^A-Za-z0-9_.\\-]+", "_", k)[:180]


def _repo_relpath_str(path: str) -> str:
    try:
        p = Path(path).expanduser().resolve()
        return str(p.relative_to(repo_paths.repo_root()))
    except Exception:
        return str(path or "")


def _resolve_callsite() -> Dict[str, Any] | None:
    try:
        fr = inspect.currentframe()
        if fr is None:
            return None
        cur = fr
        for _ in range(0, 64):
            cur = cur.f_back  # type: ignore[assignment]
            if cur is None:
                return None
            filename = cur.f_code.co_filename
            if not filename.endswith("image_client.py"):
                return {
                    "path": _repo_relpath_str(filename),
                    "line": int(cur.f_lineno),
                    "function": str(cur.f_code.co_name),
                }
    except Exception:
        return None
    return None


def _append_image_trace_event(event: Dict[str, Any]) -> None:
    if _trace_image_disabled():
        return
    key = _trace_key()
    always = (os.getenv("YTM_TRACE_IMAGE_ALWAYS") or "").strip().lower() in ("1", "true", "yes", "on")
    if not key and not always:
        return

    safe_key = _safe_trace_key(key) if key else "_global"
    try:
        out_dir = repo_paths.logs_root() / "traces" / "image"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{safe_key}.jsonl"
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _trace_image_call(
    *,
    task: str,
    tier: str,
    model_key: str,
    provider: str | None,
    request_id: str | None,
    duration_ms: int | None,
    options: "ImageTaskOptions",
) -> None:
    try:
        event: Dict[str, Any] = {
            "schema": IMAGE_TRACE_SCHEMA_V1,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "trace_key": _trace_key() or None,
            "task": str(task or ""),
            "tier": str(tier or ""),
            "model_key": str(model_key or ""),
            "provider": provider,
            "request_id": request_id,
            "duration_ms": int(duration_ms) if duration_ms is not None else None,
            "callsite": _resolve_callsite(),
            "input": {
                "prompt": options.prompt,
                "negative_prompt": getattr(options, "negative_prompt", None),
                "seed": getattr(options, "seed", None),
                "n": getattr(options, "n", None),
                "size": getattr(options, "size", None),
                "aspect_ratio": getattr(options, "aspect_ratio", None),
                "input_images": getattr(options, "input_images", None),
            },
        }
        _append_image_trace_event(event)
    except Exception:
        pass


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
    # Optional reference images (local file paths). Used by providers/models that support
    # multimodal chat inputs for image generation.
    input_images: List[str] | None = None
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
        default_config = root / "configs" / "image_models.yaml"
        local_config = root / "configs" / "image_models.local.yaml"

        resolved_config: Path
        if config_path is not None:
            resolved_config = Path(config_path)
            if not resolved_config.is_absolute():
                resolved_config = root / resolved_config
        else:
            env_config = (os.getenv("IMAGE_CLIENT_CONFIG_PATH") or "").strip()
            if env_config:
                resolved_config = Path(env_config)
                if not resolved_config.is_absolute():
                    resolved_config = root / resolved_config
            elif local_config.exists():
                resolved_config = local_config
            else:
                resolved_config = default_config

        self.config_path = resolved_config
        self._adapter_overrides = adapter_overrides or {}
        self._config = config_data or self._load_config()
        self._model_slots = self._load_model_slots()
        self._task_overrides = self._load_task_overrides()

    @staticmethod
    def _env_truthy(name: str) -> bool:
        raw = (os.getenv(name) or "").strip().lower()
        return raw in {"1", "true", "yes", "y", "on"}

    def _resolve_forced_model_key(self, *, task: str) -> Optional[str]:
        """
        Resolve forced model key from environment.

        Priority:
          1) IMAGE_CLIENT_FORCE_MODEL_KEY_<TASK>
          2) IMAGE_CLIENT_FORCE_MODEL_KEY
        """
        task_key = f"IMAGE_CLIENT_FORCE_MODEL_KEY_{(task or '').upper()}"
        raw = (os.getenv(task_key) or "").strip()
        if raw:
            return raw
        raw = (os.getenv("IMAGE_CLIENT_FORCE_MODEL_KEY") or "").strip()
        return raw or None

    def _load_model_slots(self) -> Dict[str, Any]:
        """
        Load optional image model slot codes (e.g. g-1 / f-4).

        Base: `configs/image_model_slots.yaml`
        Local: `configs/image_model_slots.local.yaml` (override; not tracked)
        """
        root = repo_paths.repo_root()
        env_path = (os.getenv("IMAGE_CLIENT_MODEL_SLOTS_PATH") or "").strip()
        if env_path:
            resolved = Path(env_path)
            if not resolved.is_absolute():
                resolved = root / resolved
            base_path = resolved
        else:
            base_path = root / "configs" / "image_model_slots.yaml"
        local_path = root / "configs" / "image_model_slots.local.yaml"

        if not base_path.exists() and not local_path.exists():
            return {"schema_version": 1, "slots": {}}

        base: Dict[str, Any] = {}
        if base_path.exists():
            try:
                raw = yaml.safe_load(base_path.read_text(encoding="utf-8")) or {}
                if isinstance(raw, dict):
                    base = raw
            except Exception:
                base = {}

        if local_path.exists():
            try:
                local = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
            except Exception:
                local = {}
            if isinstance(local, dict) and local:
                return _deep_merge_dict(base, local)

        return base

    def _resolve_model_key_selector(self, *, task: str, selector: str) -> Optional[str]:
        """
        Resolve a model selector to a real `model_key` in `configs/image_models.yaml`.

        Supported:
        - direct model key (e.g. `fireworks_flux_kontext_max`)
        - slot code (e.g. `f-4`) via `configs/image_model_slots.yaml`
        """
        raw = str(selector or "").strip()
        if not raw:
            return None

        models = self._config.get("models", {})
        if isinstance(models, dict) and raw in models:
            return raw

        slots_conf = getattr(self, "_model_slots", None)
        slots = slots_conf.get("slots") if isinstance(slots_conf, dict) else None
        if not isinstance(slots, dict):
            return None

        ent = slots.get(raw)
        if ent is None and raw.lower() in slots:
            ent = slots.get(raw.lower())
        if not isinstance(ent, dict):
            return None

        tasks = ent.get("tasks")
        if not isinstance(tasks, dict):
            return None

        tn = str(task or "").strip()
        mk = tasks.get(tn)
        if mk in (None, ""):
            mk = tasks.get("default")
        if not isinstance(mk, str) or not mk.strip():
            return None
        mk_norm = mk.strip()
        if isinstance(models, dict) and mk_norm in models:
            return mk_norm

        logging.warning(
            "ImageClient: slot selector '%s' resolved to unknown model_key '%s' for task '%s'; ignoring",
            raw,
            mk_norm,
            tn,
        )
        return None

    def _task_overrides_path(self) -> Path:
        root = repo_paths.repo_root()
        env_path = (os.getenv("IMAGE_CLIENT_TASK_OVERRIDES_PATH") or "").strip()
        if env_path:
            resolved = Path(env_path)
            if not resolved.is_absolute():
                resolved = root / resolved
            return resolved

        local_path = root / "configs" / "image_task_overrides.local.yaml"
        if local_path.exists():
            return local_path
        return root / "configs" / "image_task_overrides.yaml"

    def _load_task_overrides(self) -> Dict[str, Any]:
        path = self._task_overrides_path()
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _resolve_profile_task_override(self, *, task: str) -> Dict[str, Any]:
        """
        Resolve task override from `configs/image_task_overrides.yaml`.

        Schema:
          - profiles.<profile>.tasks.<task>.model_key: str
          - profiles.<profile>.tasks.<task>.allow_fallback: bool

        Fallback behavior:
          - profile defaults to "default"
          - missing profile -> fall back to "default"
          - missing file / invalid schema -> {}
        """
        task = (task or "").strip()
        if not task:
            return {}

        profile = (os.getenv("IMAGE_CLIENT_PROFILE") or "").strip() or "default"
        raw = getattr(self, "_task_overrides", None)
        if not isinstance(raw, dict):
            return {}

        profiles = raw.get("profiles")
        if isinstance(profiles, dict):
            profile_conf = profiles.get(profile)
            if profile_conf is None and profile != "default":
                profile_conf = profiles.get("default")
            if not isinstance(profile_conf, dict):
                return {}
            tasks = profile_conf.get("tasks", {})
            if not isinstance(tasks, dict):
                return {}
            override = tasks.get(task, {})
            return override if isinstance(override, dict) else {}

        tasks = raw.get("tasks", {})
        if not isinstance(tasks, dict):
            return {}
        override = tasks.get(task, {})
        return override if isinstance(override, dict) else {}

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
        call_time_selector: Optional[str] = None
        # Policy: when a model_key is explicitly selected (call/env/profile), do not silently
        # degrade to other tier candidates. Allowing fallback must be an explicit choice.
        allow_fallback = False
        allow_fallback_explicit = False
        if isinstance(options.extra, dict):
            raw_forced = options.extra.get("model_key")
            if isinstance(raw_forced, str) and raw_forced.strip():
                call_time_selector = raw_forced.strip()
                forced_model_key = call_time_selector
            raw_allow_fallback = options.extra.get("allow_fallback")
            if raw_allow_fallback is not None:
                allow_fallback = bool(raw_allow_fallback)
                allow_fallback_explicit = True

        # Lockdown guard:
        # Prevent per-call model overrides (and allow_fallback=true) from silently diverging from
        # operator-selected routing (env override or profile overrides).
        if lockdown_active():
            expected_selector: Optional[str] = self._resolve_forced_model_key(task=options.task)
            expected_source = "env"
            if not expected_selector:
                override = self._resolve_profile_task_override(task=options.task)
                mk = override.get("model_key") if isinstance(override, dict) else None
                if isinstance(mk, str) and mk.strip():
                    expected_selector = mk.strip()
                    expected_source = "profile"

            if expected_selector:
                expected_key = (
                    self._resolve_model_key_selector(task=options.task, selector=expected_selector) or expected_selector
                )
                if allow_fallback_explicit and allow_fallback:
                    raise ImageGenerationError(
                        "\n".join(
                            [
                                "[LOCKDOWN] Forbidden allow_fallback=true under fixed routing.",
                                f"- task: {options.task}",
                                f"- expected({expected_source}): {expected_selector} -> {expected_key}",
                                "- policy: When routing is fixed (env/profile), fallback can silently switch models and is not allowed.",
                                "- fix: unset SRT2IMAGES_IMAGE_ALLOW_FALLBACK or pass allow_fallback=false.",
                                "- debug: set YTM_EMERGENCY_OVERRIDE=1 for this run (not for normal ops).",
                            ]
                        )
                    )

                if call_time_selector:
                    call_key = (
                        self._resolve_model_key_selector(task=options.task, selector=call_time_selector) or call_time_selector
                    )
                    if expected_key != call_key:
                        raise ImageGenerationError(
                            "\n".join(
                                [
                                    "[LOCKDOWN] Conflicting image model override detected.",
                                    f"- task: {options.task}",
                                    f"- expected({expected_source}): {expected_selector} -> {expected_key}",
                                    f"- call_time: {call_time_selector} -> {call_key}",
                                    "- policy: Do not override model_key per call when routing is already fixed by env/profile.",
                                    "- fix: remove cue.image_model_key / --model-key, or change the routing via UI (/image-model-routing).",
                                    "- debug: set YTM_EMERGENCY_OVERRIDE=1 for this run (not for normal ops).",
                                ]
                            )
                        )
        if not forced_model_key:
            forced_model_key = self._resolve_forced_model_key(task=options.task)
        forced_model_from_profile = False
        if not forced_model_key:
            override = self._resolve_profile_task_override(task=options.task)
            if override:
                mk = override.get("model_key")
                if isinstance(mk, str) and mk.strip():
                    mk_norm = mk.strip()
                    resolved = self._resolve_model_key_selector(task=options.task, selector=mk_norm)
                    if resolved:
                        forced_model_key = resolved
                        forced_model_from_profile = True
                    else:
                        logging.warning(
                            "ImageClient: override model_key '%s' for task '%s' not found in image model config; ignoring",
                            mk_norm,
                            options.task,
                        )
                if forced_model_from_profile and not allow_fallback_explicit:
                    raw_af = override.get("allow_fallback")
                    if raw_af is not None:
                        allow_fallback = bool(raw_af)

        # Tier fallback policy (when no explicit model_key is selected):
        # - default: try the tier candidate list (best-effort)
        # - to make a task strict: set tasks.<task>.allow_fallback: false or pass extra.allow_fallback=false
        tier_allow_fallback = True
        if allow_fallback_explicit:
            tier_allow_fallback = allow_fallback
        else:
            raw_task_af = task_conf.get("allow_fallback")
            if raw_task_af is not None:
                tier_allow_fallback = bool(raw_task_af)

        errors: List[Tuple[str, Exception]] = []
        if forced_model_key:
            forced_selector = forced_model_key
            forced_model_key = self._resolve_model_key_selector(task=options.task, selector=forced_selector) or forced_selector
            if (
                lockdown_active()
                and str(options.task or "").strip() in IMAGE_MODEL_KEY_BLOCKLIST_TASKS
                and forced_model_key in IMAGE_MODEL_KEY_BLOCKLIST
            ):
                raise ImageGenerationError(
                    "\n".join(
                        [
                            "[LOCKDOWN] Forbidden image model key detected for video images (Gemini 3 image models are not allowed for visual_image_gen).",
                            f"- task: {options.task}",
                            f"- selector: {forced_selector}",
                            f"- resolved_model_key: {forced_model_key}",
                            "- policy: Gemini 3 系の画像モデルは動画内画像では使用禁止です（サムネは許可）。",
                            "- fix: use slot/codes like img-gemini-flash-1 (g-1) for video images, or remove the override env var.",
                            "- debug: set YTM_EMERGENCY_OVERRIDE=1 for this run (not for normal ops).",
                        ]
                    )
                )
            forced_conf = self._config.get("models", {}).get(forced_model_key)
            if not forced_conf:
                hint = ""
                if forced_model_key != forced_selector:
                    hint = (
                        f" (selector '{forced_selector}' resolved to '{forced_model_key}', but it is missing). "
                        "Check configs/image_model_slots.yaml and configs/image_models.yaml."
                    )
                raise ImageGenerationError(
                    f"Forced model '{forced_selector}' not found in image model configuration{hint}"
                )

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
                        _trace_image_call(
                            task=options.task,
                            tier=tier_name,
                            model_key=model_key,
                            provider=str(model_conf.get("provider") or "") or None,
                            request_id=result.request_id,
                            duration_ms=duration_ms,
                            options=resolved,
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
                f"Image generation failed for task '{options.task}' (requested model: '{forced_model_key}'). "
                "Fallback is disabled by default for explicit model_key; set allow_fallback=true only if you accept "
                "an alternative model. Details: "
                + "; ".join([f"{k}: {e}" for k, e in errors])
            )

        candidate_keys = self._rotate_candidates(tier_name, candidates) if tier_allow_fallback else candidates[:1]
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
                    _trace_image_call(
                        task=options.task,
                        tier=tier_name,
                        model_key=model_key,
                        provider=str(model_conf.get("provider") or "") or None,
                        request_id=result.request_id,
                        duration_ms=duration_ms,
                        options=resolved,
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
        hint = ""
        if not tier_allow_fallback and isinstance(candidates, list) and len(candidates) > 1:
            hint = (
                " Tier fallback is disabled; set extra.allow_fallback=true (per-call) or "
                "tasks.<task>.allow_fallback=true (config) to try alternatives."
            )
        raise ImageGenerationError(
            f"All image models failed for task '{options.task}': "
            + "; ".join([f"{k}: {e}" for k, e in errors])
            + hint
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
            input_images=options.input_images,
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
        if provider == "fireworks":
            return FireworksImageAdapter(self._config.get("providers", {}))

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
        if not self._env_truthy("IMAGE_CLIENT_ENABLE_ROUND_ROBIN"):
            return candidates
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
        if not self._env_truthy("IMAGE_CLIENT_ENABLE_ROUND_ROBIN"):
            return
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
        if genai is None:
            raise ImageGenerationError(
                "Gemini image provider requires optional dependency 'google-genai'. "
                "Install it (pip install google-genai) or disable gemini models."
            )
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

    @staticmethod
    def _looks_like_imagen_model(model_name: str) -> bool:
        # Imagen models (e.g. imagen-4.0-fast-generate-001) use the generate_images endpoint.
        name = str(model_name or "").strip().lower()
        return bool(name) and name.startswith("imagen-")

    def generate(self, model_conf: Dict[str, Any], options: ImageTaskOptions) -> ImageResult:
        model_name = str(model_conf.get("model_name") or "").strip()
        if not model_name:
            raise ImageGenerationError("Gemini model name is missing from configuration")

        metadata: Dict[str, Any] = {
            "aspect_ratio": options.aspect_ratio,
            "image_size": options.size,
            "seed": options.seed,
            "negative_prompt": options.negative_prompt,
            "n": options.n,
        }

        if self._looks_like_imagen_model(model_name):
            try:
                cfg = genai_types.GenerateImagesConfig(
                    numberOfImages=int(max(1, int(options.n or 1))),
                    aspectRatio=(str(options.aspect_ratio).strip() if options.aspect_ratio else None),
                    negativePrompt=(str(options.negative_prompt).strip() if options.negative_prompt else None),
                    seed=(int(options.seed) if options.seed is not None else None),
                )
                response = self.client.models.generate_images(
                    model=model_name,
                    prompt=str(options.prompt or ""),
                    config=cfg,
                )
            except Exception as e:  # pragma: no cover
                raise ImageGenerationError(str(e)) from e

            images: List[bytes] = []
            for item in getattr(response, "generated_images", []) or []:
                img_obj = getattr(item, "image", None)
                if not img_obj:
                    continue
                data = getattr(img_obj, "image_bytes", None)
                if isinstance(data, str):
                    try:
                        images.append(base64.b64decode(data))
                    except Exception:
                        continue
                elif isinstance(data, (bytes, bytearray)):
                    images.append(bytes(data))

            if not images:
                raise ImageGenerationError("Imagen response did not return any image data")

            return ImageResult(
                images=images,
                provider="gemini",
                model=model_name,
                request_id=None,
                metadata=metadata,
            )

        images: List[bytes] = []
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

    @staticmethod
    def _encode_input_image(path: str, *, max_dim: int = 512, quality: int = 80) -> Optional[str]:
        """
        Encode a local image file as a compact data URL for OpenRouter multimodal messages.
        Prefer JPEG thumbnail to keep payloads small and stable.
        """
        p = Path(path)
        if not p.exists() or not p.is_file():
            return None
        try:
            from PIL import Image  # pillow is in repo deps

            with Image.open(p) as img:
                img = img.convert("RGB")
                img.thumbnail((max_dim, max_dim), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                data = buf.getvalue()
            return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")
        except Exception:
            try:
                raw = p.read_bytes()
                suffix = p.suffix.lower()
                mime = "image/png"
                if suffix in (".jpg", ".jpeg"):
                    mime = "image/jpeg"
                elif suffix == ".webp":
                    mime = "image/webp"
                elif suffix == ".gif":
                    mime = "image/gif"
                return f"data:{mime};base64," + base64.b64encode(raw).decode("ascii")
            except Exception:
                return None

    def generate(self, model_conf: Dict[str, Any], options: ImageTaskOptions) -> ImageResult:
        model_name = model_conf.get("model_name")
        if not model_name:
            raise ImageGenerationError("OpenRouter model name is missing from configuration")

        timeout_sec = 120
        if isinstance(options.extra, dict):
            raw_timeout = options.extra.get("timeout_sec")
            if isinstance(raw_timeout, (int, float)) and raw_timeout > 0:
                timeout_sec = int(raw_timeout)

        content: Any = options.prompt
        input_images = options.input_images or []
        encoded_images_count = 0
        if input_images:
            encoded_parts: List[Dict[str, Any]] = []
            for raw_path in input_images[:3]:
                url = self._encode_input_image(str(raw_path))
                if url:
                    encoded_parts.append({"type": "image_url", "image_url": {"url": url}})
            if encoded_parts:
                hint = (
                    "Use the attached reference image(s) as visual anchors for consistency. "
                    "Keep the same character face/hair/clothing across shots and do NOT add extra people.\n\n"
                )
                content = [{"type": "text", "text": hint + options.prompt}, *encoded_parts]
                encoded_images_count = len(encoded_parts)

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": [{"role": "user", "content": content}],
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

        # Some models/providers reject multimodal content for image generation.
        # Fail-soft: retry once with text-only prompt before failing the request.
        if resp.status_code >= 400 and encoded_images_count > 0 and resp.status_code not in (402, 429):
            try:
                payload_no_images = {**payload, "messages": [{"role": "user", "content": options.prompt}]}
                resp2 = requests.post(url, headers=headers, json=payload_no_images, timeout=timeout_sec)
                try:
                    data2 = resp2.json()
                except ValueError:
                    data2 = None
                if resp2.status_code < 400 and isinstance(data2, dict):
                    logging.warning(
                        "OpenRouter multimodal request rejected (status=%s); retried with text-only and succeeded.",
                        resp.status_code,
                    )
                    resp = resp2
                    data = data2
                    encoded_images_count = 0
            except requests.RequestException:
                pass

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
                "input_images": encoded_images_count,
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


class FireworksImageAdapter:
    """
    Fireworks text-to-image adapter.

    Default endpoint (docs):
      POST /workflows/accounts/<account>/models/<model_name>/text_to_image
    """

    def __init__(self, provider_conf: Dict[str, Any]):
        conf = provider_conf.get("fireworks", {}) or {}
        if not isinstance(conf, dict):
            conf = {}
        self.provider_conf = conf

        api_key_env = str(self.provider_conf.get("env_api_key") or "FIREWORKS_API_KEY")
        api_key = GeminiImageAdapter._resolve_api_key(api_key_env)

        api_key_fallback_env = str(self.provider_conf.get("env_api_key_fallback") or "FIREWORKS_API_KEY_FALLBACK")
        api_key_fallback = GeminiImageAdapter._resolve_api_key(api_key_fallback_env)

        if not api_key and not api_key_fallback:
            # Support pooled keyring-based operation (factory_common.fireworks_keys).
            # `generate()` will lease a key per request; we only need *some* key source to exist.
            try:
                has_keyring_keys = bool(fireworks_keys.candidate_keys("image"))
            except Exception:
                has_keyring_keys = False

            if not has_keyring_keys:
                raise ImageGenerationError(
                    "Fireworks API key not found. Please set environment variable "
                    f"'{api_key_env}' (or fallback '{api_key_fallback_env}'), or add keys to "
                    f"the keyring file '{fireworks_keys.keyring_path('image')}'."
                )

        self.api_key = api_key
        self.api_key_fallback = api_key_fallback if api_key_fallback and api_key_fallback != api_key else None

        self.base_url = str(self.provider_conf.get("base_url") or "https://api.fireworks.ai/inference/v1").rstrip("/")

        self.account = self._resolve_account()
        self.max_prompt_chars = self._resolve_max_prompt_chars()

    def _resolve_max_prompt_chars(self) -> int:
        raw = self.provider_conf.get("max_prompt_chars")
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, str) and raw.strip().isdigit():
            return int(raw.strip())
        # Empirically: very long prompts can cause internal workflow failures for FLUX schnell.
        # Keep this conservative; Kontext models override via configs/image_models.yaml defaults.
        return 800

    def _resolve_account(self) -> str:
        env_account = str(self.provider_conf.get("env_account") or "").strip()
        if env_account:
            val = (os.getenv(env_account) or "").strip()
            if val:
                return val
        raw = str(self.provider_conf.get("account") or "").strip()
        return raw or "fireworks"

    def _endpoint(self, model_name: str) -> str:
        model_name = (model_name or "").strip()
        if not model_name:
            raise ImageGenerationError("Fireworks model name is missing from configuration")
        return f"{self.base_url}/workflows/accounts/{self.account}/models/{model_name}/text_to_image"

    def _kontext_endpoint(self, model_name: str) -> str:
        model_name = (model_name or "").strip()
        if not model_name:
            raise ImageGenerationError("Fireworks model name is missing from configuration")
        return f"{self.base_url}/workflows/accounts/{self.account}/models/{model_name}"

    def _kontext_get_result_endpoint(self, model_name: str) -> str:
        return f"{self._kontext_endpoint(model_name)}/get_result"

    @staticmethod
    def _is_kontext_model(model_name: str) -> bool:
        return str(model_name or "").strip().lower().startswith("flux-kontext-")

    @staticmethod
    def _compact_prompt(prompt: str, *, max_chars: int) -> str:
        if max_chars <= 0:
            return prompt
        text = str(prompt or "").strip()
        if len(text) <= max_chars:
            return text
        marker = "\n…\n"
        tail_len = min(320, max(80, max_chars // 3))
        head_len = max_chars - tail_len - len(marker)
        if head_len <= 0:
            return text[:max_chars].rstrip()
        head = text[:head_len].rstrip()
        tail = text[-tail_len:].lstrip()
        return f"{head}{marker}{tail}"

    @staticmethod
    def _parse_size(size: str | None) -> Optional[Tuple[int, int]]:
        if not size:
            return None
        s = str(size).strip().lower().replace("×", "x")
        if "x" not in s:
            return None
        a, b = s.split("x", 1)
        try:
            w = int(a.strip())
            h = int(b.strip())
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None
        return w, h

    @staticmethod
    def _maybe_resize_png(data: bytes, *, target: Optional[Tuple[int, int]]) -> bytes:
        if not target:
            return data
        try:
            from PIL import Image, ImageOps  # pillow is in repo deps

            img = Image.open(io.BytesIO(data))
            img.load()

            resized = ImageOps.fit(img, target, method=Image.LANCZOS, centering=(0.5, 0.5))
            out = io.BytesIO()
            resized.save(out, format="PNG")
            return out.getvalue()
        except Exception:
            # Fail-soft: if pillow isn't available or conversion fails, keep original bytes.
            return data

    @staticmethod
    def _decode_json_payload(payload: Dict[str, Any]) -> List[bytes]:
        """
        Fireworks can return JSON base64 array when Accept=application/json.
        Handle both `base64` and nested response styles.
        """
        out: List[bytes] = []
        b64_list = payload.get("base64")
        if isinstance(b64_list, list):
            for item in b64_list:
                if isinstance(item, str) and item:
                    try:
                        out.append(base64.b64decode(item))
                    except Exception:
                        continue
        return out

    def _post(self, *, url: str, payload: Dict[str, Any], timeout_sec: int) -> requests.Response:
        def do(key: str) -> requests.Response:
            headers = {
                "Content-Type": "application/json",
                "Accept": "image/png",
                "Authorization": f"Bearer {key}",
            }
            return requests.post(url, headers=headers, json=payload, timeout=timeout_sec)

        last: Optional[requests.Response] = None
        if self.api_key:
            last = do(self.api_key)
            if last.status_code not in (401, 403):
                return last
        if self.api_key_fallback:
            last = do(self.api_key_fallback)
            return last
        assert last is not None
        return last

    def _post_json(self, *, url: str, payload: Dict[str, Any], timeout_sec: int) -> requests.Response:
        def do(key: str) -> requests.Response:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {key}",
            }
            return requests.post(url, headers=headers, json=payload, timeout=timeout_sec)

        last: Optional[requests.Response] = None
        if self.api_key:
            last = do(self.api_key)
            if last.status_code not in (401, 403):
                return last
        if self.api_key_fallback:
            last = do(self.api_key_fallback)
            return last
        assert last is not None
        return last

    def _request_json(self, *, method: str, url: str, payload: Dict[str, Any], timeout_sec: int) -> requests.Response:
        """
        Kontext get_result is documented as GET, but official examples send POST with JSON body.
        Support both (method fallback) while keeping the same auth + JSON headers.
        """

        def do(key: str) -> requests.Response:
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {key}",
            }
            return requests.request(method, url, headers=headers, json=payload, timeout=timeout_sec)

        last: Optional[requests.Response] = None
        if self.api_key:
            last = do(self.api_key)
            if last.status_code not in (401, 403):
                return last
        if self.api_key_fallback:
            last = do(self.api_key_fallback)
            return last
        assert last is not None
        return last

    @staticmethod
    def _looks_like_base64_image(s: str) -> bool:
        text = (s or "").strip()
        if not text or len(text) < 128:
            return False
        # quick check: base64 chars only
        import re

        if not re.fullmatch(r"[A-Za-z0-9+/=\\s]+", text):
            return False
        return True

    def _extract_kontext_image_bytes(self, payload: Any, *, timeout_sec: int) -> List[bytes]:
        """
        Kontext get_result response schema is not fixed (docs describe `result` as any).
        Extract image bytes defensively from:
        - data URLs (data:image/...;base64,...)
        - HTTPS URLs (presigned images)
        - raw base64 strings in fields named like *base64*
        """

        def decode_candidate(value: str, *, key_hint: str) -> Optional[bytes]:
            text = str(value or "").strip()
            if not text:
                return None
            if text.startswith("data:"):
                return OpenRouterImageAdapter._decode_image_url(text, timeout_sec=timeout_sec)
            if text.startswith("http://") or text.startswith("https://"):
                return OpenRouterImageAdapter._decode_image_url(text, timeout_sec=timeout_sec)
            if "base64" in key_hint.lower() and self._looks_like_base64_image(text):
                try:
                    return base64.b64decode(text)
                except Exception:
                    return None
            return None

        def walk(obj: Any, *, key_hint: str = "") -> List[bytes]:
            found: List[bytes] = []
            if obj is None:
                return found
            if isinstance(obj, str):
                data = decode_candidate(obj, key_hint=key_hint)
                if data:
                    found.append(data)
                return found
            if isinstance(obj, dict):
                for k, v in obj.items():
                    next_hint = str(k)
                    if isinstance(v, str):
                        data = decode_candidate(v, key_hint=next_hint)
                        if data:
                            found.append(data)
                    else:
                        found.extend(walk(v, key_hint=next_hint))
                return found
            if isinstance(obj, list):
                for item in obj:
                    found.extend(walk(item, key_hint=key_hint))
                return found
            return found

        return walk(payload)

    def _kontext_poll_result(
        self,
        *,
        model_name: str,
        request_id: str,
        timeout_sec: int,
        get_result_timeout_sec: int,
    ) -> Dict[str, Any]:
        url = self._kontext_get_result_endpoint(model_name)
        started = time.time()
        delay = 0.8
        last_detail: Any = None

        while True:
            elapsed = time.time() - started
            if elapsed >= timeout_sec:
                raise ImageGenerationError(
                    f"Fireworks Kontext timed out after {timeout_sec}s waiting for request_id={request_id}. "
                    f"Last response: {last_detail}"
                )

            remaining = max(1, int(timeout_sec - elapsed))
            per_call_timeout = min(max(1, int(get_result_timeout_sec)), remaining)

            # Docs list GET, but official examples show POST with JSON body.
            payload = {"id": request_id}
            try:
                resp = self._request_json(method="POST", url=url, payload=payload, timeout_sec=per_call_timeout)
                if resp.status_code == 405:
                    resp = self._request_json(method="GET", url=url, payload=payload, timeout_sec=per_call_timeout)
            except requests.RequestException as exc:  # pragma: no cover - network faults
                raise ImageGenerationError(f"Fireworks Kontext get_result request failed: {exc}") from exc

            if resp.status_code >= 400:
                detail: Any = None
                try:
                    detail = resp.json()
                except Exception:
                    detail = (resp.text or "").strip()
                msg = f"Fireworks Kontext get_result error {resp.status_code}: {detail}"
                if resp.status_code == 429:
                    # Rate limit while polling is usually transient; we already have a request_id,
                    # so back off and keep waiting instead of failing the whole generation.
                    last_detail = detail
                    retry_after = resp.headers.get("Retry-After")
                    try:
                        retry_after_sec = float(retry_after) if retry_after else 0.0
                    except Exception:
                        retry_after_sec = 0.0
                    backoff = max(delay, retry_after_sec, 2.0)
                    time.sleep(backoff)
                    delay = min(10.0, max(delay * 1.35, backoff))
                    continue
                raise ImageGenerationError(msg)

            try:
                data = resp.json() if (resp.content or b"").strip() else {}
            except Exception as exc:
                raise ImageGenerationError(f"Fireworks Kontext returned invalid JSON payload: {exc}") from exc

            if not isinstance(data, dict):
                raise ImageGenerationError(f"Fireworks Kontext returned non-object JSON payload: {data}")

            last_detail = data
            status_raw = data.get("status")
            status = str(status_raw or "").strip().lower()

            if status in {"ready"}:
                return data
            if status in {"pending"}:
                pass
            elif status in {"task not found"}:
                # transient right after create; treat as pending for a short while
                pass
            elif status in {"request moderated", "content moderated", "error"}:
                raise ImageGenerationError(f"Fireworks Kontext failed ({status_raw}): {data}")
            elif status:
                # Unknown status: if we can already extract an image, accept it.
                if self._extract_kontext_image_bytes(data, timeout_sec=per_call_timeout):
                    return data

            time.sleep(delay)
            delay = min(3.5, delay * 1.35)

    def generate(self, model_conf: Dict[str, Any], options: ImageTaskOptions) -> ImageResult:
        model_name = str(model_conf.get("model_name") or "").strip()
        timeout_sec = 120
        if isinstance(options.extra, dict):
            raw_timeout = options.extra.get("timeout_sec")
            if isinstance(raw_timeout, (int, float)) and raw_timeout > 0:
                timeout_sec = int(raw_timeout)

        defaults = model_conf.get("defaults", {}) if isinstance(model_conf.get("defaults", {}), dict) else {}

        # Fireworks key management (image pool):
        # - Prefer leasing a key from the dedicated image pool.
        # - Enforce exclusivity across parallel agents.
        # - Retry on key-scoped auth/credit errors by rotating to a different key.
        try:
            lease_ttl_sec = int(os.getenv("FIREWORKS_IMAGE_KEY_LEASE_TTL_SEC", "1800"))
        except Exception:
            lease_ttl_sec = 1800
        try:
            key_attempts = int(os.getenv("FIREWORKS_IMAGE_KEY_MAX_ATTEMPTS", "5"))
        except Exception:
            key_attempts = 5
        key_attempts = max(1, min(20, int(key_attempts)))

        def _is_key_scoped_error(exc: Exception) -> bool:
            msg = str(exc or "")
            needles = [
                "Fireworks error 401:",
                "Fireworks error 402:",
                "Fireworks error 403:",
                "Fireworks error 429:",
                "Fireworks error 412:",
                "Fireworks Kontext error 401:",
                "Fireworks Kontext error 402:",
                "Fireworks Kontext error 403:",
                "Fireworks Kontext error 429:",
                "Fireworks Kontext error 412:",
                "Fireworks Kontext get_result error 401:",
                "Fireworks Kontext get_result error 402:",
                "Fireworks Kontext get_result error 403:",
                "Fireworks Kontext get_result error 429:",
                "Fireworks Kontext get_result error 412:",
            ]
            return any(n in msg for n in needles)

        def _extract_fireworks_http_status(exc: Exception) -> Optional[int]:
            msg = str(exc or "")
            prefixes = [
                "Fireworks error ",
                "Fireworks Kontext error ",
                "Fireworks Kontext get_result error ",
                "Fireworks Kontext failed (",
            ]
            for pre in prefixes:
                idx = msg.find(pre)
                if idx < 0:
                    continue
                rest = msg[idx + len(pre) :]
                if pre == "Fireworks Kontext failed (":
                    # "Fireworks Kontext failed (429): ..."
                    end = rest.find(")")
                    code = rest[:end] if end > 0 else ""
                else:
                    # "Fireworks error 429: ..."
                    code = rest[:3]
                code = str(code).strip()
                if code.isdigit():
                    try:
                        return int(code)
                    except Exception:
                        return None
            return None

        def _run_with_current_key() -> ImageResult:
            request_ids: List[str] = []

            def _resolve_num(name: str, default_val: Optional[float]) -> Optional[float]:
                if isinstance(options.extra, dict) and name in options.extra:
                    v = options.extra.get(name)
                    if isinstance(v, (int, float)):
                        return float(v)
                v = defaults.get(name)
                if isinstance(v, (int, float)):
                    return float(v)
                return default_val

            guidance_scale = _resolve_num("guidance_scale", None)
            num_steps = _resolve_num("num_inference_steps", None)

            target_size = self._parse_size(options.size)

            images: List[bytes] = []
            for _ in range(max(1, int(options.n or 1))):
                prompt_max_chars = int(self.max_prompt_chars or 0)
                if isinstance(options.extra, dict):
                    raw_max = options.extra.get("max_prompt_chars")
                    if isinstance(raw_max, int) and raw_max > 0:
                        prompt_max_chars = raw_max
                    elif isinstance(raw_max, str) and raw_max.strip().isdigit():
                        prompt_max_chars = int(raw_max.strip())
                if isinstance(defaults, dict) and "max_prompt_chars" in defaults:
                    raw_max = defaults.get("max_prompt_chars")
                    if isinstance(raw_max, int) and raw_max > 0:
                        prompt_max_chars = raw_max
                    elif isinstance(raw_max, str) and raw_max.strip().isdigit():
                        prompt_max_chars = int(raw_max.strip())

                prompt_text = self._compact_prompt(options.prompt, max_chars=prompt_max_chars)
                if self._is_kontext_model(model_name):
                    url = self._kontext_endpoint(model_name)
                    payload: Dict[str, Any] = {"prompt": prompt_text}

                    if options.aspect_ratio:
                        payload["aspect_ratio"] = options.aspect_ratio
                    if options.seed is not None:
                        payload["seed"] = int(options.seed)

                    def _resolve_extra_or_default(name: str, default_val: Any) -> Any:
                        if isinstance(options.extra, dict) and name in options.extra:
                            return options.extra.get(name)
                        if name in defaults:
                            return defaults.get(name)
                        return default_val

                    output_format = _resolve_extra_or_default("output_format", "png")
                    if isinstance(output_format, str) and output_format.strip():
                        payload["output_format"] = output_format.strip()

                    prompt_upsampling = _resolve_extra_or_default("prompt_upsampling", False)
                    if isinstance(prompt_upsampling, bool):
                        payload["prompt_upsampling"] = prompt_upsampling

                    safety_tolerance = _resolve_extra_or_default("safety_tolerance", None)
                    if isinstance(safety_tolerance, (int, float)):
                        payload["safety_tolerance"] = int(safety_tolerance)

                    # Optional reference image (Kontext is strongest with input_image).
                    input_images = options.input_images or []
                    if input_images:
                        first = str(input_images[0] or "").strip()
                        if first.startswith("http://") or first.startswith("https://"):
                            payload["input_image"] = first
                        else:
                            try:
                                b = Path(first).read_bytes()
                                payload["input_image"] = base64.b64encode(b).decode("ascii")
                            except Exception:
                                # Fail-soft: ignore invalid reference images.
                                pass

                    create_timeout_sec = min(180, timeout_sec)
                    if isinstance(options.extra, dict):
                        raw_create_timeout = options.extra.get("create_timeout_sec")
                        if isinstance(raw_create_timeout, (int, float)) and raw_create_timeout > 0:
                            create_timeout_sec = int(raw_create_timeout)
                        elif isinstance(raw_create_timeout, str) and raw_create_timeout.strip().isdigit():
                            create_timeout_sec = int(raw_create_timeout.strip())

                    try:
                        resp = self._post_json(url=url, payload=payload, timeout_sec=create_timeout_sec)
                    except requests.RequestException as exc:  # pragma: no cover - network faults
                        raise ImageGenerationError(f"Fireworks Kontext request failed: {exc}") from exc

                    if resp.status_code >= 400:
                        detail: Any = None
                        try:
                            detail = resp.json()
                        except Exception:
                            detail = (resp.text or "").strip()
                        msg = f"Fireworks Kontext error {resp.status_code}: {detail}"
                        if resp.status_code == 429:
                            raise ImageProviderRateLimitError(
                                msg,
                                provider="fireworks",
                                status_code=int(resp.status_code),
                            )
                        raise ImageGenerationError(msg)

                    try:
                        created = resp.json()
                    except Exception as exc:
                        raise ImageGenerationError(f"Fireworks Kontext returned invalid JSON payload: {exc}") from exc

                    if not isinstance(created, dict):
                        raise ImageGenerationError(f"Fireworks Kontext returned non-object JSON payload: {created}")

                    request_id = str(created.get("request_id") or created.get("id") or "").strip()
                    if not request_id:
                        raise ImageGenerationError(f"Fireworks Kontext did not return request_id: {created}")
                    request_ids.append(request_id)

                    result_payload = self._kontext_poll_result(
                        model_name=model_name,
                        request_id=request_id,
                        timeout_sec=timeout_sec,
                        get_result_timeout_sec=(
                            int(options.extra.get("get_result_timeout_sec"))
                            if isinstance(options.extra, dict)
                            and str(options.extra.get("get_result_timeout_sec") or "").strip().isdigit()
                            else 90
                        ),
                    )
                    extracted = self._extract_kontext_image_bytes(result_payload, timeout_sec=timeout_sec)
                    if not extracted:
                        raise ImageGenerationError(
                            f"Fireworks Kontext result did not include any images: {result_payload}"
                        )
                    images.append(self._maybe_resize_png(extracted[0], target=target_size))
                else:
                    url = self._endpoint(model_name)
                    payload = {"prompt": prompt_text}
                    # Provide explicit size when available. Many diffusion workflows expect width/height.
                    if target_size:
                        tw = int(target_size[0])
                        th = int(target_size[1])
                        # Some Fireworks diffusion workflows appear to require width/height divisible by 16.
                        # Generate at a safe size and resize to the requested target post-hoc.
                        safe_w = ((tw + 15) // 16) * 16
                        safe_h = ((th + 15) // 16) * 16
                        payload["width"] = safe_w
                        payload["height"] = safe_h
                    # NOTE: Some Fireworks FLUX workflows intermittently fail with aspect_ratio set
                    # (internal negative dimension errors). We can safely omit it and resize post-hoc.
                    send_ar = (os.getenv("FIREWORKS_IMAGE_SEND_ASPECT_RATIO") or "").strip().lower() in {
                        "1",
                        "true",
                        "yes",
                        "on",
                    }
                    if send_ar and options.aspect_ratio:
                        payload["aspect_ratio"] = options.aspect_ratio
                    if guidance_scale is not None:
                        payload["guidance_scale"] = guidance_scale
                    if num_steps is not None:
                        payload["num_inference_steps"] = int(num_steps)
                    if options.seed is not None:
                        payload["seed"] = int(options.seed)

                    try:
                        resp = self._post(url=url, payload=payload, timeout_sec=timeout_sec)
                    except requests.RequestException as exc:  # pragma: no cover - network faults
                        raise ImageGenerationError(f"Fireworks request failed: {exc}") from exc

                    if resp.status_code >= 400:
                        detail: Any = None
                        try:
                            detail = resp.json()
                        except Exception:
                            detail = (resp.text or "").strip()
                        msg = f"Fireworks error {resp.status_code}: {detail}"
                        if resp.status_code == 429:
                            raise ImageProviderRateLimitError(
                                msg,
                                provider="fireworks",
                                status_code=int(resp.status_code),
                            )
                        raise ImageGenerationError(msg)

                    content_type = (resp.headers.get("Content-Type") or "").lower()
                    if "application/json" in content_type:
                        try:
                            decoded = self._decode_json_payload(resp.json())
                        except Exception as exc:
                            raise ImageGenerationError(
                                f"Fireworks returned invalid JSON payload: {exc}"
                            ) from exc
                        if not decoded:
                            raise ImageGenerationError(
                                "Fireworks JSON response did not include any base64 images"
                            )
                        for img in decoded:
                            images.append(self._maybe_resize_png(img, target=target_size))
                        continue

                    raw = resp.content or b""
                    if not raw:
                        raise ImageGenerationError("Fireworks returned empty image bytes")
                    images.append(self._maybe_resize_png(raw, target=target_size))

            return ImageResult(
                images=images,
                provider="fireworks",
                model=model_name,
                request_id=request_ids[-1] if request_ids else None,
                metadata={
                    "n": len(images),
                    "aspect_ratio": options.aspect_ratio,
                    "size": options.size,
                    "seed": options.seed,
                    "negative_prompt": options.negative_prompt,
                    "guidance_scale": guidance_scale,
                    "num_inference_steps": num_steps,
                    "kontext_request_ids": request_ids if request_ids else None,
                },
            )

        last_exc: Optional[Exception] = None
        for attempt in range(key_attempts):
            lease = fireworks_keys.acquire_key(
                "image",
                purpose=f"image:{str(options.task or '')}:{model_name}",
                ttl_sec=int(lease_ttl_sec),
                preflight=False,
                allow_recheck_exhausted=False,
            )
            if lease is None:
                break

            old_key = self.api_key
            old_fallback = self.api_key_fallback
            self.api_key = lease.key
            # IMPORTANT: do not fall back to an unleased key inside this call.
            self.api_key_fallback = None
            try:
                return _run_with_current_key()
            except ImageProviderRateLimitError as exc:
                # Fireworks pooled keys: 429 is often key-scoped. Try another leased key before
                # escalating to provider-level cooldown handling.
                try:
                    if int(getattr(exc, "http_status", 0) or 0) == 429:
                        cooldown_sec = int(os.getenv("FIREWORKS_IMAGE_KEY_COOLDOWN_SEC", "120") or 120)
                        fireworks_keys.record_key_status(
                            "image",
                            key=lease.key,
                            status="ok",
                            http_status=429,
                            note="429 rate_limit during image generation",
                            cooldown_sec=cooldown_sec,
                        )
                except Exception:
                    pass
                last_exc = exc
                continue
            except Exception as exc:  # noqa: BLE001
                last_exc = exc if isinstance(exc, Exception) else Exception(str(exc))
                if _is_key_scoped_error(last_exc):
                    try:
                        hs = _extract_fireworks_http_status(last_exc)
                        if hs == 429:
                            cooldown_sec = int(os.getenv("FIREWORKS_IMAGE_KEY_COOLDOWN_SEC", "120") or 120)
                            fireworks_keys.record_key_status(
                                "image",
                                key=lease.key,
                                status="ok",
                                http_status=429,
                                note="429 rate_limit during image generation",
                                cooldown_sec=cooldown_sec,
                            )
                        elif hs in (401, 402, 412):
                            status = {401: "invalid", 402: "exhausted", 412: "suspended"}.get(int(hs), "error")
                            fireworks_keys.record_key_status(
                                "image",
                                key=lease.key,
                                status=status,
                                http_status=int(hs),
                                note=f"{status} during image generation",
                            )
                    except Exception:
                        pass
                    continue
                raise
            finally:
                self.api_key = old_key
                self.api_key_fallback = old_fallback
                try:
                    fireworks_keys.release_lease(lease)
                except Exception:
                    pass

        if last_exc is not None:
            if isinstance(last_exc, ImageProviderRateLimitError):
                raise last_exc
            raise ImageGenerationError(
                f"Fireworks image key rotation exhausted after {key_attempts} attempts: {last_exc}"
            ) from last_exc
        cfg_n = 0
        try:
            cfg_n = len(fireworks_keys.candidate_keys("image"))
        except Exception:
            cfg_n = 0

        lease_note = ""
        try:
            leases = [o for o in fireworks_keys.list_active_leases() if str(o.get("pool") or "") == "image"]
            if leases:
                now = time.time()
                parts: List[str] = []
                for o in leases[:3]:
                    exp = float(o.get("expires_at") or 0.0)
                    ttl = max(0, int(exp - now)) if exp > 0 else 0
                    parts.append(
                        f"pid={o.get('pid')} agent={o.get('agent')} purpose={o.get('purpose')} ttl_sec={ttl}"
                    )
                more = "" if len(leases) <= 3 else f" (+{len(leases) - 3} more)"
                lease_note = " active_leases(image)=" + " | ".join(parts) + more
        except Exception:
            lease_note = ""

        raise ImageGenerationError(
            f"Fireworks image keys are unavailable (all leased/invalid/exhausted) configured_keys={cfg_n}.{lease_note}"
        )
