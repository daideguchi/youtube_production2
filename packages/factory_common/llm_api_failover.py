from __future__ import annotations

import datetime as _dt
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from factory_common.agent_mode import (
    PROJECT_ROOT,
    compute_task_id,
    ensure_pending_task,
    get_queue_dir,
    load_runbook_mapping,
    pending_path,
    read_result_content,
    results_path,
    select_runbook,
)
from factory_common.paths import logs_root


def _now_iso_utc() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).isoformat()


def _llm_log_path() -> Path:
    raw = os.getenv("LLM_ROUTER_LOG_PATH") or os.getenv("LLM_USAGE_LOG_PATH") or ""
    if raw:
        p = Path(raw)
        return p if p.is_absolute() else (PROJECT_ROOT / p)
    return logs_root() / "llm_usage.jsonl"


def _append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    if os.getenv("LLM_ROUTER_LOG_DISABLE") == "1" or os.getenv("LLM_USAGE_LOG_DISABLE") == "1":
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _coordination_dir(queue_dir: Path) -> Path:
    return queue_dir / "coordination"


def _memos_dir(queue_dir: Path) -> Path:
    return _coordination_dir(queue_dir) / "memos"


def _new_memo_id() -> str:
    stamp = _dt.datetime.now(tz=_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"memo__{stamp}__{secrets.token_hex(4)}"


def _write_failover_memo(queue_dir: Path, *, task_id: str, task: str, body: str) -> None:
    if os.getenv("LLM_FAILOVER_MEMO_DISABLE") == "1":
        return
    agent = (os.getenv("LLM_AGENT_NAME") or os.getenv("AGENT_NAME") or "system").strip() or "system"
    memo_id = _new_memo_id()
    payload: Dict[str, Any] = {
        "schema_version": 1,
        "kind": "memo",
        "id": memo_id,
        "created_at": _now_iso_utc(),
        "from": agent,
        "to": ["*"],
        "subject": "LLM API failover → THINK MODE",
        "body": body,
        "related_task_id": task_id,
        "tags": ["failover", "think_mode", "llm_api"],
    }
    out = _memos_dir(queue_dir) / f"{memo_id}.json"
    _atomic_write_json(out, payload)


def _failover_enabled() -> bool:
    from factory_common.llm_exec_slots import effective_api_failover_to_think

    return effective_api_failover_to_think()


def maybe_failover_to_think(
    *,
    task: str,
    messages: List[Dict[str, str]],
    options: Dict[str, Any],
    response_format: Optional[str],
    return_raw: bool,
    failure: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    When API LLM calls fail, fall back to the agent queue.

    - If results already exist for the computed task_id, return them.
    - Otherwise create pending + stop (SystemExit).
    """
    if not _failover_enabled():
        return None

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
        _append_jsonl(
            _llm_log_path(),
            {
                "status": "api_failover_cache_hit",
                "task": task,
                "task_id": task_id,
                "queue_dir": str(q),
                "timestamp": _now_iso_utc(),
            },
        )
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

    mapping = load_runbook_mapping()
    runbook = select_runbook(task, mapping)
    p_path = pending_path(task_id, queue_dir=q)

    failover_info: Dict[str, Any] = {
        "kind": "llm_api_failover",
        "at": _now_iso_utc(),
        "error": failure.get("error"),
        "error_class": failure.get("error_class"),
        "status_code": failure.get("status_code"),
        "chain": failure.get("chain") or failure.get("tried"),
    }

    pending_obj = ensure_pending_task(
        task_id=task_id,
        task=task,
        messages=messages,
        options=options,
        response_format=response_format,
        queue_dir=q,
    )
    if isinstance(pending_obj, dict) and "failover" not in pending_obj:
        pending_obj["failover"] = failover_info
        _atomic_write_json(p_path, pending_obj)

    _append_jsonl(
        _llm_log_path(),
        {
            "status": "api_failover_to_think",
            "task": task,
            "task_id": task_id,
            "queue_dir": str(q),
            "pending": str(p_path),
            "runbook": runbook,
            "failure": failover_info,
            "timestamp": _now_iso_utc(),
        },
    )

    memo_body = "\n".join(
        [
            "API LLM が失敗したため THINK MODE へフォールバックしました。",
            f"- task: {task}",
            f"- task_id: {task_id}",
            f"- pending: {p_path}",
            f"- runbook: {runbook}",
            f"- error_class: {failover_info.get('error_class')}",
            f"- error: {failover_info.get('error')}",
            f"- status_code: {failover_info.get('status_code')}",
            f"- chain: {failover_info.get('chain')}",
            "",
            "次:",
            f"- python scripts/agent_runner.py show {task_id}",
            f"- python scripts/agent_runner.py complete {task_id} --content-file /path/to/content.txt",
            "- rerun: 元のコマンドを同じ引数で再実行",
        ]
    )
    _write_failover_memo(q, task_id=task_id, task=task, body=memo_body)

    msg_lines = [
        "[LLM_API_FAILOVER] API LLM call failed; switched to THINK MODE queue.",
        f"- task_id: {task_id}",
        f"- task: {task}",
        f"- pending: {p_path}",
        f"- runbook: {runbook}",
        f"- expected result: {r_path}",
        f"- error_class: {failover_info.get('error_class')}",
        f"- status_code: {failover_info.get('status_code')}",
        "- next:",
        "  - python scripts/agent_runner.py show " + task_id,
        "  - follow the runbook, then:",
        "    python scripts/agent_runner.py complete " + task_id + " --content-file /path/to/content.txt",
        "  - rerun: the same command that created this pending task (see pending.invocation as a hint)",
    ]
    raise SystemExit("\n".join(msg_lines))
