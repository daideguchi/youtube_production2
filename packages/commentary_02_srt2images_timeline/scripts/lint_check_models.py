#!/usr/bin/env python3
"""
Lint: verify model references across configs are consistent.
- Source of truth: configs/llm.yml (models)
- Legacy refs: configs/llm_router.yaml, configs/llm_model_registry.yaml
Checks:
 1) llm_router models keys must exist in SOT set (llm.yml ∪ llm_model_registry.yaml).
 2) Fail if allow_temperature is False but defaults.temperature is set.
Exit code 1 for any violation.
"""
from __future__ import annotations

import sys
import json
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


def main() -> int:
    errors: list[str] = []

    llm_yml = load_yaml(CFG / "llm.yml")
    sot_models = set((llm_yml.get("models") or {}).keys())

    legacy_registry = load_yaml(CFG / "llm_model_registry.yaml")
    legacy_models = set((legacy_registry.get("models") or {}).keys())

    allowed = sot_models | legacy_models

    router = load_yaml(CFG / "llm_router.yaml")
    router_models = set((router.get("models") or {}).keys())
    unknown_router = sorted(router_models - allowed)
    if unknown_router:
        errors.append(f"llm_router.yaml references unknown models: {', '.join(unknown_router)}")

    # Capability vs defaults sanity (error)
    for mid, mcfg in (llm_yml.get("models") or {}).items():
        caps = (mcfg or {}).get("capabilities") or {}
        defaults = (mcfg or {}).get("defaults") or {}
        allow_temp = caps.get("allow_temperature", True)
        if allow_temp is False and "temperature" in defaults:
            errors.append(f"{mid}: allow_temperature=False but defaults.temperature is set")

    if errors:
        print("❌ model lint failed:")
        for e in errors:
            print(" -", e)
        return 1

    print("✅ model lint passed (refs OK)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
