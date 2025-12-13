from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from factory_common.agent_mode import PROJECT_ROOT, compute_task_id
from factory_common.paths import logs_root

SCHEMA_VERSION = 1

# NOTE:
# This is an app-level cache to reduce repeated API spend when the same task+messages+options
# are executed again (reruns, retries, "run the same pipeline" workflows).
#
# It is intentionally separate from "THINK MODE" agent-queue results caching.
#
# Env toggles:
# - LLM_API_CACHE_DISABLE=1              -> disable entirely
# - LLM_API_CACHE_READ_DISABLE=1         -> never read cache (still writes unless write disabled)
# - LLM_API_CACHE_WRITE_DISABLE=1        -> never write cache
# - LLM_API_CACHE_DIR=/path             -> override cache dir (default: logs/llm_api_cache)
# - LLM_API_CACHE_TTL_SEC=...           -> optional TTL (0/empty = no TTL)
# - LLM_API_CACHE_EXCLUDE_TASKS=csv     -> additional exact task excludes
# - LLM_API_CACHE_EXCLUDE_PREFIXES=csv  -> additional prefix excludes
# - LLM_API_CACHE_PURGE_EXPIRED=1       -> delete expired entries
#
# Default behavior:
# - Enabled (unless *_DISABLE=1)
# - Excludes image-generation tasks by default (they are handled by file outputs elsewhere)

_DEFAULT_EXCLUDE_TASKS = {
    "visual_image_gen",
    "image_generation",
    "image_generation_gemini3",
}


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truthy_env(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def cache_enabled() -> bool:
    return not _truthy_env("LLM_API_CACHE_DISABLE")


def cache_dir() -> Path:
    raw = (os.getenv("LLM_API_CACHE_DIR") or "").strip()
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p)
    return logs_root() / "llm_api_cache"


def _ttl_sec() -> int:
    raw = (os.getenv("LLM_API_CACHE_TTL_SEC") or "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except Exception:
        return 0


def _purge_expired() -> bool:
    return _truthy_env("LLM_API_CACHE_PURGE_EXPIRED")


def _exclude_tasks() -> set[str]:
    raw = (os.getenv("LLM_API_CACHE_EXCLUDE_TASKS") or "").strip()
    extra = {t.strip() for t in raw.split(",") if t.strip()} if raw else set()
    return set(_DEFAULT_EXCLUDE_TASKS) | extra


def _exclude_prefixes() -> List[str]:
    raw = (os.getenv("LLM_API_CACHE_EXCLUDE_PREFIXES") or "").strip()
    return [p.strip() for p in raw.split(",") if p.strip()] if raw else []


def cache_enabled_for_task(task: str) -> bool:
    if not cache_enabled():
        return False
    if task in _exclude_tasks():
        return False
    if any(task.startswith(p) for p in _exclude_prefixes()):
        return False
    return True


def make_task_id(task: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> str:
    # For API caching, treat max-token limits as transport-level controls, not semantics.
    # This prevents cache misses when we auto-retry on truncation with a higher token cap.
    cleaned = dict(options or {})
    for k in ("max_tokens", "max_output_tokens", "max_completion_tokens"):
        cleaned.pop(k, None)
    return compute_task_id(task, messages, cleaned)


def _safe_task_dirname(task: str) -> str:
    # Keep paths stable even if task contains odd chars.
    # Most tasks are already safe (snake_case), but be defensive.
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", task.strip())
    return cleaned or "task"


def cache_path(task_id: str) -> Path:
    task, digest = (task_id.split("__", 1) + [""])[:2]
    digest = digest or task_id
    base = cache_dir() / _safe_task_dirname(task)
    # Shard by 2 chars to avoid huge directories.
    return base / digest[:2] / f"{digest}.json"


def read_cache(task: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if _truthy_env("LLM_API_CACHE_READ_DISABLE"):
        return None
    if not cache_enabled_for_task(task):
        return None

    try:
        task_id = make_task_id(task, messages, options)
    except Exception:
        return None
    path = cache_path(task_id)
    if not path.exists():
        return None

    ttl = _ttl_sec()
    if ttl:
        try:
            age = time.time() - path.stat().st_mtime
            if age > ttl:
                if _purge_expired():
                    try:
                        path.unlink()
                    except Exception:
                        pass
                return None
        except Exception:
            # If we can't stat, don't trust it.
            return None

    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return None
        if obj.get("schema_version") != SCHEMA_VERSION:
            return None
        if obj.get("task_id") != task_id:
            return None
        return obj
    except Exception:
        return None


def write_cache(task: str, messages: List[Dict[str, str]], options: Dict[str, Any], payload: Dict[str, Any]) -> Optional[Path]:
    if _truthy_env("LLM_API_CACHE_WRITE_DISABLE"):
        return None
    if not cache_enabled_for_task(task):
        return None

    try:
        task_id = make_task_id(task, messages, options)
    except Exception:
        return None
    path = cache_path(task_id)
    out: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": _now_iso_utc(),
        "task": task,
        "task_id": task_id,
        **payload,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception:
        return None
