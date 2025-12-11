from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]  # /Users/dd/10_YouTube_Automation/factory_commentary
# Prefer top-level configs/llm.yml (repo root), fallback to module-local configs (commentary_02_srt2images_timeline/configs)
LLM_CONFIG_PATH = PROJECT_ROOT / "configs" / "llm.yml"
LLM_CONFIG_FALLBACK = Path(__file__).resolve().parents[1] / "configs" / "llm.yml"


@lru_cache(maxsize=1)
def _load_llm_config() -> Dict:
    path = LLM_CONFIG_PATH if LLM_CONFIG_PATH.exists() else LLM_CONFIG_FALLBACK
    if not path.exists():
        logger.warning("llm.yml not found: %s", path)
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover
        logger.error("Failed to load llm.yml: %s", exc)
        return {}


def get_task_config(task: str) -> Optional[Dict]:
    cfg = _load_llm_config()
    return (cfg.get("tasks") or {}).get(task)


def get_model_config(model_id: str) -> Optional[Dict]:
    cfg = _load_llm_config()
    return (cfg.get("models") or {}).get(model_id)


def get_tier_models(tier: str) -> List[str]:
    cfg = _load_llm_config()
    return list((cfg.get("tiers") or {}).get(tier, []) or [])


def resolve_task(task: str) -> Optional[Dict[str, str]]:
    """
    Resolve tier and first model for a task from llm.yml.
    Returns dict {tier, model} or None if task/tier missing.
    """
    tconf = get_task_config(task)
    if not tconf:
        return None
    tier = tconf.get("tier")
    models = get_tier_models(tier) if tier else []
    model = models[0] if models else None
    return {"tier": tier, "model": model}
