from __future__ import annotations

import copy
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

from backend.core.llm import LLMFactory
from factory_common.paths import repo_root as ssot_repo_root, research_root as ssot_research_root

LOGGER_NAME = "ui_backend"
logger = logging.getLogger(LOGGER_NAME)

REPO_ROOT = ssot_repo_root()
# NOTE: PROJECT_ROOT is treated as repo-root throughout this project (legacy alias).
PROJECT_ROOT = REPO_ROOT

UI_SETTINGS_PATH = PROJECT_ROOT / "configs" / "ui_settings.json"
ENV_FILE_CANDIDATES = [
    PROJECT_ROOT / ".env",
    PROJECT_ROOT / "ui" / ".env",
    ssot_research_root() / ".env",
    PROJECT_ROOT.parent / ".env",
]

OPENAI_CAPTION_DEFAULT_MODEL = os.getenv("OPENAI_DEFAULT_CAPTION_MODEL", "gpt-5-chat")
DEFAULT_CAPTION_PROVIDER = os.getenv("THUMBNAIL_CAPTION_PROVIDER", "openai")
DEFAULT_OPENAI_CAPTION_MODEL = os.getenv("OPENAI_DEFAULT_CAPTION_MODEL", OPENAI_CAPTION_DEFAULT_MODEL)

SETTINGS_LOCK = threading.Lock()
DEFAULT_UI_SETTINGS: Dict[str, Any] = {
    "llm": {
        "caption_provider": DEFAULT_CAPTION_PROVIDER,
        "openai_api_key": None,
        "openai_caption_model": DEFAULT_OPENAI_CAPTION_MODEL,
        "openrouter_api_key": None,
        "openrouter_caption_model": "qwen/qwen3-14b:free",
        # Phase models are now managed by LLMRegistry, but kept here for UI compatibility.
        "phase_models": {},
    }
}
UI_SETTINGS: Dict[str, Any] = {}
UI_SETTINGS_DISK_STATE: Dict[str, Optional[float]] = {
    "ui_settings_mtime": None,
}


def _safe_mtime(path: Path) -> Optional[float]:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _normalize_llm_settings(raw: Optional[dict]) -> dict:
    """Normalize settings using LLMRegistry defaults."""
    llm = copy.deepcopy(DEFAULT_UI_SETTINGS["llm"])
    if not isinstance(raw, dict):
        return llm

    # Copy basic settings.
    for key in [
        "caption_provider",
        "openai_api_key",
        "openai_caption_model",
        "openrouter_api_key",
        "openrouter_caption_model",
    ]:
        if raw.get(key):
            llm[key] = raw.get(key)

    # Merge phase models from registry and raw input.
    registry = LLMFactory.get_registry()
    merged_phase_models: Dict[str, Dict[str, object]] = {}

    # 1. Start with registry defaults.
    for phase, config in registry.phases.items():
        merged_phase_models[phase.value] = {
            "label": config.label or phase.value,
            "provider": config.provider.value,
            "model": config.model,
        }

    # 2. Override with incoming raw settings.
    incoming_phases = raw.get("phase_models") or {}
    for phase_id, incoming in incoming_phases.items():
        if not isinstance(incoming, dict):
            continue
        current = merged_phase_models.get(phase_id, {})
        merged_phase_models[phase_id] = {
            "label": incoming.get("label") or current.get("label") or phase_id,
            "provider": incoming.get("provider") or current.get("provider") or "openrouter",
            "model": incoming.get("model") or current.get("model"),
        }

    llm["phase_models"] = merged_phase_models
    return llm


def _load_ui_settings_from_disk() -> None:
    global UI_SETTINGS
    with SETTINGS_LOCK:
        settings = copy.deepcopy(DEFAULT_UI_SETTINGS)
        if UI_SETTINGS_PATH.exists():
            try:
                loaded = json.loads(UI_SETTINGS_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    settings["llm"] = _normalize_llm_settings(loaded.get("llm"))
            except Exception as exc:  # pragma: no cover - corrupted settings
                logger.warning("Failed to read %s: %s", UI_SETTINGS_PATH, exc)
        UI_SETTINGS = settings
        UI_SETTINGS_DISK_STATE["ui_settings_mtime"] = _safe_mtime(UI_SETTINGS_PATH)


def _write_ui_settings(settings: Dict[str, Any]) -> None:
    UI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_LOCK:
        UI_SETTINGS_PATH.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        UI_SETTINGS.update(copy.deepcopy(settings))
        UI_SETTINGS_DISK_STATE["ui_settings_mtime"] = _safe_mtime(UI_SETTINGS_PATH)


def _maybe_reload_ui_settings_from_disk() -> None:
    ui_mtime = _safe_mtime(UI_SETTINGS_PATH)
    with SETTINGS_LOCK:
        if ui_mtime == UI_SETTINGS_DISK_STATE.get("ui_settings_mtime"):
            return
    _load_ui_settings_from_disk()


def _get_ui_settings() -> Dict[str, Any]:
    _maybe_reload_ui_settings_from_disk()
    with SETTINGS_LOCK:
        return copy.deepcopy(UI_SETTINGS)


_load_ui_settings_from_disk()


def _load_env_value(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value:
        return value
    for env_path in ENV_FILE_CANDIDATES:
        if not env_path or not env_path.exists():
            continue
        try:
            for raw_line in env_path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith(f"{name}="):
                    _, raw_value = line.split("=", 1)
                    os.environ[name] = raw_value
                    logger.info("Loaded %s from %s", name, env_path)
                    return raw_value
        except Exception as exc:  # pragma: no cover - best-effort parsing
            logger.warning("Failed to parse %s for %s: %s", env_path, name, exc)
    return None


def _ensure_openrouter_api_key() -> str:
    getter = globals().get("_get_ui_settings")
    if callable(getter):
        settings = getter()
    else:  # pragma: no cover - defensive fallback for reload edge cases
        logger.error("_get_ui_settings is unavailable during OpenRouter key resolution; using defaults.")
        settings = copy.deepcopy(DEFAULT_UI_SETTINGS)
    value = settings.get("llm", {}).get("openrouter_api_key")
    if value:
        os.environ.setdefault("OPENROUTER_API_KEY", value)
        return value
    value = os.getenv("OPENROUTER_API_KEY") or _load_env_value("OPENROUTER_API_KEY")
    if value:
        return value
    if os.getenv("YTM_ALLOW_OPENROUTER_MISSING") == "1":
        logger.warning(
            "OPENROUTER_API_KEY is not configured, but YTM_ALLOW_OPENROUTER_MISSING=1 so continuing in degraded mode."
        )
        return ""
    raise RuntimeError(
        "OPENROUTER_API_KEY が設定されていません。`.env` を更新し `python scripts/check_env.py --keys OPENROUTER_API_KEY` "
        "を通過させてから UI を起動してください。"
    )


OPENROUTER_API_KEY = _ensure_openrouter_api_key()


def _get_effective_openrouter_key() -> Optional[str]:
    settings = _get_ui_settings()
    key = settings.get("llm", {}).get("openrouter_api_key")
    if key:
        return key
    return OPENROUTER_API_KEY or None


def _get_effective_openai_key() -> Optional[str]:
    settings = _get_ui_settings()
    key = settings.get("llm", {}).get("openai_api_key")
    if key:
        return key
    return os.getenv("OPENAI_API_KEY") or _load_env_value("OPENAI_API_KEY")


def _validate_provider_endpoint(provider: str) -> None:
    """
    Fail-fast to avoid sending OpenRouter payloads to Azure or vice versa.
    """
    if provider == "openrouter":
        base = os.getenv("OPENAI_BASE_URL", "").lower()
        if "cognitiveservices.azure.com" in base:
            raise HTTPException(
                status_code=400,
                detail=(
                    "provider=openrouter ですが OPENAI_BASE_URL が Azure を指しています。"
                    " OPENAI_BASE_URL=https://openrouter.ai/api/v1 にしてください。"
                ),
            )
        key = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENROUTER_API_TOKEN")
        if not key:
            raise HTTPException(status_code=400, detail="provider=openrouter には OPENROUTER_API_KEY が必要です。")
    elif provider == "openai":
        if not _get_effective_openai_key():
            raise HTTPException(status_code=400, detail="provider=openai には OpenAI/Azure APIキーが必要です。")
    elif provider == "gemini":
        if not os.getenv("GEMINI_API_KEY"):
            raise HTTPException(status_code=400, detail="provider=gemini には GEMINI_API_KEY が必要です。")
