"""
LEGACY (compat/tests) — `configs/llm.yml` loader.

Normal ops routing SSOT:
- `configs/llm_router.yaml` + `configs/llm_task_overrides.yaml` (+ codes/slots/exec slots)
- See `ssot/DECISIONS.md:D-010`.

時点情報（git log --follow 根拠）:
- created: 2025-12-13
- updated: 2026-01-09
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

from factory_common.paths import repo_root

PROJECT_ROOT = repo_root()
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "llm.yml"
DEFAULT_CONFIG_LOCAL = PROJECT_ROOT / "configs" / "llm.local.yml"
DEFAULT_TIER_MAPPING = PROJECT_ROOT / "configs" / "llm_tier_mapping.yaml"
DEFAULT_TIER_MAPPING_LOCAL = PROJECT_ROOT / "configs" / "llm_tier_mapping.local.yaml"
DEFAULT_TIER_CANDIDATES = PROJECT_ROOT / "configs" / "llm_tier_candidates.yaml"
DEFAULT_TIER_CANDIDATES_LOCAL = PROJECT_ROOT / "configs" / "llm_tier_candidates.local.yaml"

# Compatibility fallback (router-era config)
LEGACY_ROUTER = PROJECT_ROOT / "configs" / "llm_router.yaml"


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_llm_config(
    config_path: Path | str | None = None,
    tier_mapping_path: Path | str | None = None,
    tier_candidates_path: Path | str | None = None,
) -> Dict[str, Any]:
    """
    Load the unified LLM config with optional tier/task overrides.
    Falls back to router-era config (llm_router.yaml) if the unified config is absent.
    """
    config_path = Path(config_path) if config_path else (DEFAULT_CONFIG_LOCAL if DEFAULT_CONFIG_LOCAL.exists() else DEFAULT_CONFIG)
    tier_mapping_path = Path(tier_mapping_path) if tier_mapping_path else (
        DEFAULT_TIER_MAPPING_LOCAL if DEFAULT_TIER_MAPPING_LOCAL.exists() else DEFAULT_TIER_MAPPING
    )
    tier_candidates_path = Path(tier_candidates_path) if tier_candidates_path else (
        DEFAULT_TIER_CANDIDATES_LOCAL if DEFAULT_TIER_CANDIDATES_LOCAL.exists() else DEFAULT_TIER_CANDIDATES
    )

    base = _load_yaml(config_path)
    providers = base.get("providers", {})
    models = base.get("models", {})
    tiers = base.get("tiers", {})
    tasks = base.get("tasks", {})

    if not base:
        base = _load_yaml(LEGACY_ROUTER)
        providers = providers or base.get("providers", {})
        models = models or base.get("models", {})
        tiers = tiers or base.get("tiers", {})
        tasks = tasks or base.get("tasks", {})

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
    model_candidates = tiers.get(tier_name, [])
    defaults = task_conf.get("defaults", {}) or {}

    return {
        "task": task,
        "tier": tier_name,
        "models": model_candidates,
        "defaults": defaults,
    }
