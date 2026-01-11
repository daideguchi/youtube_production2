#!/usr/bin/env python3
"""
lint_llm_router_config — slot-based LLM routing SSOT sanity checks (deterministic; no network)

Validates that:
  - configs/llm_router.yaml parses and references are consistent
    - models.*.provider exists in providers
    - tiers.* references existing models
    - tasks.*.tier (when set) exists in tiers
    - policy: tiers.* is single-model (no automatic fallback)
  - configs/llm_model_codes.yaml codes resolve to existing llm_router models
  - configs/llm_model_slots.yaml slot tiers/script_tiers resolve (codes -> model_key -> models)
    - policy: slot tiers/script_tiers are single-model (no automatic fallback)
  - configs/llm_task_overrides.yaml override models resolve (codes -> model_key -> models)
    - policy: allow_fallback=true is forbidden (no silent model/provider swap)
    - policy: script_* tasks must use models=['script-main-1'] (fixed)
    - policy: script_* tasks must not route to provider=azure/openrouter (Fireworks-only by default)
  - configs/codex_exec.yaml policy guards
    - policy: codex exec must not include `script_*` tasks

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
    codex_path = _pick_local_or_base(
        root / "configs" / "codex_exec.local.yaml",
        root / "configs" / "codex_exec.yaml",
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

    def _provider_for_model_key(model_key: str) -> str:
        cfg = models.get(model_key)
        if not isinstance(cfg, dict):
            return ""
        return _as_str(cfg.get("provider"))

    # tiers.* -> models
    for tier_name, tier_models in tiers.items():
        tn = _as_str(tier_name)
        if not tn:
            errors.append(f"tiers has empty key: {router_path.as_posix()}")
            continue
        if not isinstance(tier_models, list):
            errors.append(f"tiers.{tn} must be a list: {router_path.as_posix()}")
            continue
        # Policy: avoid automatic fallback by keeping each tier single-model.
        normalized = [_as_str(x) for x in tier_models if _as_str(x)]
        if len(normalized) != 1:
            errors.append(f"policy: tiers.{tn} must contain exactly 1 model (no automatic fallback): {router_path.as_posix()}")
        tier_providers_set: set[str] = set()
        for tok in normalized:
            prov = _provider_for_model_key(tok)
            if prov:
                tier_providers_set.add(prov)
        tier_providers = sorted(tier_providers_set)
        if len(tier_providers) > 1:
            errors.append(
                f"policy: tiers.{tn} mixes providers {tier_providers} (no LLM API fallback allowed): {router_path.as_posix()}"
            )
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

    # Policy: script-main-1 is fixed to Fireworks DeepSeek V3.2 exp (no silent swap).
    if "script-main-1" not in code_to_model_key:
        errors.append(f"policy: llm_model_codes: missing required code 'script-main-1' ({codes_path.as_posix()})")
    else:
        mk = resolve_selector("script-main-1")
        if mk not in models:
            errors.append(
                f"policy: llm_model_codes: 'script-main-1' -> {mk!r} not in llm_router.models ({router_path.as_posix()})"
            )
        else:
            prov = provider_of(mk)
            if prov != "fireworks":
                errors.append(f"policy: script-main-1 must use provider='fireworks' (got {prov!r}; model_key={mk})")
            ent = models.get(mk) if isinstance(models.get(mk), dict) else {}
            model_name = _as_str(ent.get("model_name"))
            if model_name != "deepseek/deepseek-v3.2-exp":
                errors.append(
                    "policy: script-main-1 must resolve to model_name='deepseek/deepseek-v3.2-exp' "
                    f"(got {model_name!r}; model_key={mk})"
                )

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
            normalized = [_as_str(x) for x in sel_list if _as_str(x)]
            if len(normalized) != 1:
                errors.append(
                    f"policy: llm_model_slots: slots.{slot_id}.{kind}.{tn} must contain exactly 1 selector (no automatic fallback)"
                )
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
        if ent.get("allow_fallback") is True:
            errors.append(f"policy: llm_task_overrides: tasks.{t}.allow_fallback=true is forbidden (no automatic fallback)")
        tier = ent.get("tier")
        if tier is not None and _as_str(tier) != "":
            tn = _as_str(tier)
            if tn not in tiers:
                errors.append(f"llm_task_overrides: tasks.{t}.tier unknown: {tn}")
        model_list = ent.get("models")
        if model_list is None:
            if t.startswith("script_"):
                errors.append(
                    f"policy: llm_task_overrides: tasks.{t}.models is required for script_* (must be ['script-main-1'])"
                )
            continue
        if not isinstance(model_list, list):
            errors.append(f"llm_task_overrides: tasks.{t}.models must be a list")
            continue
        normalized = [_as_str(x) for x in model_list if _as_str(x)]
        if len(normalized) != 1:
            errors.append(f"policy: llm_task_overrides: tasks.{t}.models must contain exactly 1 selector (no automatic fallback)")
        if t.startswith("script_") and normalized and normalized[0] != "script-main-1":
            errors.append(
                f"policy: llm_task_overrides: tasks.{t}.models must be ['script-main-1'] (got {normalized[0]!r})"
            )
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
            if t.startswith("script_") and provider_of(mk) == "openrouter":
                errors.append(f"policy: script_* must not use openrouter provider (task={t} selector={sel!r} -> {mk})")

    # Codex exec policy: do not allow script_* tasks.
    codex_cfg, err = _load_yaml(codex_path)
    if err:
        errors.append(err)
        codex_cfg = {}
    selection = _as_dict(codex_cfg.get("selection"))
    prefixes = [_as_str(x) for x in _as_list(selection.get("include_task_prefixes")) if _as_str(x)]
    include_tasks = [_as_str(x) for x in _as_list(selection.get("include_tasks")) if _as_str(x)]
    if "script_" in prefixes:
        errors.append(f"policy: codex_exec.yaml: selection.include_task_prefixes must not include 'script_' ({codex_path.as_posix()})")
    script_includes = [t for t in include_tasks if t.startswith("script_")]
    if script_includes:
        errors.append(
            f"policy: codex_exec.yaml: selection.include_tasks must not include script_* tasks: {script_includes} ({codex_path.as_posix()})"
        )

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
