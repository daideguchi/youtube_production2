#!/usr/bin/env python3
"""
lint_llm_config — unified LLM config sanity checks (deterministic; no network)

Validates that:
  - configs/llm.yml parses and has expected sections
  - models reference existing providers
  - tiers reference existing models
  - tasks reference existing tiers (if tier is set)
  - llm_tier_mapping.yaml references existing tiers
  - llm_tier_candidates.yaml (if present) references existing models

Exit code:
  - 0: ok (warnings allowed unless --strict)
  - 1: errors (or warnings with --strict)
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import repo_root  # noqa: E402


def _load_yaml(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {}, f"failed to parse yaml: {path}: {exc}"
    if not isinstance(data, dict):
        return {}, f"expected mapping at top-level: {path}"
    return data, None


def _pick_local_or_base(local_path: Path, base_path: Path, *, prefer_local: bool) -> Path:
    if prefer_local and local_path.exists():
        return local_path
    return base_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Lint unified LLM config references")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument(
        "--prefer-local",
        action="store_true",
        help="prefer *.local.* overrides when present (default: lint repo-tracked base files only)",
    )
    args = ap.parse_args()

    root = repo_root()
    cfg_path = _pick_local_or_base(root / "configs" / "llm.local.yml", root / "configs" / "llm.yml", prefer_local=bool(args.prefer_local))
    mapping_path = _pick_local_or_base(
        root / "configs" / "llm_tier_mapping.local.yaml",
        root / "configs" / "llm_tier_mapping.yaml",
        prefer_local=bool(args.prefer_local),
    )
    candidates_path = _pick_local_or_base(
        root / "configs" / "llm_tier_candidates.local.yaml",
        root / "configs" / "llm_tier_candidates.yaml",
        prefer_local=bool(args.prefer_local),
    )

    errors: list[str] = []
    warnings: list[str] = []

    cfg, err = _load_yaml(cfg_path)
    if err:
        errors.append(err)

    providers = cfg.get("providers", {})
    models = cfg.get("models", {})
    tiers = cfg.get("tiers", {})
    tasks = cfg.get("tasks", {})

    if providers and not isinstance(providers, dict):
        errors.append(f"providers must be a mapping: {cfg_path}")
        providers = {}
    if models and not isinstance(models, dict):
        errors.append(f"models must be a mapping: {cfg_path}")
        models = {}
    if tiers and not isinstance(tiers, dict):
        errors.append(f"tiers must be a mapping: {cfg_path}")
        tiers = {}
    if tasks and not isinstance(tasks, dict):
        errors.append(f"tasks must be a mapping: {cfg_path}")
        tasks = {}

    # Model -> provider references
    for model_id, model_cfg in (models or {}).items():
        if not isinstance(model_cfg, dict):
            errors.append(f"models.{model_id} must be a mapping: {cfg_path}")
            continue
        provider = str(model_cfg.get("provider") or "").strip()
        if not provider:
            errors.append(f"models.{model_id}.provider missing: {cfg_path}")
            continue
        if provider not in (providers or {}):
            errors.append(f"models.{model_id}.provider={provider!r} not defined in providers: {cfg_path}")

    # Tier -> model references
    for tier_name, candidates in (tiers or {}).items():
        if not isinstance(candidates, list):
            errors.append(f"tiers.{tier_name} must be a list of model ids: {cfg_path}")
            continue
        for model_id in candidates:
            if not isinstance(model_id, str) or not model_id.strip():
                errors.append(f"tiers.{tier_name} contains invalid model id: {model_id!r}: {cfg_path}")
                continue
            if model_id not in (models or {}):
                errors.append(f"tiers.{tier_name} references unknown model id: {model_id}: {cfg_path}")

    # Task -> tier references (tier optional; fallback allowed)
    for task_name, task_cfg in (tasks or {}).items():
        if not isinstance(task_cfg, dict):
            errors.append(f"tasks.{task_name} must be a mapping: {cfg_path}")
            continue
        tier = task_cfg.get("tier")
        if tier is None or str(tier).strip() == "":
            continue
        tier = str(tier).strip()
        if tier not in (tiers or {}):
            errors.append(f"tasks.{task_name}.tier references unknown tier: {tier}: {cfg_path}")

    # Tier mapping overrides
    mapping, err = _load_yaml(mapping_path)
    if err:
        errors.append(err)
    mapping_tasks = mapping.get("tasks", {})
    if mapping_tasks and not isinstance(mapping_tasks, dict):
        errors.append(f"expected mapping at tasks: {mapping_path}")
        mapping_tasks = {}
    for task_name, tier in (mapping_tasks or {}).items():
        if tier is None or str(tier).strip() == "":
            warnings.append(f"llm_tier_mapping: tasks.{task_name} has empty tier (ignored): {mapping_path}")
            continue
        tier = str(tier).strip()
        if tier not in (tiers or {}):
            errors.append(f"llm_tier_mapping: tasks.{task_name} references unknown tier: {tier}: {mapping_path}")
        if task_name not in (tasks or {}):
            warnings.append(f"llm_tier_mapping: tasks.{task_name} not present in llm.yml tasks (may be OK): {mapping_path}")

    # Tier candidates override (optional)
    cand, err = _load_yaml(candidates_path)
    if err:
        errors.append(err)
    cand_tiers = cand.get("tiers", {})
    if cand_tiers and not isinstance(cand_tiers, dict):
        errors.append(f"expected mapping at tiers: {candidates_path}")
        cand_tiers = {}
    for tier_name, candidates in (cand_tiers or {}).items():
        if not isinstance(candidates, list):
            errors.append(f"llm_tier_candidates: tiers.{tier_name} must be a list of model ids: {candidates_path}")
            continue
        for model_id in candidates:
            if not isinstance(model_id, str) or not model_id.strip():
                errors.append(f"llm_tier_candidates: tiers.{tier_name} contains invalid model id: {model_id!r}: {candidates_path}")
                continue
            if model_id not in (models or {}):
                errors.append(f"llm_tier_candidates: tiers.{tier_name} references unknown model id: {model_id}: {candidates_path}")

    if warnings:
        print("⚠️ warnings:")
        for w in warnings:
            print(f"- {w}")

    if errors:
        print("❌ errors:")
        for e in errors:
            print(f"- {e}")
        return 1

    if warnings and args.strict:
        print("❌ strict mode: warnings are treated as errors")
        return 1

    print("✅ LLM config lint passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
