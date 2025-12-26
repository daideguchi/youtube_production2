"""
Agent-mode support for replacing API LLM calls with a queue.

Goal:
- Replace API LLM calls with an "enqueue → complete → rerun" workflow.
- Keep the switch opt-in via env vars (default remains API).

Key env vars (SSOT: ssot/ops/OPS_ENV_VARS.md):
- LLM_MODE=api|agent|think  (think is an alias of agent, with safe defaults)
- LLM_AGENT_QUEUE_DIR=/abs/or/relative/path (default: workspaces/logs/agent_tasks)
- LLM_AGENT_NAME=Mike (optional; used for claimed_by/completed_by metadata)
- LLM_AGENT_TASKS=comma,separated,task,names (optional; exact allowlist)
- LLM_AGENT_TASK_PREFIXES=script_,tts_ (optional; prefix allowlist)
- LLM_AGENT_EXCLUDE_TASKS=image_generation (optional; exact blocklist)
- LLM_AGENT_EXCLUDE_PREFIXES=visual_ (optional; prefix blocklist)
- LLM_AGENT_RUNBOOKS_CONFIG=path/to/config (default: configs/agent_runbooks.yaml)
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from factory_common import paths as repo_paths

SCHEMA_VERSION = 1
PROJECT_ROOT = repo_paths.repo_root()


def _default_queue_dir() -> Path:
    return repo_paths.logs_root() / "agent_tasks"


def _default_runbooks_config() -> Path:
    return PROJECT_ROOT / "configs" / "agent_runbooks.yaml"


DEFAULT_QUEUE_DIR = _default_queue_dir()
DEFAULT_RUNBOOKS_CONFIG = _default_runbooks_config()

# Options that should NOT affect the cache key (non-semantic / transport-only)
_HASH_EXCLUDE_OPTION_KEYS = {
    "timeout",
    "request_timeout",
    "http_timeout",
    "retries",
    "retry_policy",
}


def _now_iso_utc() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _to_project_relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except Exception:
        return str(path.resolve())


def _llm_mode() -> str:
    return (os.getenv("LLM_MODE") or "api").strip().lower()


def _agent_name() -> Optional[str]:
    raw = (os.getenv("LLM_AGENT_NAME") or os.getenv("AGENT_NAME") or "").strip()
    return raw or None


def agent_mode_enabled_for_task(task: str) -> bool:
    mode = _llm_mode()
    # Alias: "think" means "agent" (named mode)
    if mode not in {"agent", "think"}:
        return False

    tasks_csv = os.getenv("LLM_AGENT_TASKS")
    prefixes_csv = os.getenv("LLM_AGENT_TASK_PREFIXES")
    exclude_csv = os.getenv("LLM_AGENT_EXCLUDE_TASKS")
    exclude_prefixes_csv = os.getenv("LLM_AGENT_EXCLUDE_PREFIXES")

    # THINK MODE default behavior (safe defaults):
    # - If no filters are specified, intercept only text tasks and avoid image generation tasks.
    if mode == "think" and not (tasks_csv or prefixes_csv or exclude_csv or exclude_prefixes_csv):
        # Keep this list broad enough so THINK MODE never accidentally hits API for text-only tasks.
        # (Operator can always narrow via env vars / think.sh flags.)
        prefixes_csv = "script_,tts_,visual_,title_,belt_"
        exclude_csv = "visual_image_gen,image_generation"

    if exclude_csv:
        excluded = {t.strip() for t in exclude_csv.split(",") if t.strip()}
        if task in excluded:
            return False

    if exclude_prefixes_csv:
        excluded_prefixes = [p.strip() for p in exclude_prefixes_csv.split(",") if p.strip()]
        if any(task.startswith(p) for p in excluded_prefixes):
            return False

    if tasks_csv:
        allowed = {t.strip() for t in tasks_csv.split(",") if t.strip()}
        return task in allowed

    if prefixes_csv:
        prefixes = [p.strip() for p in prefixes_csv.split(",") if p.strip()]
        return any(task.startswith(p) for p in prefixes)

    # No allowlist specified → allow all tasks (still opt-in via LLM_MODE)
    return True


def get_queue_dir() -> Path:
    raw = os.getenv("LLM_AGENT_QUEUE_DIR")
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p)
    return DEFAULT_QUEUE_DIR


def pending_path(task_id: str, queue_dir: Optional[Path] = None) -> Path:
    q = queue_dir or get_queue_dir()
    return q / "pending" / f"{task_id}.json"


def results_path(task_id: str, queue_dir: Optional[Path] = None) -> Path:
    q = queue_dir or get_queue_dir()
    return q / "results" / f"{task_id}.json"


def completed_path(task_id: str, queue_dir: Optional[Path] = None) -> Path:
    q = queue_dir or get_queue_dir()
    return q / "completed" / f"{task_id}.json"


def _canonicalize_options(options: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (options or {}).items():
        if k in _HASH_EXCLUDE_OPTION_KEYS:
            continue
        if v in (None, ""):
            continue
        out[k] = v
    return out


def compute_task_id(task: str, messages: List[Dict[str, str]], options: Dict[str, Any]) -> str:
    canonical = {
        "task": task,
        "messages": messages,
        "options": _canonicalize_options(options),
    }
    blob = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:32]
    return f"{task}__{digest}"


def load_runbook_mapping(config_path: Optional[Path] = None) -> Dict[str, Any]:
    raw = os.getenv("LLM_AGENT_RUNBOOKS_CONFIG")
    path = config_path or (Path(raw) if raw else DEFAULT_RUNBOOKS_CONFIG)
    path = path if path.is_absolute() else (PROJECT_ROOT / path)

    if not path.exists():
        return {
            "schema_version": 1,
            "default_runbook": "ssot/agent_runbooks/RUNBOOK_GENERIC_LLM_TASK.md",
            "task_prefix": {},
            "task_exact": {},
        }

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def select_runbook(task: str, mapping: Dict[str, Any]) -> str:
    exact = (mapping.get("task_exact") or {}) if isinstance(mapping, dict) else {}
    if isinstance(exact, dict) and task in exact:
        return str(exact[task])

    prefixes = (mapping.get("task_prefix") or {}) if isinstance(mapping, dict) else {}
    if isinstance(prefixes, dict):
        for prefix, rb in prefixes.items():
            try:
                if task.startswith(str(prefix)):
                    return str(rb)
            except Exception:
                continue

    default = mapping.get("default_runbook") if isinstance(mapping, dict) else None
    return str(default or "ssot/agent_runbooks/RUNBOOK_GENERIC_LLM_TASK.md")


def _infer_caller() -> Dict[str, Any]:
    for fr in inspect.stack()[2:]:
        try:
            p = Path(fr.filename).resolve()
        except Exception:
            continue
        # Skip internal plumbing frames
        if p.name in {"llm_router.py", "llm_client.py", "agent_mode.py"} and "factory_common" in p.parts:
            continue
        return {
            "file": _to_project_relative(p),
            "line": int(fr.lineno),
            "function": str(fr.function),
        }
    return {}


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_result_content(task_id: str, queue_dir: Optional[Path] = None) -> str:
    path = results_path(task_id, queue_dir=queue_dir)
    obj = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or "content" not in obj:
        raise ValueError(f"Invalid result json (missing content): {path}")
    return str(obj["content"])


def ensure_pending_task(
    task_id: str,
    task: str,
    messages: List[Dict[str, str]],
    options: Dict[str, Any],
    response_format: Optional[str],
    queue_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    q = queue_dir or get_queue_dir()
    p_path = pending_path(task_id, queue_dir=q)
    r_path = results_path(task_id, queue_dir=q)

    mapping = load_runbook_mapping()
    runbook = select_runbook(task, mapping)

    if p_path.exists():
        try:
            return json.loads(p_path.read_text(encoding="utf-8"))
        except Exception:
            # If corrupted, overwrite with a fresh payload.
            pass

    agent_name = _agent_name()
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": task_id,
        "status": "pending",
        "created_at": _now_iso_utc(),
        "project_root": str(PROJECT_ROOT),
        "task": task,
        "response_format": response_format,
        "messages": messages,
        "options": _canonicalize_options(options),
        "runbook_path": runbook,
        "result_path": _to_project_relative(r_path),
        "instructions": {
            "what_to_do": "Produce the required output content for this task (follow runbook + messages).",
            "how_to_submit": f"python scripts/agent_runner.py complete {task_id} --content-file /path/to/content.txt",
            "how_to_resume": "Rerun the original pipeline command that created this pending task (see invocation).",
            "notes": "If response_format=json_object, output must be a single JSON object with no extra text.",
        },
        "caller": _infer_caller(),
        "invocation": {
            "cwd": os.getcwd(),
            "python": sys.executable,
            "argv": sys.argv,
        },
    }
    if agent_name:
        payload["claimed_by"] = agent_name
        payload["claimed_at"] = _now_iso_utc()
    _atomic_write_json(p_path, payload)
    return payload


def write_result(
    task_id: str,
    task: str,
    content: str,
    notes: str | None = None,
    completed_by: str | None = None,
    queue_dir: Optional[Path] = None,
    move_pending: bool = True,
) -> Path:
    q = queue_dir or get_queue_dir()
    r_path = results_path(task_id, queue_dir=q)
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "id": task_id,
        "task": task,
        "completed_at": _now_iso_utc(),
        "completed_by": completed_by or _agent_name(),
        "content": content,
    }
    if notes:
        payload["notes"] = str(notes)
    _atomic_write_json(r_path, payload)

    if move_pending:
        p_path = pending_path(task_id, queue_dir=q)
        if p_path.exists():
            c_path = completed_path(task_id, queue_dir=q)
            c_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                p_path.replace(c_path)
            except Exception:
                # Non-fatal; result is already written.
                pass

    return r_path


def maybe_handle_agent_mode(
    *,
    task: str,
    messages: List[Dict[str, str]],
    options: Dict[str, Any],
    response_format: Optional[str],
    return_raw: bool,
) -> Optional[Dict[str, Any]]:
    """
    If agent-mode is enabled for this task:
    - Return cached results when present
    - Otherwise, enqueue a pending task and stop the process (SystemExit)
    """
    if not agent_mode_enabled_for_task(task):
        return None

    mode = _llm_mode()
    q = get_queue_dir()
    task_id = compute_task_id(task, messages, options)
    r_path = results_path(task_id, queue_dir=q)

    if r_path.exists():
        content = read_result_content(task_id, queue_dir=q)
        result_obj = None
        if return_raw:
            try:
                result_obj = json.loads(r_path.read_text(encoding="utf-8"))
            except Exception:
                result_obj = None
        return {
            "content": content,
            "raw": result_obj if return_raw else None,
            "usage": {},
            "request_id": task_id,
            "model": "agent",
            "provider": "agent",
            "chain": ["agent"],
            "latency_ms": 0,
        }

    ensure_pending_task(
        task_id=task_id,
        task=task,
        messages=messages,
        options=options,
        response_format=response_format,
        queue_dir=q,
    )
    p_path = pending_path(task_id, queue_dir=q)
    runbook = select_runbook(task, load_runbook_mapping())

    tag = "THINK_MODE" if mode == "think" else "AGENT_MODE"
    msg_lines = [
        f"[{tag}] LLM call replaced by agent queue.",
        f"- task_id: {task_id}",
        f"- task: {task}",
        f"- pending: {p_path}",
        f"- runbook: {runbook}",
        f"- expected result: {r_path}",
        "- next:",
        "  - python scripts/agent_runner.py show " + task_id,
        "  - follow the runbook, then:",
        "    python scripts/agent_runner.py complete " + task_id + " --content-file /path/to/content.txt",
        "  - rerun: the same command that created this pending task (see pending.invocation as a hint)",
    ]
    raise SystemExit("\n".join(msg_lines))
