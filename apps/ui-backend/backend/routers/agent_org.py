from __future__ import annotations

import fnmatch
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from factory_common.paths import logs_root, repo_root as ssot_repo_root

router = APIRouter(prefix="/api/agent-org", tags=["agent_org"])

REPO_ROOT = ssot_repo_root()
DEFAULT_QUEUE_DIR = logs_root() / "agent_tasks"


def _queue_dir() -> Path:
    raw = (os.getenv("LLM_AGENT_QUEUE_DIR") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p if p.is_absolute() else (REPO_ROOT / p)
    return DEFAULT_QUEUE_DIR


def _coord_dir() -> Path:
    return _queue_dir() / "coordination"


def _read_json(path: Path) -> dict:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(str(dt_str))
    except Exception:
        return None


def _pid_is_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False
    return True


def _try_lock_available(lock_path: Path) -> bool:
    try:
        import fcntl  # unix-only
    except Exception:
        return True

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        except Exception:
            return True
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
    return True


def _scope_matches_path(scope: str, rel_path: str) -> bool:
    scope = (scope or "").strip()
    if not scope:
        return False
    if any(ch in scope for ch in "*?[]"):
        return fnmatch.fnmatch(rel_path, scope)
    scope_norm = scope.rstrip("/")
    if rel_path == scope_norm:
        return True
    return rel_path.startswith(scope_norm + "/")


def _append_event(payload: dict) -> None:
    p = _coord_dir() / "events.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


def _new_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}__{stamp}__{secrets.token_hex(4)}"


def _repo_relative(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = REPO_ROOT / p
    # IMPORTANT: keep symlink-relative path (do not resolve())
    try:
        return str(p.absolute().relative_to(REPO_ROOT)).replace(os.sep, "/")
    except Exception:
        return str(path).replace(os.sep, "/")


class AgentNoteCreateRequest(BaseModel):
    to: str = Field(..., description="recipient agent name")
    subject: str = Field(..., description="note subject")
    body: str = Field(..., description="note body")
    from_agent: str = Field("ui", alias="from", description="sender name")
    ttl_min: Optional[int] = Field(None, ge=1, le=60 * 24 * 7, description="optional TTL minutes")


class OrchestratorRequestBody(BaseModel):
    action: str = Field(..., description="orchestrator action")
    payload: Dict[str, Any] = Field(default_factory=dict)
    from_agent: str = Field("ui", alias="from")
    wait_sec: float = Field(0.0, ge=0.0, le=30.0)


@router.get("/orchestrator")
def get_orchestrator_status() -> Dict[str, Any]:
    q = _queue_dir()
    orch_dir = _coord_dir() / "orchestrator"
    state_path = orch_dir / "state.json"
    lock_path = orch_dir / "lease.lock"
    state = _read_json(state_path) if state_path.exists() else {}

    lock_held = not _try_lock_available(lock_path)
    pid = None
    try:
        pid = int(state.get("pid")) if state.get("pid") is not None else None
    except Exception:
        pid = None

    pid_alive = _pid_is_alive(pid)
    now = datetime.now(timezone.utc)
    last = _parse_iso(state.get("last_heartbeat_at") if isinstance(state, dict) else None)
    heartbeat_age_sec = None
    if last:
        try:
            heartbeat_age_sec = int((now - last).total_seconds())
        except Exception:
            heartbeat_age_sec = None

    return {
        "queue_dir": str(q),
        "lock_held": lock_held,
        "pid_alive": pid_alive,
        "heartbeat_age_sec": heartbeat_age_sec,
        "state": state,
    }


@router.get("/agents")
def list_agents(stale_sec: int = Query(30, ge=1, le=3600)) -> Dict[str, Any]:
    q = _queue_dir()
    d = _coord_dir() / "agents"
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    agents: List[Dict[str, Any]] = []

    for fp in sorted(d.glob("*.json")):
        obj = _read_json(fp)
        if not obj:
            continue
        last_dt = _parse_iso(obj.get("last_seen_at"))
        age = None
        if last_dt:
            try:
                age = int((now - last_dt).total_seconds())
            except Exception:
                age = None
        pid = None
        try:
            pid = int(obj.get("pid")) if obj.get("pid") is not None else None
        except Exception:
            pid = None
        pid_alive = _pid_is_alive(pid)

        status = "active"
        if age is not None and age > int(stale_sec):
            status = "stale"
        if pid and not pid_alive:
            status = "dead"

        agents.append(
            {
                "status": status,
                "id": obj.get("id") or fp.stem,
                "name": obj.get("name") or "-",
                "role": obj.get("assigned_role") or obj.get("role") or "-",
                "pid": pid,
                "host_pid": obj.get("host_pid"),
                "last_seen_at": obj.get("last_seen_at"),
                "raw": obj,
            }
        )

    agents.sort(key=lambda a: str(a.get("last_seen_at") or ""), reverse=True)
    return {"count": len(agents), "agents": agents, "queue_dir": str(q)}


@router.get("/memos")
def list_memos(
    limit: int = Query(200, ge=1, le=2000),
    to: Optional[str] = Query(default=None),
    from_: Optional[str] = Query(default=None, alias="from"),
) -> Dict[str, Any]:
    q = _queue_dir()
    d = _coord_dir() / "memos"
    d.mkdir(parents=True, exist_ok=True)
    want_to = (to or "").strip()
    want_from = (from_ or "").strip()

    rows: List[Dict[str, Any]] = []
    for fp in sorted(d.glob("*.json"), reverse=True):
        obj = _read_json(fp)
        if not obj:
            continue
        from_val = str(obj.get("from") or "")
        to_val = obj.get("to") or []
        if not isinstance(to_val, list):
            to_val = [str(to_val)]

        if want_from and from_val != want_from:
            continue
        if want_to and (want_to not in to_val and "*" not in to_val):
            continue

        rows.append(
            {
                "id": obj.get("id") or fp.stem,
                "created_at": obj.get("created_at"),
                "from": obj.get("from"),
                "to": to_val,
                "subject": obj.get("subject"),
                "related_task_id": obj.get("related_task_id"),
            }
        )
        if len(rows) >= limit:
            break

    return {"count": len(rows), "memos": rows, "queue_dir": str(q)}


@router.get("/memos/{memo_id}")
def get_memo(memo_id: str) -> Dict[str, Any]:
    q = _queue_dir()
    p = _coord_dir() / "memos" / f"{memo_id}.json"
    if not p.exists():
        raise HTTPException(status_code=404, detail="memo not found")
    return _read_json(p)


@router.get("/notes")
def list_notes(
    limit: int = Query(200, ge=1, le=2000),
    all: bool = Query(default=False),
    to: Optional[str] = Query(default=None),
    from_: Optional[str] = Query(default=None, alias="from"),
) -> Dict[str, Any]:
    q = _queue_dir()
    inbox = _coord_dir() / "agent_notes" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    want_to = (to or "").strip()
    want_from = (from_ or "").strip()

    now = datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []
    for fp in sorted(inbox.glob("*.json"), reverse=True):
        obj = _read_json(fp)
        if not obj:
            continue

        to_val = str(obj.get("to") or "")
        from_val = str(obj.get("from") or "")
        if want_to and to_val != want_to:
            continue
        if want_from and from_val != want_from:
            continue

        exp_dt = _parse_iso(obj.get("expires_at"))
        expired = bool(exp_dt and exp_dt <= now)
        if expired and not all:
            continue

        rows.append(
            {
                "status": "expired" if expired else "active",
                "id": obj.get("id") or fp.stem,
                "created_at": obj.get("created_at"),
                "from": obj.get("from"),
                "to": obj.get("to"),
                "subject": obj.get("subject"),
            }
        )
        if len(rows) >= limit:
            break

    return {"count": len(rows), "notes": rows, "queue_dir": str(q)}


@router.post("/notes")
def create_note(payload: AgentNoteCreateRequest) -> Dict[str, Any]:
    q = _queue_dir()
    inbox = _coord_dir() / "agent_notes" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    note_id = _new_id("note")

    expires_at = None
    if payload.ttl_min is not None and payload.ttl_min > 0:
        expires_at = (
            datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=int(payload.ttl_min))
        ).isoformat()

    body = {
        "schema_version": 1,
        "kind": "agent_note",
        "id": note_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "to": payload.to.strip(),
        "from": payload.from_agent.strip() or "ui",
        "subject": payload.subject.strip(),
        "body": payload.body,
    }
    if expires_at:
        body["expires_at"] = expires_at

    out = inbox / f"{note_id}.json"
    _atomic_write_json(out, body)

    _append_event(
        {
            "schema_version": 1,
            "kind": "event",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "actor": payload.from_agent.strip() or "ui",
            "action": "agent_note_sent",
            "to": payload.to.strip(),
            "subject": payload.subject.strip(),
            "path": str(out),
        }
    )

    return {"ok": True, "queue_dir": str(q), "note_id": note_id, "path": str(out)}


@router.get("/notes/{note_id}")
def get_note(note_id: str) -> Dict[str, Any]:
    q = _queue_dir()
    inbox = _coord_dir() / "agent_notes" / "inbox" / f"{note_id}.json"
    archived = _coord_dir() / "agent_notes" / "archived" / f"{note_id}.json"
    if inbox.exists():
        return _read_json(inbox)
    if archived.exists():
        return _read_json(archived)
    raise HTTPException(status_code=404, detail="note not found")


@router.post("/orchestrator/request")
def orchestrator_request(body: OrchestratorRequestBody) -> Dict[str, Any]:
    q = _queue_dir()
    orch_dir = _coord_dir() / "orchestrator"
    inbox = orch_dir / "inbox"
    outbox = orch_dir / "outbox"
    processed = orch_dir / "processed"
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)

    req_id = _new_id("req")
    req_path = inbox / f"{req_id}.json"
    req = {
        "schema_version": 1,
        "kind": "orchestrator_request",
        "id": req_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "from": body.from_agent.strip() or "ui",
        "action": body.action,
        "payload": body.payload or {},
    }
    _atomic_write_json(req_path, req)

    _append_event(
        {
            "schema_version": 1,
            "kind": "event",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "actor": body.from_agent.strip() or "ui",
            "action": "orchestrator_request_created",
            "request_id": req_id,
            "request_action": body.action,
            "path": str(req_path),
        }
    )

    if not body.wait_sec:
        return {"ok": True, "queue_dir": str(q), "request_id": req_id, "request_path": str(req_path)}

    deadline = time.time() + float(body.wait_sec)
    resp_path = outbox / f"resp__{req_id}.json"
    while time.time() < deadline:
        if resp_path.exists():
            resp = _read_json(resp_path)
            return {
                "ok": True,
                "queue_dir": str(q),
                "request_id": req_id,
                "request_path": str(req_path),
                "response_path": str(resp_path),
                "response": resp,
            }
        time.sleep(0.2)
    raise HTTPException(status_code=504, detail=f"timeout waiting for orchestrator response: {resp_path}")

@router.get("/locks")
def list_locks(
    all: bool = Query(default=False),
    path: Optional[str] = Query(default=None),
) -> Dict[str, Any]:
    q = _queue_dir()
    d = _coord_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True)

    rel_path = _repo_relative(path) if path else None
    now = datetime.now(timezone.utc)
    rows: List[Dict[str, Any]] = []

    for fp in sorted(d.glob("*.json")):
        obj = _read_json(fp)
        if not obj:
            continue

        exp_dt = _parse_iso(obj.get("expires_at"))
        expired = bool(exp_dt and exp_dt <= now)
        if expired and not all:
            continue

        scopes = obj.get("scopes") or []
        if not isinstance(scopes, list):
            scopes = [str(scopes)]

        if rel_path:
            if not any(_scope_matches_path(str(s), rel_path) for s in scopes):
                continue

        rows.append(
            {
                "status": "expired" if expired else "active",
                "id": obj.get("id") or fp.stem,
                "mode": obj.get("mode"),
                "created_by": obj.get("created_by"),
                "created_at": obj.get("created_at"),
                "expires_at": obj.get("expires_at"),
                "scopes": scopes,
                "note": obj.get("note"),
            }
        )

    return {"count": len(rows), "locks": rows, "queue_dir": str(q)}


@router.get("/assignments")
def list_assignments(limit: int = Query(200, ge=1, le=2000)) -> Dict[str, Any]:
    q = _queue_dir()
    d = _coord_dir() / "assignments"
    d.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for fp in sorted(d.glob("*.json"), reverse=True):
        obj = _read_json(fp)
        if not obj:
            continue
        rows.append(obj)
        if len(rows) >= limit:
            break
    return {"count": len(rows), "assignments": rows, "queue_dir": str(q)}


@router.get("/events")
def tail_events(limit: int = Query(200, ge=1, le=2000)) -> Dict[str, Any]:
    q = _queue_dir()
    p = _coord_dir() / "events.jsonl"
    if not p.exists():
        return {"count": 0, "events": [], "queue_dir": str(q)}
    lines = p.read_text(encoding="utf-8").splitlines()
    out: List[Dict[str, Any]] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return {"count": len(out), "events": out, "queue_dir": str(q)}
