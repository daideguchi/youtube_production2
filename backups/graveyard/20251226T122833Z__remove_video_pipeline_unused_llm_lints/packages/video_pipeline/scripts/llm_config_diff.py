#!/usr/bin/env python3
"""
Compare llm.yml vs legacy llm_router.yaml/llm_model_registry.yaml/llm_registry.json
Outputs a short diff report to stdout.
Non-fatal: returns 0; intended for manual inspection.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Set

import yaml

from factory_common.paths import repo_root

ROOT = repo_root()
CFG = ROOT / "configs"


def load_yaml(path: Path) -> Dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")) or {}


def main() -> int:
    llm = load_yaml(CFG / "llm.yml")
    router = load_yaml(CFG / "llm_router.yaml")
    legacy_reg = load_yaml(CFG / "llm_model_registry.yaml")
    llm_reg_json = load_json(CFG / "llm_registry.json")

    sot_models: Set[str] = set((llm.get("models") or {}).keys())
    router_models: Set[str] = set((router.get("models") or {}).keys())
    legacy_models: Set[str] = set((legacy_reg.get("models") or {}).keys())
    allowed = sot_models | legacy_models

    print("=== Model sets ===")
    print(f"SOT (llm.yml): {len(sot_models)}")
    print(f"router models : {len(router_models)}")
    print(f"legacy models : {len(legacy_models)}")
    if extra := router_models - allowed:
        print("router-only (missing in SOT):", ", ".join(sorted(extra)))
    if missing := sot_models - router_models:
        print("SOT-only (not in router):", ", ".join(sorted(missing)))

    print("\n=== Tasks (llm.yml vs llm_registry.json) ===")
    tasks_llm = set((llm.get("tasks") or {}).keys())
    tasks_json = set((llm_reg_json.get("tasks") or {}).keys())
    print(f"llm.yml tasks     : {len(tasks_llm)}")
    print(f"llm_registry.json : {len(tasks_json)}")
    if extra := tasks_json - tasks_llm:
        print("legacy-only tasks:", ", ".join(sorted(extra)))
    if missing := tasks_llm - tasks_json:
        print("new-only tasks   :", ", ".join(sorted(missing)))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
