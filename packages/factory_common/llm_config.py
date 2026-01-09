"""
factory_common.llm_config

Legacy-compatible config loader used mainly in unit tests.

SSOT (2026-01-09):
- Routing SSOT is `configs/llm_router.yaml` (+ task_overrides + codes/slots).
- Legacy `configs/llm.yml` is compatibility-only and is disabled under routing lockdown
  (`YTM_ROUTING_LOCKDOWN=1` / default ON).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

from factory_common.paths import repo_root
from factory_common.routing_lockdown import lockdown_active

PROJECT_ROOT = repo_root()
DEFAULT_ROUTER_CONFIG = PROJECT_ROOT / "configs" / "llm_router.yaml"
DEFAULT_ROUTER_CONFIG_LOCAL = PROJECT_ROOT / "configs" / "llm_router.local.yaml"

LEGACY_LLM_YML = PROJECT_ROOT / "configs" / "llm.yml"
LEGACY_LLM_YML_LOCAL = PROJECT_ROOT / "configs" / "llm.local.yml"

DEFAULT_TIER_MAPPING = PROJECT_ROOT / "configs" / "llm_tier_mapping.yaml"
DEFAULT_TIER_MAPPING_LOCAL = PROJECT_ROOT / "configs" / "llm_tier_mapping.local.yaml"
DEFAULT_TIER_CANDIDATES = PROJECT_ROOT / "configs" / "llm_tier_candidates.yaml"
DEFAULT_TIER_CANDIDATES_LOCAL = PROJECT_ROOT / "configs" / "llm_tier_candidates.local.yaml"

_LEGACY_REL_PATHS = {"configs/llm.yml", "configs/llm.local.yml"}


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep-merge dicts (override wins).

    Used for `.local.yaml` overlays to avoid drift when only a small subset is customized.
    """
    out: Dict[str, Any] = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out.get(key) or {}, value)  # type: ignore[arg-type]
        else:
            out[key] = value
    return out


def _as_repo_path(path: Path) -> Path:
    """
    Normalize relative paths against the repo root (CWD-independent).
    """
    if path.is_absolute():
        return path
    return PROJECT_ROOT / path


def _assert_legacy_llm_yml_allowed(config_path: Path) -> None:
    """
    Under routing lockdown, forbid loading the repo's legacy `configs/llm.yml` config.
    """
    if not lockdown_active():
        return

    try:
        rel = config_path.resolve().relative_to(PROJECT_ROOT.resolve())
    except Exception:
        return

    if rel.as_posix() not in _LEGACY_REL_PATHS:
        return

    raise RuntimeError(
        "\n".join(
            [
                "[LOCKDOWN] Legacy LLM config is disabled under routing lockdown.",
                f"- config: {rel.as_posix()}",
                "- policy: Use slot-based routing SSOT (configs/llm_router.yaml + codes/slots/overrides).",
                "- hint: remove legacy config usage, or pass a non-legacy config for tests.",
                "- debug: set YTM_ROUTING_LOCKDOWN=0 (disable lockdown) or YTM_EMERGENCY_OVERRIDE=1 (bypass) for this run.",
            ]
        )
    )


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
        return loaded if isinstance(loaded, dict) else {}


def _load_router_config_default() -> Dict[str, Any]:
    base = _load_yaml(DEFAULT_ROUTER_CONFIG)
    if DEFAULT_ROUTER_CONFIG_LOCAL.exists():
        local = _load_yaml(DEFAULT_ROUTER_CONFIG_LOCAL)
        if local:
            return _deep_merge_dict(base, local)
    return base


def load_llm_config(
    config_path: Path | str | None = None,
    tier_mapping_path: Path | str | None = None,
    tier_candidates_path: Path | str | None = None,
) -> Dict[str, Any]:
    """
    Load LLM routing config (router SSOT) with optional tier/task overrides.

    Default:
      - Base: `configs/llm_router.yaml`
      - Local overlay (deep-merge): `configs/llm_router.local.yaml`

    Legacy (compat-only; disabled under routing lockdown):
      - `configs/llm.yml` (+ `configs/llm.local.yml`)
    """
    if config_path:
        config_path = _as_repo_path(Path(config_path))
        _assert_legacy_llm_yml_allowed(config_path)
        base = _load_yaml(config_path)
    else:
        base = _load_router_config_default()

        # Fallback for environments that still rely on the legacy unified config.
        if not base:
            legacy = LEGACY_LLM_YML_LOCAL if LEGACY_LLM_YML_LOCAL.exists() else LEGACY_LLM_YML
            _assert_legacy_llm_yml_allowed(legacy)
            base = _load_yaml(legacy)

    tier_mapping_path = _as_repo_path(Path(tier_mapping_path)) if tier_mapping_path else (
        DEFAULT_TIER_MAPPING_LOCAL if DEFAULT_TIER_MAPPING_LOCAL.exists() else DEFAULT_TIER_MAPPING
    )
    tier_candidates_path = _as_repo_path(Path(tier_candidates_path)) if tier_candidates_path else (
        DEFAULT_TIER_CANDIDATES_LOCAL if DEFAULT_TIER_CANDIDATES_LOCAL.exists() else DEFAULT_TIER_CANDIDATES
    )

    providers = base.get("providers", {})
    models = base.get("models", {})
    tiers = base.get("tiers", {})
    tasks = base.get("tasks", {})

    # Allow tier override file
    mapping = _load_yaml(tier_mapping_path)
    if mapping.get("tasks"):
        for task_name, tier in mapping["tasks"].items():
            tasks.setdefault(task_name, {})
            tasks[task_name]["tier"] = tier

    # Allow tier candidates override file
    candidate_override = _load_yaml(tier_candidates_path)
    enable_candidates_override = os.getenv("LLM_ENABLE_TIER_CANDIDATES_OVERRIDE", "").lower() in ("1", "true", "yes", "on")
    if enable_candidates_override and candidate_override.get("tiers"):
        tiers = candidate_override["tiers"]

    return {
        "providers": providers,
        "models": models,
        "tiers": tiers,
        "tasks": tasks,
    }


def resolve_task(config: Dict[str, Any], task: str) -> Dict[str, Any]:
    """
    Return tier, candidate models, and default options for a given task.
    Falls back to standard tier if not specified.
    """
    tasks = config.get("tasks", {}) or {}
    tiers = config.get("tiers", {}) or {}

    task_conf = tasks.get(task, {})
    tier_name = task_conf.get("tier") or "standard"
    explicit_models = task_conf.get("models")
    model_candidates = list(explicit_models) if isinstance(explicit_models, list) and explicit_models else tiers.get(tier_name, [])
    defaults = task_conf.get("defaults", {}) or {}

    return {
        "task": task,
        "tier": tier_name,
        "models": model_candidates,
        "defaults": defaults,
    }
