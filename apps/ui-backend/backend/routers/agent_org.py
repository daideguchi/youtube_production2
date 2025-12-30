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


def _locked_update_json(path: Path, update_fn) -> dict:
    """
    Best-effort locked read-modify-write to avoid clobbering concurrent updates.
    Falls back to atomic replace when flock isn't available.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl  # unix-only
    except Exception:
        cur = _read_json(path)
        nxt = update_fn(cur if isinstance(cur, dict) else {})
        if not isinstance(nxt, dict):
            nxt = {}
        _atomic_write_json(path, nxt)
        return nxt

    with path.open("a+", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            cur = _read_json(path)
            nxt = update_fn(cur if isinstance(cur, dict) else {})
            if not isinstance(nxt, dict):
                nxt = {}
            _atomic_write_json(path, nxt)
            return nxt

        f.seek(0)
        raw = f.read()
        try:
            cur = json.loads(raw) if raw.strip() else {}
        except Exception:
            cur = {}
        if not isinstance(cur, dict):
            cur = {}

        nxt = update_fn(cur)
        if not isinstance(nxt, dict):
            nxt = {}

        f.seek(0)
        f.truncate()
        f.write(json.dumps(nxt, ensure_ascii=False, indent=2) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
        return nxt


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


_SCOPE_GLOB_CHARS = {"*", "?", "[", "]"}


def _scope_has_glob(scope: str) -> bool:
    s = (scope or "").strip()
    if not s:
        return False
    if "**" in s:
        return True
    return any(ch in s for ch in _SCOPE_GLOB_CHARS)


def _scope_static_prefix(scope: str) -> str:
    """
    Best-effort non-glob prefix for overlap detection.
    Examples:
      - "apps/ui/**" -> "apps/ui/"
      - "packages/**/tests/*" -> "packages/"
    """
    s = (scope or "").strip()
    if not s:
        return ""
    for i, ch in enumerate(s):
        if ch in _SCOPE_GLOB_CHARS:
            return s[:i]
    if "**" in s:
        return s.split("**", 1)[0]
    return s


def _is_same_or_parent(parent: str, child: str) -> bool:
    p = (parent or "").strip().rstrip("/")
    c = (child or "").strip().rstrip("/")
    if not p or not c:
        return False
    if p == c:
        return True
    return c.startswith(p + "/")


def _scopes_may_intersect(scope_a: str, scope_b: str) -> bool:
    """
    Conservative (safe) overlap check between two scopes (paths or globs).

    We intentionally bias toward "might overlap" to prevent two agents from
    taking conflicting locks. If overlap is truly needed, allow it via "force".
    """
    a = (scope_a or "").strip().replace("\\", "/").strip("/")
    b = (scope_b or "").strip().replace("\\", "/").strip("/")
    if not a or not b:
        return False

    a_glob = _scope_has_glob(a)
    b_glob = _scope_has_glob(b)

    if not a_glob and not b_glob:
        return _is_same_or_parent(a, b) or _is_same_or_parent(b, a)

    a_prefix = _scope_static_prefix(a).replace("\\", "/").strip("/")
    b_prefix = _scope_static_prefix(b).replace("\\", "/").strip("/")
    if a_prefix and b_prefix and (_is_same_or_parent(a_prefix, b_prefix) or _is_same_or_parent(b_prefix, a_prefix)):
        return True

    a_probe = a_prefix if a_glob and a_prefix else a
    b_probe = b_prefix if b_glob and b_prefix else b
    try:
        if fnmatch.fnmatchcase(a_probe, b) or fnmatch.fnmatchcase(b_probe, a):
            return True
    except Exception:
        pass

    return False


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


def _normalize_scope(scope: str) -> str:
    raw = (scope or "").strip()
    if not raw:
        return raw

    has_wildcard = any(ch in raw for ch in "*?[]")
    if has_wildcard:
        root = str(REPO_ROOT)
        if raw.startswith(root):
            rel = raw[len(root) :].lstrip("/\\")
            return rel.replace(os.sep, "/")
        return raw.replace(os.sep, "/")

    p = Path(raw).expanduser()
    if p.is_absolute():
        try:
            rel = p.absolute().relative_to(REPO_ROOT)
            return str(rel).replace(os.sep, "/")
        except Exception:
            return str(p.absolute()).replace(os.sep, "/")
    return str(p).replace(os.sep, "/")


def _board_path() -> Path:
    return _coord_dir() / "board.json"


def _board_default_payload(now_iso: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "agent_board",
        "updated_at": now_iso,
        "agents": {},
        "areas": {},
        "log": [],
    }


def _board_ensure_shape(obj: dict, now_iso: str) -> Dict[str, Any]:
    if not isinstance(obj, dict):
        obj = {}
    base = _board_default_payload(now_iso)
    obj.setdefault("schema_version", base["schema_version"])
    obj.setdefault("kind", base["kind"])
    obj.setdefault("updated_at", base["updated_at"])
    obj.setdefault("agents", {})
    obj.setdefault("areas", {})
    obj.setdefault("log", [])
    if not isinstance(obj.get("agents"), dict):
        obj["agents"] = {}
    if not isinstance(obj.get("areas"), dict):
        obj["areas"] = {}
    if not isinstance(obj.get("log"), list):
        obj["log"] = []
    return obj  # type: ignore[return-value]


def _parse_csv_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    out: List[str] = []
    for part in str(raw).split(","):
        s = part.strip()
        if s:
            out.append(s)
    # de-dup preserve order
    seen = set()
    uniq: List[str] = []
    for s in out:
        if s in seen:
            continue
        uniq.append(s)
        seen.add(s)
    return uniq


def _ensure_board_agent_entry(actor: str) -> None:
    p = _board_path()
    now_iso = datetime.now(timezone.utc).isoformat()

    def _update(cur: dict) -> dict:
        cur = _board_ensure_shape(cur if isinstance(cur, dict) else {}, now_iso)
        agents = cur.get("agents") if isinstance(cur.get("agents"), dict) else {}
        if not isinstance(agents, dict):
            agents = {}

        st = agents.get(actor)
        if not isinstance(st, dict):
            st = {"doing": "-", "blocked": "-", "next": "-", "note": "(auto)"}
            st["updated_at"] = now_iso
            agents[actor] = st
            cur["agents"] = agents
            cur["updated_at"] = now_iso
            return cur

        st.setdefault("doing", "-")
        st.setdefault("blocked", "-")
        st.setdefault("next", "-")
        st.setdefault("note", "-")
        st.setdefault("updated_at", now_iso)
        agents[actor] = st
        cur["agents"] = agents
        cur["updated_at"] = now_iso
        return cur

    _locked_update_json(p, _update)


def _board_append_note(*, actor: str, topic: str, message: str, tags: List[str]) -> str:
    p = _board_path()
    now_iso = datetime.now(timezone.utc).isoformat()
    note_id = _new_id("note")
    entry: Dict[str, Any] = {
        "id": note_id,
        "thread_id": note_id,
        "ts": now_iso,
        "agent": actor,
        "topic": topic,
        "message": message,
        "tags": tags,
    }

    def _update(cur: dict) -> dict:
        cur = _board_ensure_shape(cur if isinstance(cur, dict) else {}, now_iso)
        agents = cur.get("agents") if isinstance(cur.get("agents"), dict) else {}
        if not isinstance(agents, dict):
            agents = {}
        st = agents.get(actor)
        if not isinstance(st, dict):
            st = {}
        st["last_note_at"] = now_iso
        st.setdefault("updated_at", now_iso)
        agents[actor] = st
        cur["agents"] = agents

        log = cur.get("log") if isinstance(cur.get("log"), list) else []
        if not isinstance(log, list):
            log = []
        log.append(entry)
        max_log = 1000
        if len(log) > max_log:
            log = log[-max_log:]
        cur["log"] = log
        cur["updated_at"] = now_iso
        return cur

    _locked_update_json(p, _update)
    return note_id


def _normalize_lock_id(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return s
    s = s.replace("\\", "/")
    if "/" in s:
        s = s.rsplit("/", 1)[-1]
    if s.endswith(".json"):
        s = s[: -len(".json")]
    return s.strip()


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


class LockCreateRequest(BaseModel):
    from_agent: str = Field("ui", alias="from", description="actor name")
    scopes: List[str] = Field(..., description="scopes (repo-relative globs/paths or absolute paths)")
    mode: str = Field("no_touch", description="lock mode")
    ttl_min: Optional[int] = Field(None, ge=1, le=60 * 24 * 7, description="optional TTL minutes")
    note: Optional[str] = Field(default=None)
    force: bool = Field(default=False, description="allow overlapping existing locks")
    announce: bool = Field(default=True, description="post a shared-board note about this lock")
    announce_tags: Optional[str] = Field(default=None, description="comma-separated tags (default: lock,coordination)")


class LockUnlockRequest(BaseModel):
    from_agent: str = Field("ui", alias="from", description="actor name")
    lock_id: str = Field(..., description="lock id (e.g., lock__... )")


class BoardStatusUpdateRequest(BaseModel):
    from_agent: str = Field("ui", alias="from", description="actor name")
    doing: Optional[str] = Field(default=None)
    blocked: Optional[str] = Field(default=None)
    next: Optional[str] = Field(default=None)
    note: Optional[str] = Field(default=None)
    tags: Optional[str] = Field(default=None, description="comma-separated tags")
    clear: bool = Field(default=False)


class BoardNoteCreateRequest(BaseModel):
    from_agent: str = Field("ui", alias="from", description="actor name")
    topic: str = Field(..., description="thread/topic title (BEP-1 recommended)")
    message: str = Field(..., description="body")
    reply_to: Optional[str] = Field(default=None, description="reply to existing note_id")
    tags: Optional[str] = Field(default=None, description="comma-separated tags")


class BoardAreaSetRequest(BaseModel):
    from_agent: str = Field("ui", alias="from", description="actor name")
    area: str = Field(..., description="area key (e.g., script/audio/ui)")
    owner: Optional[str] = Field(default=None)
    reviewers: Optional[str] = Field(default=None, description="comma-separated reviewers")
    note: Optional[str] = Field(default=None)
    clear: bool = Field(default=False)


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


@router.get("/overview")
def get_overview(
    stale_sec: int = Query(30, ge=1, le=3600),
    limit_memos: int = Query(3, ge=0, le=50),
    include_expired_locks: bool = Query(False),
) -> Dict[str, Any]:
    """
    Aggregated "who is doing what" view for UI:
    - agents (heartbeat + pid alive)
    - locks (grouped by created_by)
    - memos (grouped by from; limited per actor)
    - assignments (attached by agent_id)
    """
    q = _queue_dir()
    coord = _coord_dir()
    now = datetime.now(timezone.utc)

    # Agents (raw records)
    agents_dir = coord / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent_records: List[Dict[str, Any]] = []
    for fp in sorted(agents_dir.glob("*.json")):
        obj = _read_json(fp)
        if not obj:
            continue
        obj["_path"] = str(fp)
        agent_records.append(obj)

    agents_by_name: Dict[str, List[Dict[str, Any]]] = {}
    agents_by_id: Dict[str, Dict[str, Any]] = {}
    for a in agent_records:
        name = str(a.get("name") or "").strip()
        if name:
            agents_by_name.setdefault(name, []).append(a)
        aid = str(a.get("id") or "").strip()
        if aid:
            agents_by_id[aid] = a

    def _status_for_agent(a: Dict[str, Any]) -> str:
        last = _parse_iso(a.get("last_seen_at"))
        age = None
        if last:
            try:
                age = int((now - last).total_seconds())
            except Exception:
                age = None

        pid = None
        try:
            pid = int(a.get("pid")) if a.get("pid") is not None else None
        except Exception:
            pid = None
        pid_alive = _pid_is_alive(pid)

        status = "active"
        if age is not None and age > int(stale_sec):
            status = "stale"
        if pid and not pid_alive:
            status = "dead"
        return status

    # Locks (grouped by created_by)
    locks_dir = coord / "locks"
    locks_dir.mkdir(parents=True, exist_ok=True)
    locks_by_actor: Dict[str, List[Dict[str, Any]]] = {}
    lock_count = 0

    for fp in sorted(locks_dir.glob("*.json")):
        obj = _read_json(fp)
        if not obj:
            continue
        exp_dt = _parse_iso(obj.get("expires_at"))
        expired = bool(exp_dt and exp_dt <= now)
        if expired and not include_expired_locks:
            continue
        status = "expired" if expired else "active"

        actor = str(obj.get("created_by") or "unknown").strip() or "unknown"
        locks_by_actor.setdefault(actor, []).append(
            {
                "status": status,
                "id": obj.get("id") or fp.stem,
                "mode": obj.get("mode"),
                "created_by": obj.get("created_by"),
                "created_at": obj.get("created_at"),
                "expires_at": obj.get("expires_at"),
                "scopes": obj.get("scopes") or [],
                "note": obj.get("note"),
                "_path": str(fp),
            }
        )
        lock_count += 1

    # Memos (grouped by from; limited per actor)
    memos_dir = coord / "memos"
    memos_dir.mkdir(parents=True, exist_ok=True)
    memos_by_actor: Dict[str, List[Dict[str, Any]]] = {}
    memos_scanned = 0
    scan_limit = max(200, int(limit_memos) * 50) if limit_memos > 0 else 0

    for fp in sorted(memos_dir.glob("*.json"), reverse=True):
        if scan_limit and memos_scanned >= scan_limit:
            break
        obj = _read_json(fp)
        if not obj:
            continue
        memos_scanned += 1
        actor = str(obj.get("from") or "unknown").strip() or "unknown"
        bucket = memos_by_actor.setdefault(actor, [])
        if limit_memos > 0 and len(bucket) < int(limit_memos):
            bucket.append(
                {
                    "id": obj.get("id") or fp.stem,
                    "created_at": obj.get("created_at"),
                    "from": obj.get("from"),
                    "to": obj.get("to"),
                    "subject": obj.get("subject"),
                    "related_task_id": obj.get("related_task_id"),
                    "_path": str(fp),
                }
            )

    # Assignments (attach by agent_id when possible)
    assigns_dir = coord / "assignments"
    assigns_dir.mkdir(parents=True, exist_ok=True)
    assigns_by_agent_id: Dict[str, List[Dict[str, Any]]] = {}
    assignment_count = 0
    for fp in sorted(assigns_dir.glob("*.json"), reverse=True):
        obj = _read_json(fp)
        if not obj:
            continue
        assignment_count += 1
        agent_id = str(obj.get("agent_id") or "").strip()
        if not agent_id:
            continue
        assigns_by_agent_id.setdefault(agent_id, []).append(obj)

    actors: set[str] = set()
    actors.update(agents_by_name.keys())
    actors.update(locks_by_actor.keys())
    actors.update(memos_by_actor.keys())

    rows: List[Dict[str, Any]] = []
    for actor in sorted(actors):
        recs = agents_by_name.get(actor, [])
        best = None
        if recs:
            best = max(recs, key=lambda r: str(r.get("last_seen_at") or ""))
        status = _status_for_agent(best) if best else "unregistered"
        locks = locks_by_actor.get(actor, [])
        memos = memos_by_actor.get(actor, [])
        assignments: List[Dict[str, Any]] = []
        if best:
            aid = str(best.get("id") or "").strip()
            if aid and aid in assigns_by_agent_id:
                assignments = assigns_by_agent_id.get(aid, [])

        rows.append(
            {
                "actor": actor,
                "status": status,
                "agent_records": recs,
                "locks": sorted(locks, key=lambda r: str(r.get("created_at") or "")),
                "recent_memos": memos,
                "assignments": assignments,
            }
        )

    rows.sort(key=lambda r: (-len(r.get("locks") or []), str(r.get("status") or ""), str(r.get("actor") or "")))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "queue_dir": str(q),
        "counts": {
            "actors": len(rows),
            "agents": len(agent_records),
            "locks": lock_count,
            "memos_scanned": memos_scanned,
            "assignments": assignment_count,
        },
        "actors": rows,
    }


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


@router.post("/locks")
def create_lock(req: LockCreateRequest) -> Dict[str, Any]:
    q = _queue_dir()
    coord = _coord_dir()
    d = coord / "locks"
    d.mkdir(parents=True, exist_ok=True)

    actor = (req.from_agent or "").strip() or "ui"
    scopes = [_normalize_scope(s) for s in (req.scopes or []) if str(s).strip()]
    if not scopes:
        raise HTTPException(status_code=400, detail="scopes is required")

    now = datetime.now(timezone.utc)

    if not req.force:
        conflicts: List[Dict[str, Any]] = []
        for fp in sorted(d.glob("*.json")):
            obj = _read_json(fp)
            if not obj:
                continue
            exp_dt = _parse_iso(obj.get("expires_at"))
            if exp_dt and exp_dt <= now:
                continue

            created_by = str(obj.get("created_by") or "").strip()
            if created_by and created_by == actor:
                continue

            other_scopes = obj.get("scopes") or []
            if not isinstance(other_scopes, list):
                other_scopes = [str(other_scopes)]

            hit = False
            for new_sc in scopes:
                for old_sc in other_scopes:
                    if _scopes_may_intersect(new_sc, str(old_sc)):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                conflicts.append(
                    {
                        "id": obj.get("id") or fp.stem,
                        "created_by": obj.get("created_by"),
                        "mode": obj.get("mode"),
                        "created_at": obj.get("created_at"),
                        "expires_at": obj.get("expires_at"),
                        "scopes": other_scopes,
                        "note": obj.get("note"),
                    }
                )

        if conflicts:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "lock_scope_conflict",
                    "message": "lock scope intersects existing active locks",
                    "conflicts": conflicts[:20],
                },
            )

    lock_id = _new_id("lock")
    expires_at = None
    if req.ttl_min is not None and req.ttl_min > 0:
        expires_at = (now.replace(microsecond=0) + timedelta(minutes=int(req.ttl_min))).isoformat()

    payload: Dict[str, Any] = {
        "schema_version": 1,
        "kind": "lock",
        "id": lock_id,
        "created_at": now.isoformat(),
        "created_by": actor,
        "mode": str(req.mode or "no_touch"),
        "scopes": scopes,
    }
    if req.note:
        payload["note"] = str(req.note)
    if expires_at:
        payload["expires_at"] = expires_at

    out = d / f"{lock_id}.json"
    _atomic_write_json(out, payload)

    try:
        _ensure_board_agent_entry(actor)
    except Exception:
        pass

    note_id = None
    if req.announce:
        try:
            announce_tags = _parse_csv_list(req.announce_tags) or ["lock", "coordination"]
            msg_lines = [
                "scope:",
                *[f"- {s}" for s in scopes],
                "locks:",
                f"- {lock_id}",
                "mode:",
                f"- {payload.get('mode')}",
                "ttl_min:",
                f"- {req.ttl_min if req.ttl_min is not None else '-'}",
                "expires_at:",
                f"- {expires_at or '-'}",
                "note:",
                f"- {str(req.note) if req.note else '-'}",
            ]
            note_id = _board_append_note(
                actor=actor,
                topic=f"[FYI][lock] {actor}",
                message="\n".join(msg_lines) + "\n",
                tags=announce_tags,
            )
        except Exception:
            note_id = None

    _append_event(
        {
            "schema_version": 1,
            "kind": "event",
            "created_at": now.isoformat(),
            "actor": actor,
            "action": "lock_created",
            "lock_id": lock_id,
            "lock_path": str(out),
            "mode": payload.get("mode"),
            "scopes": scopes,
            "ttl_min": req.ttl_min,
            "board_note_id": note_id,
        }
    )

    return {
        "ok": True,
        "queue_dir": str(q),
        "lock_id": lock_id,
        "lock_path": str(out),
        "board_note_id": note_id,
        "lock": payload,
    }


@router.post("/locks/unlock")
def unlock_lock(req: LockUnlockRequest) -> Dict[str, Any]:
    q = _queue_dir()
    d = _coord_dir() / "locks"
    d.mkdir(parents=True, exist_ok=True)

    actor = (req.from_agent or "").strip() or "ui"
    lock_id = _normalize_lock_id(req.lock_id)
    if not lock_id:
        raise HTTPException(status_code=400, detail="lock_id is required")

    lock_path = d / f"{lock_id}.json"
    existed = lock_path.exists()
    if existed:
        lock_path.unlink()

    now_iso = datetime.now(timezone.utc).isoformat()
    _append_event(
        {
            "schema_version": 1,
            "kind": "event",
            "created_at": now_iso,
            "actor": actor,
            "action": "lock_removed" if existed else "lock_remove_missing",
            "lock_id": lock_id,
            "lock_path": str(lock_path),
        }
    )

    return {
        "ok": True,
        "queue_dir": str(q),
        "unlocked": existed,
        "lock_id": lock_id,
        "lock_path": str(lock_path),
    }


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
