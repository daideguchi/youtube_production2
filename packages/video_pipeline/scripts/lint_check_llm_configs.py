#!/usr/bin/env python3
"""
Cross-check LLM config consistency without changing runtime.
Checks:
  - tasks/tier/model references in llm_router.yaml are defined in allowed models
  - tier definitions reference existing models
  - llm_registry.json task -> model references exist
  - llm.yml tasks reference existing tiers/models (self-consistency)
Source of truth set = llm.yml.models ∪ llm_model_registry.yaml.models
Fails on any missing reference.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

from factory_common.paths import repo_root

ROOT = repo_root()
CFG = ROOT / "configs"


def load_yaml(path: Path):
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover
        print(f"⚠️ failed to parse {path}: {exc}")
        return {}


def load_json(path: Path):
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover
        print(f"⚠️ failed to parse {path}: {exc}")
        return {}


def main() -> int:
    errors: list[str] = []

    llm_yml = load_yaml(CFG / "llm.yml")
    sot_models = set((llm_yml.get("models") or {}).keys())

    legacy_registry = load_yaml(CFG / "llm_model_registry.yaml")
    legacy_models = set((legacy_registry.get("models") or {}).keys())

    allowed_models = sot_models | legacy_models

    # llm_router.yaml
    router = load_yaml(CFG / "llm_router.yaml")
    router_models = set((router.get("models") or {}).keys())
    unknown_router_models = router_models - allowed_models
    if unknown_router_models:
        errors.append("llm_router.yaml defines unknown models: " + ", ".join(sorted(unknown_router_models)))

    tiers = router.get("tiers") or {}
    for tier_name, tier_models in tiers.items():
        for m in tier_models or []:
            if m not in allowed_models:
                errors.append(f"llm_router.yaml tier '{tier_name}' references unknown model '{m}'")

    tasks = router.get("tasks") or {}
    for tname, tconf in tasks.items():
        tier = tconf.get("tier")
        if tier and tier not in tiers:
            errors.append(f"llm_router.yaml task '{tname}' references unknown tier '{tier}'")

    # llm.yml self-consistency (tasks -> tier -> model)
    llm_tasks = llm_yml.get("tasks") or {}
    llm_tiers = llm_yml.get("tiers") or {}
    for tname, tconf in llm_tasks.items():
        tier = tconf.get("tier")
        if tier and tier not in llm_tiers:
            errors.append(f"llm.yml task '{tname}' references unknown tier '{tier}'")
        models = llm_tiers.get(tier, []) if tier else []
        for m in models:
            if m not in allowed_models:
                errors.append(f"llm.yml tier '{tier}' (task '{tname}') references unknown model '{m}'")

    # llm_registry.json (legacy)
    llm_reg = load_json(CFG / "llm_registry.json")
    for tname, tconf in (llm_reg.get("tasks") or {}).items():
        model = tconf.get("model")
        if model and model not in allowed_models:
            errors.append(f"llm_registry.json task '{tname}' references unknown model '{model}'")

    if errors:
        print("❌ llm config lint failed:")
        for e in errors:
            print(" -", e)
        return 1

    print("✅ llm config lint passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
