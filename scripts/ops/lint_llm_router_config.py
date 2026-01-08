#!/usr/bin/env python3
"""
lint_llm_router_config — slot-based LLM routing SSOT sanity checks (deterministic; no network)

Validates that:
  - configs/llm_router.yaml parses and references are consistent
    - models.*.provider exists in providers
    - tiers.* references existing models
    - tasks.*.tier (when set) exists in tiers
  - configs/llm_model_codes.yaml codes resolve to existing llm_router models
  - configs/llm_model_slots.yaml slot tiers/script_tiers resolve (codes -> model_key -> models)
  - configs/llm_task_overrides.yaml override models resolve (codes -> model_key -> models)
  - Policy guard: script_* tasks must not route to provider=azure

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
        return {}, f"missing: {path.as_posix()}"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        return {}, f"failed to parse yaml: {path.as_posix()}: {exc}"
    if not isinstance(data, dict):
        return {}, f"expected mapping at top-level: {path.as_posix()}"
    return data, None


def _pick_local_or_base(local_path: Path, base_path: Path, *, prefer_local: bool) -> Path:
    if prefer_local and local_path.exists():
        return local_path
    return base_path


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_str(value: Any) -> str:
    return str(value or "").strip()


def _load_model_code_map(codes_cfg: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    raw_codes = codes_cfg.get("codes")
    if raw_codes is None:
        return {}, []
    if not isinstance(raw_codes, dict):
        return {}, ["codes must be a mapping: configs/llm_model_codes.yaml"]

    code_to_model_key: dict[str, str] = {}
    for code, ent in raw_codes.items():
        c = _as_str(code)
        if not c:
            errors.append("empty code in llm_model_codes.yaml")
            continue
        if isinstance(ent, str):
            mk = _as_str(ent)
        elif isinstance(ent, dict):
            mk = _as_str(ent.get("model_key"))
        else:
            errors.append(f"codes.{c} must be mapping or string")
            continue
        if not mk:
            errors.append(f"codes.{c}.model_key missing/empty")
            continue
        code_to_model_key[c] = mk
    return code_to_model_key, errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lint slot-based LLM routing configs (router + codes + slots + overrides).")
    ap.add_argument("--strict", action="store_true", help="treat warnings as errors")
    ap.add_argument(
        "--prefer-local",
        action="store_true",
        help="prefer *.local.yaml overrides when present (default: lint repo-tracked base files only)",
    )
    args = ap.parse_args(argv)

    root = repo_root()
    router_path = _pick_local_or_base(
        root / "configs" / "llm_router.local.yaml",
        root / "configs" / "llm_router.yaml",
        prefer_local=bool(args.prefer_local),
    )
    codes_path = _pick_local_or_base(
        root / "configs" / "llm_model_codes.local.yaml",
        root / "configs" / "llm_model_codes.yaml",
        prefer_local=bool(args.prefer_local),
    )
    slots_path = _pick_local_or_base(
        root / "configs" / "llm_model_slots.local.yaml",
        root / "configs" / "llm_model_slots.yaml",
        prefer_local=bool(args.prefer_local),
    )
    overrides_path = _pick_local_or_base(
        root / "configs" / "llm_task_overrides.local.yaml",
        root / "configs" / "llm_task_overrides.yaml",
        prefer_local=bool(args.prefer_local),
    )

    errors: list[str] = []
    warnings: list[str] = []

    router, err = _load_yaml(router_path)
    if err:
        errors.append(err)

    providers = _as_dict(router.get("providers"))
    models = _as_dict(router.get("models"))
    tiers = _as_dict(router.get("tiers"))
    tasks = _as_dict(router.get("tasks"))

    if not providers:
        errors.append(f"providers missing/empty: {router_path.as_posix()}")
    if not models:
        errors.append(f"models missing/empty: {router_path.as_posix()}")
    if not tiers:
        errors.append(f"tiers missing/empty: {router_path.as_posix()}")
    if not tasks:
        warnings.append(f"tasks missing/empty: {router_path.as_posix()} (ok if all tasks live in overrides)")

    # models.*.provider
    for model_key, model_cfg in models.items():
        mk = _as_str(model_key)
        if not mk:
            errors.append(f"models has empty key: {router_path.as_posix()}")
            continue
        if not isinstance(model_cfg, dict):
            errors.append(f"models.{mk} must be a mapping: {router_path.as_posix()}")
            continue
        provider = _as_str(model_cfg.get("provider"))
        if not provider:
            errors.append(f"models.{mk}.provider missing: {router_path.as_posix()}")
            continue
        if provider not in providers:
            errors.append(f"models.{mk}.provider={provider!r} not in providers: {router_path.as_posix()}")
        if not _as_str(model_cfg.get("deployment")) and not _as_str(model_cfg.get("model_name")):
            warnings.append(f"models.{mk} has neither deployment nor model_name: {router_path.as_posix()}")

    # tiers.* -> models
    for tier_name, tier_models in tiers.items():
        tn = _as_str(tier_name)
        if not tn:
            errors.append(f"tiers has empty key: {router_path.as_posix()}")
            continue
        if not isinstance(tier_models, list):
            errors.append(f"tiers.{tn} must be a list: {router_path.as_posix()}")
            continue
        for raw in tier_models:
            token = _as_str(raw)
            if not token:
                errors.append(f"tiers.{tn} contains empty model key: {router_path.as_posix()}")
                continue
            if token not in models:
                errors.append(f"tiers.{tn} references unknown model key: {token}: {router_path.as_posix()}")

    # tasks.*.tier
    for task_name, task_cfg in tasks.items():
        t = _as_str(task_name)
        if not t:
            errors.append(f"tasks has empty key: {router_path.as_posix()}")
            continue
        if not isinstance(task_cfg, dict):
            errors.append(f"tasks.{t} must be a mapping: {router_path.as_posix()}")
            continue
        tier = task_cfg.get("tier")
        if tier is None or _as_str(tier) == "":
            continue
        tier = _as_str(tier)
        if tier not in tiers:
            errors.append(f"tasks.{t}.tier references unknown tier: {tier}: {router_path.as_posix()}")

    codes_cfg, err = _load_yaml(codes_path)
    if err:
        errors.append(err)
        codes_cfg = {}

    code_to_model_key, code_errors = _load_model_code_map(codes_cfg)
    errors.extend(code_errors)

    # Validate codes -> models
    for code, model_key in code_to_model_key.items():
        if model_key not in models:
            errors.append(f"llm_model_codes: code {code!r} resolves to unknown model_key: {model_key}")

    def resolve_selector(token: str) -> str:
        raw = _as_str(token)
        return code_to_model_key.get(raw, raw)

    def provider_of(model_key: str) -> str:
        ent = models.get(model_key)
        if not isinstance(ent, dict):
            return ""
        return _as_str(ent.get("provider"))

    # Slot config
    slots_cfg, err = _load_yaml(slots_path)
    if err:
        errors.append(err)
        slots_cfg = {}
    slots = slots_cfg.get("slots") if isinstance(slots_cfg.get("slots"), dict) else {}
    if not isinstance(slots, dict):
        errors.append(f"slots must be a mapping: {slots_path.as_posix()}")
        slots = {}

    def _lint_slot_tiers(slot_id: str, kind: str, mapping: Any) -> None:
        if mapping is None:
            return
        if not isinstance(mapping, dict):
            errors.append(f"llm_model_slots: slots.{slot_id}.{kind} must be a mapping")
            return
        for tier_name, sel_list in mapping.items():
            tn = _as_str(tier_name)
            if not tn:
                errors.append(f"llm_model_slots: slots.{slot_id}.{kind} has empty tier name")
                continue
            if tn not in tiers:
                errors.append(f"llm_model_slots: slots.{slot_id}.{kind}.{tn} unknown tier (not in llm_router.yaml)")
                continue
            if not isinstance(sel_list, list):
                errors.append(f"llm_model_slots: slots.{slot_id}.{kind}.{tn} must be a list")
                continue
            for raw in sel_list:
                sel = _as_str(raw)
                if not sel:
                    errors.append(f"llm_model_slots: slots.{slot_id}.{kind}.{tn} has empty selector")
                    continue
                mk = resolve_selector(sel)
                if mk not in models:
                    errors.append(
                        f"llm_model_slots: slots.{slot_id}.{kind}.{tn} selector {sel!r} -> {mk!r} not in llm_router.models"
                    )
                    continue
                if kind == "script_tiers" and provider_of(mk) == "azure":
                    errors.append(
                        f"policy: script slot routing must not use azure provider (slot={slot_id} tier={tn} selector={sel!r} -> {mk})"
                    )

    for sid, ent in slots.items():
        try:
            sid_int = int(sid) if not isinstance(sid, bool) else None
        except Exception:
            sid_int = None
        slot_id = str(sid_int) if sid_int is not None else _as_str(sid)
        if not slot_id:
            errors.append("llm_model_slots: empty slot id key")
            continue
        if not isinstance(ent, dict):
            errors.append(f"llm_model_slots: slots.{slot_id} must be a mapping")
            continue
        _lint_slot_tiers(slot_id, "tiers", ent.get("tiers"))
        _lint_slot_tiers(slot_id, "script_tiers", ent.get("script_tiers"))

    # Task overrides
    overrides_cfg, err = _load_yaml(overrides_path)
    if err:
        errors.append(err)
        overrides_cfg = {}
    overrides_tasks = overrides_cfg.get("tasks") if isinstance(overrides_cfg.get("tasks"), dict) else {}
    if not isinstance(overrides_tasks, dict):
        errors.append(f"tasks must be a mapping: {overrides_path.as_posix()}")
        overrides_tasks = {}

    for task_name, ent in overrides_tasks.items():
        t = _as_str(task_name)
        if not t:
            errors.append("llm_task_overrides: empty task key")
            continue
        if not isinstance(ent, dict):
            errors.append(f"llm_task_overrides: tasks.{t} must be a mapping")
            continue
        tier = ent.get("tier")
        if tier is not None and _as_str(tier) != "":
            tn = _as_str(tier)
            if tn not in tiers:
                errors.append(f"llm_task_overrides: tasks.{t}.tier unknown: {tn}")
        model_list = ent.get("models")
        if model_list is None:
            continue
        if not isinstance(model_list, list):
            errors.append(f"llm_task_overrides: tasks.{t}.models must be a list")
            continue
        for raw in model_list:
            sel = _as_str(raw)
            if not sel:
                errors.append(f"llm_task_overrides: tasks.{t}.models has empty selector")
                continue
            mk = resolve_selector(sel)
            if mk not in models:
                errors.append(f"llm_task_overrides: tasks.{t}.models selector {sel!r} -> {mk!r} not in llm_router.models")
                continue
            if t.startswith("script_") and provider_of(mk) == "azure":
                errors.append(f"policy: script_* must not use azure provider (task={t} selector={sel!r} -> {mk})")

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

    print("✅ llm router config lint passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
