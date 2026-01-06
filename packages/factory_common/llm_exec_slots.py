from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from factory_common import paths as repo_paths
from factory_common.routing_lockdown import assert_env_absent

PROJECT_ROOT = repo_paths.repo_root()
_DEFAULT_CONFIG_PATH = PROJECT_ROOT / "configs" / "llm_exec_slots.yaml"
_LOCAL_CONFIG_PATH = PROJECT_ROOT / "configs" / "llm_exec_slots.local.yaml"


def _boolish(value: Any) -> bool:
    if value is True:
        return True
    s = str(value or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


@lru_cache(maxsize=1)
def load_llm_exec_slots_config() -> Dict[str, Any]:
    """
    Load the execution-slot config (no secrets).

    Base: configs/llm_exec_slots.yaml
    Local: configs/llm_exec_slots.local.yaml (override; not tracked)
    """
    base: Dict[str, Any] = {}
    local: Dict[str, Any] = {}

    if _DEFAULT_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(_DEFAULT_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                base = raw
        except Exception:
            base = {}

    if _LOCAL_CONFIG_PATH.exists():
        try:
            raw = yaml.safe_load(_LOCAL_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            if isinstance(raw, dict):
                local = raw
        except Exception:
            local = {}

    if local:
        return _deep_merge(base, local)
    return base


def _slot_int(value: Any, *, default: int) -> int:
    try:
        n = int(value)
    except Exception:
        return int(default)
    return max(0, n)


def _slots_map(cfg: Dict[str, Any]) -> Dict[Any, Any]:
    slots = cfg.get("slots") if isinstance(cfg, dict) else None
    return slots if isinstance(slots, dict) else {}


def _slot_entry(cfg: Dict[str, Any], slot_id: int) -> Optional[Dict[str, Any]]:
    slots = _slots_map(cfg)
    ent = slots.get(slot_id)
    if ent is None:
        ent = slots.get(str(slot_id))
    return ent if isinstance(ent, dict) else None


def active_llm_exec_slot_id() -> Dict[str, Any]:
    """
    Returns:
      {"id": int, "source": "env"|"default"}
    """
    cfg = load_llm_exec_slots_config()
    default_slot = _slot_int(cfg.get("default_slot") if isinstance(cfg, dict) else 0, default=0)

    raw = (os.getenv("LLM_EXEC_SLOT") or "").strip()
    if raw:
        try:
            slot = max(0, int(raw))
            return {"id": slot, "source": "env"}
        except Exception:
            return {"id": default_slot, "source": "default"}
    return {"id": default_slot, "source": "default"}


def effective_llm_mode() -> str:
    """
    Effective LLM_MODE with exec-slot fallback.

    Priority:
      1) explicit env LLM_MODE (if valid)
      2) exec-slot (LLM_EXEC_SLOT → configs/llm_exec_slots.yaml)
      3) default: "api"
    """
    assert_env_absent(
        ["LLM_MODE"],
        context="llm_exec_slots.effective_llm_mode",
        hint="Use LLM_EXEC_SLOT (e.g. --exec-slot 3 for THINK, --exec-slot 4 for AGENT).",
    )
    raw = (os.getenv("LLM_MODE") or "").strip().lower()
    if raw in {"api", "agent", "think"}:
        return raw

    cfg = load_llm_exec_slots_config()
    active = active_llm_exec_slot_id()
    slot_id = int(active.get("id") or 0)
    ent = _slot_entry(cfg, slot_id) or _slot_entry(cfg, _slot_int(cfg.get("default_slot"), default=0))
    mode = str((ent or {}).get("llm_mode") or "").strip().lower()
    return mode if mode in {"api", "agent", "think"} else "api"


def codex_exec_enabled_override() -> Optional[bool]:
    """
    Optional boolean override for Codex exec enable/disable.

    Slot override applies only when *no explicit env override exists*.
    """
    assert_env_absent(
        [
            "YTM_CODEX_EXEC_ENABLED",
            "YTM_CODEX_EXEC_DISABLE",
            "YTM_CODEX_EXEC_PROFILE",
            "YTM_CODEX_EXEC_MODEL",
            "YTM_CODEX_EXEC_TIMEOUT_S",
            "YTM_CODEX_EXEC_SANDBOX",
            "YTM_CODEX_EXEC_EXCLUDE_TASKS",
            "YTM_CODEX_EXEC_ENABLE_IN_PYTEST",
        ],
        context="llm_exec_slots.codex_exec_enabled_override",
        hint="Use LLM_EXEC_SLOT=1 (force codex on) / 2 (force codex off), and configs/codex_exec.yaml for defaults.",
    )
    if (os.getenv("YTM_CODEX_EXEC_ENABLED") or "").strip() != "":
        return None
    if (os.getenv("YTM_CODEX_EXEC_DISABLE") or "").strip() != "":
        return None

    cfg = load_llm_exec_slots_config()
    active = active_llm_exec_slot_id()
    slot_id = int(active.get("id") or 0)
    ent = _slot_entry(cfg, slot_id) or None
    codex = ent.get("codex_exec") if isinstance(ent, dict) else None
    if not isinstance(codex, dict) or "enabled" not in codex:
        return None
    return _boolish(codex.get("enabled"))


def effective_api_failover_to_think() -> bool:
    """
    Effective API→THINK failover toggle for non-script tasks.

    Priority:
      1) explicit env LLM_API_FAILOVER_TO_THINK / LLM_API_FALLBACK_TO_THINK
      2) exec-slot api_failover_to_think (if present)
      3) default: True
    """
    assert_env_absent(
        ["LLM_API_FAILOVER_TO_THINK", "LLM_API_FALLBACK_TO_THINK"],
        context="llm_exec_slots.effective_api_failover_to_think",
        hint="Use LLM_EXEC_SLOT=5 to disable failover (non-script tasks). Default is ON.",
    )
    raw = (os.getenv("LLM_API_FAILOVER_TO_THINK") or os.getenv("LLM_API_FALLBACK_TO_THINK") or "").strip()
    if raw != "":
        return raw.lower() not in {"0", "false", "no", "off"}

    cfg = load_llm_exec_slots_config()
    active = active_llm_exec_slot_id()
    slot_id = int(active.get("id") or 0)
    ent = _slot_entry(cfg, slot_id) or None
    if isinstance(ent, dict) and "api_failover_to_think" in ent:
        return _boolish(ent.get("api_failover_to_think"))
    return True
