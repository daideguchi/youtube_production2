#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


PROJECT_ROOT = _discover_repo_root(Path(__file__).resolve())
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factory_common.agent_mode import get_queue_dir

SCHEMA_VERSION = 1


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(str(dt_str))
    except Exception:
        return None


def _agent_name(args: argparse.Namespace) -> str | None:
    raw = (getattr(args, "agent_name", None) or os.getenv("LLM_AGENT_NAME") or os.getenv("AGENT_NAME") or "").strip()
    if raw:
        return raw
    # Fallback to a stable local identity to avoid created_by="unknown" everywhere.
    fallback = (os.getenv("USER") or os.getenv("LOGNAME") or "").strip()
    if fallback:
        return fallback
    try:
        return os.getlogin()
    except Exception:
        return None


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_json_maybe(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _locked_update_json(path: Path, update_fn) -> dict:
    """
    Best-effort locked read-modify-write to avoid clobbering concurrent updates.
    Falls back to atomic replace when flock isn't available.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import fcntl  # unix-only
    except Exception:
        cur = _load_json_maybe(path)
        nxt = update_fn(cur if isinstance(cur, dict) else {})
        if not isinstance(nxt, dict):
            nxt = {}
        _atomic_write_json(path, nxt)
        return nxt

    with path.open("a+", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        except Exception:
            cur = _load_json_maybe(path)
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


def _append_jsonl(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        return


def _coord_dir(q: Path) -> Path:
    return q / "coordination"


def _events_path(q: Path) -> Path:
    return _coord_dir(q) / "events.jsonl"


def _append_event(q: Path, payload: dict) -> None:
    _append_jsonl(_events_path(q), payload)


def _memos_dir(q: Path) -> Path:
    return _coord_dir(q) / "memos"


def _locks_dir(q: Path) -> Path:
    return _coord_dir(q) / "locks"


def _agents_dir(q: Path) -> Path:
    return _coord_dir(q) / "agents"


def _assignments_dir(q: Path) -> Path:
    return _coord_dir(q) / "assignments"


def _orch_dir(q: Path) -> Path:
    return _coord_dir(q) / "orchestrator"


def _orch_lock_path(q: Path) -> Path:
    return _orch_dir(q) / "lease.lock"


def _orch_state_path(q: Path) -> Path:
    return _orch_dir(q) / "state.json"


def _orch_pidfile_path(q: Path) -> Path:
    return _orch_dir(q) / "pid"


def _orch_inbox_dir(q: Path) -> Path:
    return _orch_dir(q) / "inbox"


def _orch_outbox_dir(q: Path) -> Path:
    return _orch_dir(q) / "outbox"


def _orch_processed_dir(q: Path) -> Path:
    return _orch_dir(q) / "processed"


def _new_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}__{stamp}__{secrets.token_hex(4)}"


def _pid_is_alive(pid: int) -> bool:
    if not pid or pid <= 0:
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


def _ps_ppid_and_cmd(pid: int) -> tuple[int, str] | None:
    try:
        out = subprocess.check_output(["ps", "-p", str(int(pid)), "-o", "ppid=,command="], text=True)
    except Exception:
        return None
    line = (out or "").strip()
    if not line:
        return None
    parts = line.split(None, 1)
    try:
        ppid = int(parts[0])
    except Exception:
        return None
    cmd = parts[1] if len(parts) > 1 else ""
    return (ppid, cmd)


def _detect_codex_host_pid(max_depth: int = 12) -> tuple[int | None, str | None]:
    """
    Best-effort: walk parent processes to find a Codex CLI host PID.
    Returns (pid, command) or (None, None).
    """
    pid = os.getpid()
    for _ in range(max_depth):
        info = _ps_ppid_and_cmd(pid)
        if not info:
            break
        ppid, cmd = info
        cmd_norm = cmd.lower()
        if "codex run resume" in cmd_norm or cmd_norm.endswith("/codex") or " codex " in cmd_norm:
            return (pid, cmd)
        if ppid <= 1:
            break
        pid = ppid
    return (None, None)


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


def _normalize_scope(scope: str) -> str:
    raw = (scope or "").strip()
    if not raw:
        return raw

    has_wildcard = any(ch in raw for ch in "*?[]")
    if has_wildcard:
        root = str(PROJECT_ROOT)
        if raw.startswith(root):
            rel = raw[len(root) :].lstrip("/\\")
            return rel.replace(os.sep, "/")
        return raw.replace(os.sep, "/")

    p = Path(raw).expanduser()
    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(PROJECT_ROOT)
            return str(rel).replace(os.sep, "/")
        except Exception:
            return str(p.resolve()).replace(os.sep, "/")
    return str(p).replace(os.sep, "/")


def _to_project_relative_str(path: Path) -> str:
    try:
        p = path
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        # IMPORTANT: avoid resolve() here (ui/ has symlinks)
        p = p.absolute()
        return str(p.relative_to(PROJECT_ROOT)).replace(os.sep, "/")
    except Exception:
        return str(path).replace(os.sep, "/")


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


def _create_memo(
    q: Path,
    *,
    from_agent: str,
    to_list: list[str] | None,
    subject: str,
    body: str,
    task_id: str | None = None,
    tags_csv: str | None = None,
) -> Path:
    d = _memos_dir(q)
    d.mkdir(parents=True, exist_ok=True)
    memo_id = _new_id("memo")
    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": "memo",
        "id": memo_id,
        "created_at": _now_iso_utc(),
        "from": from_agent,
        "to": [str(x).strip() for x in (to_list or ["*"]) if str(x).strip()],
        "subject": str(subject),
        "body": str(body),
    }
    if task_id:
        payload["related_task_id"] = str(task_id)
    if tags_csv:
        payload["tags"] = [t.strip() for t in str(tags_csv).split(",") if t.strip()]
    out = d / f"{memo_id}.json"
    _atomic_write_json(out, payload)
    return out


def _create_lock(
    q: Path,
    *,
    agent: str,
    scopes: list[str],
    mode: str,
    ttl_min: int | None,
    note: str | None,
) -> Path:
    d = _locks_dir(q)
    d.mkdir(parents=True, exist_ok=True)
    lock_id = _new_id("lock")

    now = datetime.now(timezone.utc)
    expires_at = None
    if ttl_min is not None and ttl_min > 0:
        expires_at = (now.replace(microsecond=0) + timedelta(minutes=int(ttl_min))).isoformat()

    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": "lock",
        "id": lock_id,
        "created_at": _now_iso_utc(),
        "created_by": agent,
        "mode": str(mode),
        "scopes": [_normalize_scope(s) for s in scopes],
    }
    if note:
        payload["note"] = str(note)
    if expires_at:
        payload["expires_at"] = expires_at

    out = d / f"{lock_id}.json"
    _atomic_write_json(out, payload)
    return out


def _find_agents(q: Path) -> list[dict]:
    d = _agents_dir(q)
    d.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for fp in sorted(d.glob("*.json")):
        obj = _load_json_maybe(fp)
        if not obj:
            continue
        obj["_path"] = str(fp)
        out.append(obj)
    return out


def _find_agent_by_id(q: Path, agent_id: str) -> dict | None:
    p = _agents_dir(q) / f"{agent_id}.json"
    if not p.exists():
        return None
    obj = _load_json_maybe(p)
    if not obj:
        return None
    obj["_path"] = str(p)
    return obj


def _find_agents_by_name(q: Path, name: str) -> list[dict]:
    name = (name or "").strip()
    if not name:
        return []
    agents = _find_agents(q)
    return [a for a in agents if str(a.get("name") or "").strip() == name]


def cmd_memo(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    agent = _agent_name(args) or "unknown"
    to_list = args.to if args.to else ["*"]

    body = ""
    if args.body is not None:
        body = str(args.body)
    elif args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    else:
        try:
            body = os.read(0, 10_000_000).decode("utf-8", errors="replace")
        except Exception:
            body = ""

    out = _create_memo(
        q,
        from_agent=agent,
        to_list=[str(x).strip() for x in to_list if str(x).strip()],
        subject=str(args.subject),
        body=body,
        task_id=str(args.task_id) if args.task_id else None,
        tags_csv=str(args.tags) if args.tags else None,
    )
    print(str(out))
    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": _now_iso_utc(),
            "actor": agent,
            "action": "memo_created",
            "memo_path": str(out),
            "subject": str(args.subject),
            "to": [str(x).strip() for x in to_list if str(x).strip()],
            "related_task_id": str(args.task_id) if args.task_id else None,
        },
    )
    return 0


def cmd_memos(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    d = _memos_dir(q)
    d.mkdir(parents=True, exist_ok=True)

    want_to = (str(args.to).strip() if args.to else "")
    want_from = (str(args.from_).strip() if args.from_ else "")

    rows: list[tuple[str, str, str, str, str, str]] = []
    for fp in sorted(d.glob("*.json"), reverse=True):
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue

        memo_id = str(obj.get("id") or fp.stem)
        created_at = str(obj.get("created_at") or "-")
        from_ = str(obj.get("from") or "-")
        to = obj.get("to") or []
        if not isinstance(to, list):
            to = [str(to)]
        to_str = ",".join(str(x) for x in to) if to else "-"
        subject = str(obj.get("subject") or "-")
        task_id = str(obj.get("related_task_id") or "-")

        if want_from and from_ != want_from:
            continue
        if want_to:
            if want_to not in to and "*" not in to:
                continue

        rows.append((created_at, memo_id, from_, to_str, subject, task_id))

    if not rows:
        print("(no memos)")
        return 0

    print("created_at\tmemo_id\tfrom\tto\tsubject\trelated_task_id")
    for r in rows:
        print("\t".join(r))
    return 0


def cmd_memo_show(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = _memos_dir(q) / f"{args.memo_id}.json"
    if not p.exists():
        print(f"memo not found: {p}")
        return 2
    obj = json.loads(p.read_text(encoding="utf-8"))
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


def cmd_lock(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    agent = _agent_name(args) or "unknown"
    scopes = [_normalize_scope(s) for s in (args.scopes or []) if str(s).strip()]
    if not scopes:
        print("at least one scope is required")
        return 2

    ttl_int = None
    if args.ttl_min is not None:
        try:
            ttl_int = int(args.ttl_min)
        except Exception:
            ttl_int = None

    out = _create_lock(q, agent=agent, scopes=scopes, mode=str(args.mode), ttl_min=ttl_int, note=args.note)
    print(str(out))
    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": _now_iso_utc(),
            "actor": agent,
            "action": "lock_created",
            "lock_path": str(out),
            "mode": str(args.mode),
            "scopes": scopes,
            "ttl_min": ttl_int,
        },
    )
    return 0


def cmd_unlock(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    d = _locks_dir(q)
    p = d / f"{args.lock_id}.json"
    if not p.exists():
        print(f"lock not found: {p}")
        return 2
    p.unlink()
    print(str(p))
    agent = _agent_name(args) or "unknown"
    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": _now_iso_utc(),
            "actor": agent,
            "action": "lock_removed",
            "lock_path": str(p),
        },
    )
    return 0


def cmd_locks(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    d = _locks_dir(q)
    d.mkdir(parents=True, exist_ok=True)

    rel_path = None
    if args.path:
        p = Path(str(args.path)).expanduser()
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        rel_path = _to_project_relative_str(p)

    rows: list[tuple[str, str, str, str, str, str, str, str]] = []
    now = datetime.now(timezone.utc)
    for fp in sorted(d.glob("*.json")):
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue

        lock_id = str(obj.get("id") or fp.stem)
        mode = str(obj.get("mode") or "-")
        created_by = str(obj.get("created_by") or "-")
        created_at = str(obj.get("created_at") or "-")
        expires_at = str(obj.get("expires_at") or "-")
        note = str(obj.get("note") or "-")
        scopes = obj.get("scopes") or []
        if not isinstance(scopes, list):
            scopes = [str(scopes)]
        scopes_str = ",".join(str(s) for s in scopes) if scopes else "-"

        exp_dt = _parse_iso(obj.get("expires_at"))
        expired = bool(exp_dt and exp_dt <= now)
        status = "expired" if expired else "active"
        if expired and not args.all:
            continue

        if rel_path:
            if not any(_scope_matches_path(str(s), rel_path) for s in scopes):
                continue

        rows.append((status, lock_id, mode, created_by, created_at, expires_at, scopes_str, note))

    if not rows:
        print("(no locks)")
        return 0

    print("status\tlock_id\tmode\tcreated_by\tcreated_at\texpires_at\tscopes\tnote")
    for r in rows:
        print("\t".join(r))
    return 0


def _find_locks(q: Path, *, include_expired: bool) -> list[dict]:
    d = _locks_dir(q)
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    out: list[dict] = []
    for fp in sorted(d.glob("*.json")):
        obj = _load_json_maybe(fp)
        if not obj:
            continue
        obj["_path"] = str(fp)
        exp_dt = _parse_iso(obj.get("expires_at"))
        expired = bool(exp_dt and exp_dt <= now)
        if expired and not include_expired:
            continue
        obj["_status"] = "expired" if expired else "active"
        out.append(obj)
    return out


def _find_memos(q: Path, *, limit: int | None) -> list[dict]:
    d = _memos_dir(q)
    d.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for fp in sorted(d.glob("*.json"), reverse=True):
        obj = _load_json_maybe(fp)
        if not obj:
            continue
        obj["_path"] = str(fp)
        out.append(obj)
        if limit is not None and len(out) >= int(limit):
            break
    return out


def _find_assignments(q: Path) -> list[dict]:
    d = _assignments_dir(q)
    d.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    for fp in sorted(d.glob("*.json"), reverse=True):
        obj = _load_json_maybe(fp)
        if not obj:
            continue
        obj["_path"] = str(fp)
        out.append(obj)
    return out


def cmd_overview(args: argparse.Namespace) -> int:
    """
    Human-friendly "who is doing what" overview:
    - agents (heartbeat)
    - active locks (grouped by created_by)
    - recent memos (grouped by from)
    - assignments (by agent_id)
    """
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    stale_sec = int(args.stale_sec)
    limit_memos = int(args.limit_memos)
    include_expired_locks = bool(args.include_expired_locks)
    json_mode = bool(args.json)

    now = datetime.now(timezone.utc)
    agents = _find_agents(q)
    locks = _find_locks(q, include_expired=include_expired_locks)
    memos = _find_memos(q, limit=max(200, limit_memos * 20))
    assignments = _find_assignments(q)

    agents_by_name: dict[str, list[dict]] = {}
    agents_by_id: dict[str, dict] = {}
    for a in agents:
        name = str(a.get("name") or "").strip()
        if name:
            agents_by_name.setdefault(name, []).append(a)
        aid = str(a.get("id") or "").strip()
        if aid:
            agents_by_id[aid] = a

    locks_by_actor: dict[str, list[dict]] = {}
    for lk in locks:
        actor = str(lk.get("created_by") or "unknown").strip() or "unknown"
        locks_by_actor.setdefault(actor, []).append(lk)

    memos_by_from: dict[str, list[dict]] = {}
    for m in memos:
        actor = str(m.get("from") or "unknown").strip() or "unknown"
        if limit_memos <= 0:
            continue
        bucket = memos_by_from.setdefault(actor, [])
        if len(bucket) < limit_memos:
            bucket.append(m)

    assigns_by_agent_id: dict[str, list[dict]] = {}
    for a in assignments:
        agent_id = str(a.get("agent_id") or "").strip()
        if not agent_id:
            continue
        assigns_by_agent_id.setdefault(agent_id, []).append(a)

    actors: set[str] = set()
    actors.update(agents_by_name.keys())
    actors.update(locks_by_actor.keys())
    actors.update(memos_by_from.keys())

    def _agent_status(agent_obj: dict) -> str:
        last_dt = _parse_iso(agent_obj.get("last_seen_at"))
        age = None
        if last_dt:
            try:
                age = int((now - last_dt).total_seconds())
            except Exception:
                age = None
        pid = agent_obj.get("pid")
        pid_alive = False
        try:
            pid_alive = bool(pid and _pid_is_alive(int(pid)))
        except Exception:
            pid_alive = False

        status = "active"
        if age is not None and age > stale_sec:
            status = "stale"
        if pid and not pid_alive:
            status = "dead"
        return status

    summaries: list[dict] = []
    for actor in sorted(actors):
        recs = agents_by_name.get(actor, [])
        best_rec = None
        if recs:
            best_rec = max(recs, key=lambda r: str(r.get("last_seen_at") or ""))
        status = _agent_status(best_rec) if best_rec else "unregistered"
        actor_locks = locks_by_actor.get(actor, [])
        actor_memos = memos_by_from.get(actor, [])

        actor_assignments: list[dict] = []
        if best_rec:
            aid = str(best_rec.get("id") or "").strip()
            if aid and aid in assigns_by_agent_id:
                actor_assignments = assigns_by_agent_id.get(aid, [])

        summaries.append(
            {
                "actor": actor,
                "status": status,
                "agent_records": recs,
                "locks": sorted(actor_locks, key=lambda r: str(r.get("created_at") or "")),
                "recent_memos": actor_memos,
                "assignments": actor_assignments,
            }
        )

    summaries.sort(key=lambda s: (-len(s.get("locks") or []), str(s.get("status") or ""), str(s.get("actor") or "")))

    payload = {
        "generated_at": _now_iso_utc(),
        "queue_dir": str(q),
        "counts": {
            "actors": len(summaries),
            "agents": len(agents),
            "locks": len(locks),
            "memos_scanned": len(memos),
            "assignments": len(assignments),
        },
        "actors": summaries,
    }

    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"[agent_org overview] queue_dir={payload['queue_dir']} actors={payload['counts']['actors']} locks={payload['counts']['locks']}")
    for s in summaries:
        actor = str(s.get("actor") or "-")
        status = str(s.get("status") or "-")
        lock_n = len(s.get("locks") or [])
        memo_n = len(s.get("recent_memos") or [])
        agent_n = len(s.get("agent_records") or [])
        print(f"- {actor} status={status} agents={agent_n} locks={lock_n} recent_memos={memo_n}")

        best = None
        if s.get("agent_records"):
            best = max(s["agent_records"], key=lambda r: str(r.get("last_seen_at") or ""))
        if best:
            pid = best.get("pid")
            role = best.get("assigned_role") or best.get("role") or "-"
            last_seen = best.get("last_seen_at") or "-"
            print(f"  agent: id={best.get('id') or '-'} role={role} pid={pid or '-'} last_seen_at={last_seen}")

        for lk in (s.get("locks") or [])[:10]:
            lid = lk.get("id") or Path(str(lk.get("_path") or "")).stem
            scopes = lk.get("scopes") or []
            if not isinstance(scopes, list):
                scopes = [str(scopes)]
            scopes_str = ",".join(str(x) for x in scopes[:6])
            if len(scopes) > 6:
                scopes_str += ",..."
            print(
                f"  lock: {lid} mode={lk.get('mode') or '-'} "
                f"expires_at={lk.get('expires_at') or '-'} scopes={scopes_str} note={lk.get('note') or '-'}"
            )
        if lock_n > 10:
            print(f"  lock: ... ({lock_n - 10} more)")

        for m in (s.get("recent_memos") or [])[:limit_memos]:
            mid = m.get("id") or Path(str(m.get("_path") or "")).stem
            created = m.get("created_at") or "-"
            subject = str(m.get("subject") or "-")
            if len(subject) > 120:
                subject = subject[:117] + "..."
            print(f"  memo: {mid} created_at={created} subject={subject}")

    return 0


def _agent_upsert(
    q: Path,
    *,
    agent_id: str,
    name: str,
    role: str,
    pid: int,
    host_pid: int | None,
    host_cmd: str | None,
    note: str | None = None,
    started_at: str | None = None,
) -> Path:
    p = _agents_dir(q) / f"{agent_id}.json"
    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": "agent",
        "id": agent_id,
        "name": name,
        "role": role,
        "pid": pid,
        "host_pid": host_pid,
        "host_cmd": host_cmd,
        "last_seen_at": _now_iso_utc(),
        "queue_dir": str(q),
        "project_root": str(PROJECT_ROOT),
    }

    def _update(cur: dict) -> dict:
        out = dict(cur or {})
        out.update(payload)
        if started_at:
            out["started_at"] = started_at
        elif cur.get("started_at"):
            out["started_at"] = cur.get("started_at")
        else:
            out["started_at"] = _now_iso_utc()
        if note:
            out["note"] = note
        return out

    _locked_update_json(p, _update)
    return p


def cmd_agents_list(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    stale_sec = int(args.stale_sec)
    now = datetime.now(timezone.utc)

    rows: list[tuple[str, str, str, str, str, str, str]] = []
    for a in _find_agents(q):
        agent_id = str(a.get("id") or "-")
        name = str(a.get("name") or "-")
        role = str(a.get("assigned_role") or a.get("role") or "-")
        pid = a.get("pid")
        host_pid = a.get("host_pid")
        last_seen = str(a.get("last_seen_at") or "-")
        last_dt = _parse_iso(a.get("last_seen_at"))
        age = None
        if last_dt:
            try:
                age = int((now - last_dt).total_seconds())
            except Exception:
                age = None
        pid_alive = False
        try:
            pid_alive = bool(pid and _pid_is_alive(int(pid)))
        except Exception:
            pid_alive = False

        status = "active"
        if age is not None and age > stale_sec:
            status = "stale"
        if pid and not pid_alive:
            status = "dead"

        rows.append(
            (
                status,
                agent_id,
                name,
                role,
                str(pid) if pid is not None else "-",
                str(host_pid) if host_pid is not None else "-",
                last_seen,
            )
        )

    if not rows:
        print("(no agents)")
        return 0

    print("status\tagent_id\tname\trole\tpid\thost_pid\tlast_seen_at")
    for r in sorted(rows, key=lambda x: (x[0], x[2], x[1])):
        print("\t".join(r))
    return 0


def cmd_agents_show(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    obj = _find_agent_by_id(q, args.agent_id)
    if not obj:
        print("agent not found", file=sys.stderr)
        return 2
    print(json.dumps(obj, ensure_ascii=False, indent=2))
    return 0


def cmd_agents_register(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    name = (args.name or _agent_name(args) or f"agent-{os.getpid()}").strip() or f"agent-{os.getpid()}"
    role = (args.role or "worker").strip() or "worker"
    agent_id = (args.agent_id or _new_id("agent")).strip()

    host_pid, host_cmd = _detect_codex_host_pid()
    p = _agent_upsert(
        q,
        agent_id=agent_id,
        name=name,
        role=role,
        pid=os.getpid(),
        host_pid=host_pid,
        host_cmd=host_cmd,
        note=args.note,
    )
    print(str(p))
    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": _now_iso_utc(),
            "actor": name,
            "action": "agent_registered",
            "agent_id": agent_id,
            "role": role,
            "pid": os.getpid(),
            "host_pid": host_pid,
        },
    )
    return 0


def cmd_agents_run(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    name = (args.name or _agent_name(args) or f"agent-{os.getpid()}").strip() or f"agent-{os.getpid()}"
    role = (args.role or "worker").strip() or "worker"
    agent_id = (args.agent_id or _new_id("agent")).strip()
    hb_sec = float(args.heartbeat_sec)

    host_pid, host_cmd = _detect_codex_host_pid()
    started_at = _now_iso_utc()
    _agent_upsert(
        q,
        agent_id=agent_id,
        name=name,
        role=role,
        pid=os.getpid(),
        host_pid=host_pid,
        host_cmd=host_cmd,
        note=args.note,
        started_at=started_at,
    )
    print(agent_id)

    stop_flag = {"stop": False}

    def _handle(_sig: int, _frame: object) -> None:
        stop_flag["stop"] = True

    try:
        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)
    except Exception:
        pass

    try:
        while not stop_flag["stop"]:
            _agent_upsert(
                q,
                agent_id=agent_id,
                name=name,
                role=role,
                pid=os.getpid(),
                host_pid=host_pid,
                host_cmd=host_cmd,
                note=args.note,
                started_at=started_at,
            )
            time.sleep(max(0.2, hb_sec))
    finally:
        p = _agents_dir(q) / f"{agent_id}.json"
        cur = _load_json_maybe(p)
        cur["stopped_at"] = _now_iso_utc()
        _atomic_write_json(p, cur)
        _append_event(
            q,
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "event",
                "created_at": _now_iso_utc(),
                "actor": name,
                "action": "agent_stopped",
                "agent_id": agent_id,
                "pid": os.getpid(),
            },
        )
    return 0


def cmd_agents_start(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    name = (args.name or _agent_name(args) or "agent").strip() or "agent"
    role = (args.role or "worker").strip() or "worker"
    agent_id = _new_id("agent")

    d = _agents_dir(q)
    d.mkdir(parents=True, exist_ok=True)
    log_path = d / f"{agent_id}.stdout.log"

    cmd: list[str] = [sys.executable, str(Path(__file__).resolve())]
    cmd += ["--queue-dir", str(q)]
    if args.agent_name:
        cmd += ["--agent-name", str(args.agent_name)]
    cmd += [
        "agents",
        "run",
        "--agent-id",
        agent_id,
        "--name",
        name,
        "--role",
        role,
        "--heartbeat-sec",
        str(args.heartbeat_sec),
    ]
    if args.note:
        cmd += ["--note", str(args.note)]

    with log_path.open("a", encoding="utf-8") as log_f:
        p = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=log_f,
            stderr=log_f,
            start_new_session=True,
        )

    print(json.dumps({"agent_id": agent_id, "pid": p.pid, "log": str(log_path)}, ensure_ascii=False))
    return 0


def cmd_agents_stop(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()

    agent_id = args.agent_id
    target = _find_agent_by_id(q, agent_id) if agent_id else None
    if not target and args.name:
        matches = _find_agents_by_name(q, str(args.name))
        if matches:
            matches.sort(key=lambda a: str(a.get("last_seen_at") or ""), reverse=True)
            target = matches[0]
            agent_id = str(target.get("id") or "")

    if not target:
        print("agent not found", file=sys.stderr)
        return 2

    pid = None
    try:
        pid = int(target.get("pid"))
    except Exception:
        pid = None
    if not pid or not _pid_is_alive(pid):
        print("agent process not alive", file=sys.stderr)
        return 2

    sig = signal.SIGKILL if args.force else signal.SIGTERM
    os.kill(pid, sig)

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _pid_is_alive(pid):
            print("stopped")
            return 0
        time.sleep(0.2)

    print("still running (use --force)", file=sys.stderr)
    return 2


def cmd_orchestrator_status(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    state_p = _orch_state_path(q)
    state = _load_json_maybe(state_p)

    lock_available = _try_lock_available(_orch_lock_path(q))
    lock_held = not lock_available

    pid = None
    try:
        pid = int(state.get("pid")) if state.get("pid") is not None else None
    except Exception:
        pid = None
    pid_alive = bool(pid and _pid_is_alive(pid))

    now = datetime.now(timezone.utc)
    last = _parse_iso(state.get("last_heartbeat_at") if isinstance(state, dict) else None)
    heartbeat_age_sec = None
    if last:
        try:
            heartbeat_age_sec = int((now - last).total_seconds())
        except Exception:
            heartbeat_age_sec = None

    out = {
        "lock_held": lock_held,
        "pid_alive": pid_alive,
        "heartbeat_age_sec": heartbeat_age_sec,
        "state_path": str(state_p),
        "state": state,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _orchestrator_process_requests(
    q: Path,
    *,
    orch_name: str,
    lease_id: str,
    verbose: bool,
) -> None:
    inbox = _orch_inbox_dir(q)
    outbox = _orch_outbox_dir(q)
    processed = _orch_processed_dir(q)
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)
    processed.mkdir(parents=True, exist_ok=True)

    for fp in sorted(inbox.glob("req__*.json")):
        req = {}
        try:
            req = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:
            resp = {
                "schema_version": SCHEMA_VERSION,
                "kind": "orchestrator_response",
                "id": fp.stem,
                "processed_at": _now_iso_utc(),
                "ok": False,
                "error": f"invalid_json: {e}",
            }
            _atomic_write_json(outbox / f"resp__{fp.stem}.json", resp)
            try:
                fp.replace(processed / fp.name)
            except Exception:
                pass
            continue

        req_id = str(req.get("id") or fp.stem)
        action = str(req.get("action") or "")
        payload = req.get("payload") or {}
        from_agent = str(req.get("from") or "unknown")

        ok = True
        result: dict = {}
        error: str | None = None

        try:
            if action == "memo":
                to_list = payload.get("to")
                if isinstance(to_list, str):
                    to_list = [to_list]
                if to_list is not None and not isinstance(to_list, list):
                    to_list = None
                subject = str(payload.get("subject") or "")
                body = str(payload.get("body") or "")
                if not subject:
                    raise ValueError("payload.subject is required")
                out = _create_memo(
                    q,
                    from_agent=orch_name,
                    to_list=[str(x) for x in (to_list or ["*"])],
                    subject=subject,
                    body=body,
                    task_id=str(payload.get("task_id") or "") or None,
                    tags_csv=str(payload.get("tags") or "") or None,
                )
                result = {"memo_path": str(out)}
            elif action == "lock":
                scopes = payload.get("scopes")
                if isinstance(scopes, str):
                    scopes = [scopes]
                if not isinstance(scopes, list) or not scopes:
                    raise ValueError("payload.scopes is required (list)")
                mode = str(payload.get("mode") or "no_write")
                ttl_min = payload.get("ttl_min")
                ttl_int = int(ttl_min) if ttl_min is not None else None
                out = _create_lock(
                    q,
                    agent=orch_name,
                    scopes=[str(s) for s in scopes],
                    mode=mode,
                    ttl_min=ttl_int,
                    note=str(payload.get("note") or "") or None,
                )
                result = {"lock_path": str(out)}
            elif action == "unlock":
                lock_id = str(payload.get("lock_id") or "")
                if not lock_id:
                    raise ValueError("payload.lock_id is required")
                lock_path = _locks_dir(q) / f"{lock_id}.json"
                if lock_path.exists():
                    lock_path.unlink()
                    result = {"unlocked": True, "lock_path": str(lock_path)}
                else:
                    result = {"unlocked": False, "lock_path": str(lock_path)}
            elif action == "set_role":
                agent_id = str(payload.get("agent_id") or "")
                agent_name = str(payload.get("agent_name") or "")
                role = str(payload.get("role") or "")
                if not role:
                    raise ValueError("payload.role is required")
                target = _find_agent_by_id(q, agent_id) if agent_id else None
                if not target and agent_name:
                    matches = _find_agents_by_name(q, agent_name)
                    if len(matches) != 1:
                        raise ValueError(f"agent_name match count must be 1 (got {len(matches)})")
                    target = matches[0]
                if not target:
                    raise ValueError("agent not found (agent_id or agent_name)")

                p = Path(str(target.get("_path") or ""))
                updated_at = _now_iso_utc()

                def _update(cur: dict) -> dict:
                    out = dict(cur or {})
                    out["assigned_role"] = role
                    out["assigned_role_updated_at"] = updated_at
                    out["assigned_role_updated_by"] = orch_name
                    return out

                _locked_update_json(p, _update)
                result = {"agent_path": str(p), "role": role}
            elif action == "assign_task":
                task_id = str(payload.get("task_id") or "")
                if not task_id:
                    raise ValueError("payload.task_id is required")
                agent_id = str(payload.get("agent_id") or "")
                agent_name = str(payload.get("agent_name") or "")
                note = str(payload.get("note") or "")

                to_list: list[str] | None = None
                if agent_name:
                    to_list = [agent_name]
                elif agent_id:
                    a = _find_agent_by_id(q, agent_id)
                    if a:
                        to_list = [str(a.get("name") or agent_id)]

                assign_id = _new_id("assign")
                aout = _assignments_dir(q) / f"{assign_id}.json"
                _atomic_write_json(
                    aout,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "kind": "assignment",
                        "id": assign_id,
                        "created_at": _now_iso_utc(),
                        "created_by": orch_name,
                        "task_id": task_id,
                        "agent_id": agent_id or None,
                        "agent_name": agent_name or None,
                        "note": note or None,
                    },
                )
                memo_body = "\n".join(
                    [
                        "TASK assigned by orchestrator.",
                        f"- task_id: {task_id}",
                        f"- assignment: {aout}",
                        (f"- note: {note}" if note else ""),
                        "",
                        "next:",
                        f"- python scripts/agent_runner.py show {task_id}",
                        f"- python scripts/agent_runner.py prompt {task_id}",
                    ]
                ).strip()
                mout = _create_memo(
                    q,
                    from_agent=orch_name,
                    to_list=to_list or ["*"],
                    subject=f"TASK ASSIGNED: {task_id}",
                    body=memo_body,
                    task_id=task_id,
                    tags_csv="assignment,task",
                )
                result = {"assignment_path": str(aout), "memo_path": str(mout)}
            else:
                raise ValueError(f"unknown action: {action}")
        except Exception as e:
            ok = False
            error = str(e)

        resp: dict = {
            "schema_version": SCHEMA_VERSION,
            "kind": "orchestrator_response",
            "id": req_id,
            "processed_at": _now_iso_utc(),
            "ok": ok,
            "action": action,
            "lease_id": lease_id,
            "from": from_agent,
        }
        if ok:
            resp["result"] = result
        else:
            resp["error"] = error

        _atomic_write_json(outbox / f"resp__{req_id}.json", resp)
        try:
            fp.replace(processed / fp.name)
        except Exception:
            try:
                fp.unlink()
            except Exception:
                pass

        _append_event(
            q,
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "event",
                "created_at": _now_iso_utc(),
                "actor": orch_name,
                "action": "orchestrator_request_processed",
                "lease_id": lease_id,
                "request_id": req_id,
                "request_action": action,
                "ok": ok,
                "result": result if ok else None,
                "error": error if not ok else None,
            },
        )
        if verbose:
            print(json.dumps(resp, ensure_ascii=False))


def cmd_orchestrator_run(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    d = _orch_dir(q)
    d.mkdir(parents=True, exist_ok=True)

    orch_name = (args.name or _agent_name(args) or "orchestrator").strip() or "orchestrator"
    lease_id = _new_id("orch")

    lock_path = _orch_lock_path(q)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        import fcntl  # unix-only
    except Exception:
        print("orchestrator lease requires fcntl (unix).", file=sys.stderr)
        return 2

    lock_f = lock_path.open("a")
    try:
        flags = fcntl.LOCK_EX
        if not args.wait:
            flags |= fcntl.LOCK_NB
        fcntl.flock(lock_f.fileno(), flags)
    except BlockingIOError:
        print("orchestrator already running (lease is held).", file=sys.stderr)
        lock_f.close()
        return 2
    except Exception as e:
        print(f"failed to acquire orchestrator lease: {e}", file=sys.stderr)
        lock_f.close()
        return 2

    pid = os.getpid()
    host_pid, host_cmd = _detect_codex_host_pid()

    state_p = _orch_state_path(q)
    state: dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": "orchestrator",
        "lease_id": lease_id,
        "name": orch_name,
        "pid": pid,
        "host_pid": host_pid,
        "host_cmd": host_cmd,
        "started_at": _now_iso_utc(),
        "last_heartbeat_at": _now_iso_utc(),
        "queue_dir": str(q),
        "project_root": str(PROJECT_ROOT),
        "lock_path": str(lock_path),
        "inbox_dir": str(_orch_inbox_dir(q)),
        "outbox_dir": str(_orch_outbox_dir(q)),
        "process_requests": bool(not args.no_process_requests),
    }
    _atomic_write_json(state_p, state)
    _orch_pidfile_path(q).write_text(str(pid) + "\n", encoding="utf-8")

    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": _now_iso_utc(),
            "actor": orch_name,
            "action": "orchestrator_lease_acquired",
            "lease_id": lease_id,
            "pid": pid,
            "host_pid": host_pid,
        },
    )

    stop_flag = {"stop": False}

    def _handle(_sig: int, _frame: object) -> None:
        stop_flag["stop"] = True

    try:
        signal.signal(signal.SIGINT, _handle)
        signal.signal(signal.SIGTERM, _handle)
    except Exception:
        pass

    hb_sec = float(args.heartbeat_sec)
    poll_sec = float(args.poll_sec)
    verbose = bool(args.verbose)

    try:
        while not stop_flag["stop"]:
            cur = _load_json_maybe(state_p)
            cur["last_heartbeat_at"] = _now_iso_utc()
            _atomic_write_json(state_p, cur)

            if not args.no_process_requests:
                _orchestrator_process_requests(q, orch_name=orch_name, lease_id=lease_id, verbose=verbose)

            time.sleep(max(0.1, min(hb_sec, poll_sec)))
    finally:
        try:
            cur = _load_json_maybe(state_p)
            cur["stopped_at"] = _now_iso_utc()
            _atomic_write_json(state_p, cur)
        except Exception:
            pass
        _append_event(
            q,
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "event",
                "created_at": _now_iso_utc(),
                "actor": orch_name,
                "action": "orchestrator_lease_released",
                "lease_id": lease_id,
                "pid": pid,
            },
        )
        try:
            lock_f.close()
        except Exception:
            pass

    return 0


def cmd_orchestrator_start(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    d = _orch_dir(q)
    d.mkdir(parents=True, exist_ok=True)

    if not _try_lock_available(_orch_lock_path(q)):
        print("orchestrator already running (lease is held).", file=sys.stderr)
        return 2

    log_path = d / "stdout.log"
    cmd: list[str] = [sys.executable, str(Path(__file__).resolve())]
    cmd += ["--queue-dir", str(q)]
    if args.agent_name:
        cmd += ["--agent-name", str(args.agent_name)]
    cmd += [
        "orchestrator",
        "run",
        "--name",
        str(args.name),
        "--heartbeat-sec",
        str(args.heartbeat_sec),
        "--poll-sec",
        str(args.poll_sec),
    ]
    if args.no_process_requests:
        cmd += ["--no-process-requests"]
    if args.wait:
        cmd += ["--wait"]
    if args.verbose:
        cmd += ["--verbose"]

    with log_path.open("a", encoding="utf-8") as log_f:
        p = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_ROOT),
            stdout=log_f,
            stderr=log_f,
            start_new_session=True,
        )
    _orch_pidfile_path(q).write_text(str(p.pid) + "\n", encoding="utf-8")
    print(str(p.pid))
    return 0


def cmd_orchestrator_stop(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    pid_p = _orch_pidfile_path(q)
    if not pid_p.exists():
        print("orchestrator pidfile not found", file=sys.stderr)
        return 2
    try:
        pid = int(pid_p.read_text(encoding="utf-8").strip())
    except Exception:
        print("invalid orchestrator pidfile", file=sys.stderr)
        return 2

    if not _pid_is_alive(pid):
        print("orchestrator process is not alive", file=sys.stderr)
        try:
            pid_p.unlink()
        except Exception:
            pass
        return 2

    sig = signal.SIGKILL if args.force else signal.SIGTERM
    os.kill(pid, sig)

    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _pid_is_alive(pid):
            try:
                pid_p.unlink()
            except Exception:
                pass
            print("stopped")
            return 0
        time.sleep(0.2)

    print("still running (use --force)", file=sys.stderr)
    return 2


def cmd_orchestrator_request(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    inbox = _orch_inbox_dir(q)
    outbox = _orch_outbox_dir(q)
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)

    from_agent = _agent_name(args) or "unknown"
    req_id = _new_id("req")

    payload: dict = {}
    if args.payload_json:
        raw = str(args.payload_json)
        if raw.startswith("@"):
            payload = json.loads(Path(raw[1:]).read_text(encoding="utf-8"))
        else:
            payload = json.loads(raw)

    req: dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": "orchestrator_request",
        "id": req_id,
        "created_at": _now_iso_utc(),
        "from": from_agent,
        "action": str(args.action),
        "payload": payload,
    }
    out = inbox / f"{req_id}.json"
    _atomic_write_json(out, req)
    print(str(out))

    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": _now_iso_utc(),
            "actor": from_agent,
            "action": "orchestrator_request_created",
            "request_id": req_id,
            "request_action": str(args.action),
            "path": str(out),
        },
    )

    if args.wait_sec and args.wait_sec > 0:
        deadline = time.time() + float(args.wait_sec)
        resp_p = outbox / f"resp__{req_id}.json"
        while time.time() < deadline:
            if resp_p.exists():
                print(resp_p.read_text(encoding="utf-8"))
                return 0
            time.sleep(0.2)
        print(f"timeout waiting for response: {resp_p}", file=sys.stderr)
        return 3

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="AI Org coordination helpers (agents/orchestrator/locks/memos)")
    p.add_argument("--queue-dir", default=None, help="override queue dir (default: env/ logs/agent_tasks)")
    p.add_argument("--agent-name", default=None, help="agent name (or set env LLM_AGENT_NAME)")

    sub = p.add_subparsers(dest="cmd", required=True)

    # Memos
    sp = sub.add_parser("memos", help="list memos")
    sp.add_argument("--to", default=None, help="filter by recipient (includes '*' broadcast)")
    sp.add_argument("--from", dest="from_", default=None, help="filter by sender")
    sp.set_defaults(func=cmd_memos)

    sp = sub.add_parser("memo", help="create a memo")
    sp.add_argument("--to", action="append", default=None, help="recipient agent name (repeatable); default '*'")
    sp.add_argument("--subject", required=True)
    sp.add_argument("--body", default=None, help="memo body (if omitted, read stdin or --body-file)")
    sp.add_argument("--body-file", default=None, help="file containing memo body (utf-8)")
    sp.add_argument("--task-id", default=None, help="optional related task_id")
    sp.add_argument("--tags", default=None, help="comma-separated tags")
    sp.set_defaults(func=cmd_memo)

    sp = sub.add_parser("memo-show", help="show a memo json by id")
    sp.add_argument("memo_id")
    sp.set_defaults(func=cmd_memo_show)

    # Locks
    sp = sub.add_parser("locks", help="list locks")
    sp.add_argument("--all", action="store_true", help="include expired locks")
    sp.add_argument("--path", default=None, help="filter locks affecting this repo-relative path")
    sp.set_defaults(func=cmd_locks)

    sp = sub.add_parser("lock", help="create a lock")
    sp.add_argument("scopes", nargs="+", help="repo-relative paths or globs (e.g. ui/**)")
    sp.add_argument("--mode", default="no_write", choices=["no_write", "read_only", "no_touch"])
    sp.add_argument("--ttl-min", dest="ttl_min", default=None, help="optional TTL minutes (auto-expire)")
    sp.add_argument("--note", default=None, help="optional note/reason")
    sp.set_defaults(func=cmd_lock)

    sp = sub.add_parser("unlock", help="remove a lock by id")
    sp.add_argument("lock_id")
    sp.set_defaults(func=cmd_unlock)

    # Overview
    sp = sub.add_parser("overview", help="show who-is-doing-what overview (agents + locks + memos)")
    sp.add_argument("--stale-sec", default=30, type=int, help="mark stale after N seconds (default: 30)")
    sp.add_argument("--limit-memos", default=3, type=int, help="include N recent memos per actor (default: 3)")
    sp.add_argument("--include-expired-locks", action="store_true", help="include expired locks")
    sp.add_argument("--json", action="store_true", help="emit JSON payload")
    sp.set_defaults(func=cmd_overview)

    # Agents
    sp = sub.add_parser("agents", help="agent registry + heartbeat")
    agents_sub = sp.add_subparsers(dest="agents_cmd", required=True)

    sp2 = agents_sub.add_parser("list", help="list registered agents")
    sp2.add_argument("--stale-sec", default=30, type=int, help="mark stale after N seconds (default: 30)")
    sp2.set_defaults(func=cmd_agents_list)

    sp2 = agents_sub.add_parser("show", help="show agent json")
    sp2.add_argument("agent_id")
    sp2.set_defaults(func=cmd_agents_show)

    sp2 = agents_sub.add_parser("register", help="one-shot upsert agent record")
    sp2.add_argument("--agent-id", default=None)
    sp2.add_argument("--name", default=None)
    sp2.add_argument("--role", default="worker")
    sp2.add_argument("--note", default=None)
    sp2.set_defaults(func=cmd_agents_register)

    sp2 = agents_sub.add_parser("run", help="run heartbeat loop (long-running)")
    sp2.add_argument("--agent-id", default=None)
    sp2.add_argument("--name", default=None)
    sp2.add_argument("--role", default="worker")
    sp2.add_argument("--heartbeat-sec", default=3.0, type=float)
    sp2.add_argument("--note", default=None)
    sp2.set_defaults(func=cmd_agents_run)

    sp2 = agents_sub.add_parser("start", help="start heartbeat in background")
    sp2.add_argument("--name", required=True)
    sp2.add_argument("--role", default="worker")
    sp2.add_argument("--heartbeat-sec", default=3.0, type=float)
    sp2.add_argument("--note", default=None)
    sp2.set_defaults(func=cmd_agents_start)

    sp2 = agents_sub.add_parser("stop", help="stop a background agent heartbeat (by id or name)")
    sp2.add_argument("--agent-id", default=None)
    sp2.add_argument("--name", default=None)
    sp2.add_argument("--force", action="store_true", help="SIGKILL")
    sp2.set_defaults(func=cmd_agents_stop)

    # Orchestrator
    sp = sub.add_parser("orchestrator", help="single-orchestrator lease + request inbox")
    orch_sub = sp.add_subparsers(dest="orch_cmd", required=True)

    sp2 = orch_sub.add_parser("status", help="show orchestrator status")
    sp2.set_defaults(func=cmd_orchestrator_status)

    sp2 = orch_sub.add_parser("run", help="run orchestrator (holds lease; long-running)")
    sp2.add_argument("--name", required=True, help="orchestrator name (e.g., 'dd-orch')")
    sp2.add_argument("--heartbeat-sec", dest="heartbeat_sec", default=3.0, type=float)
    sp2.add_argument("--poll-sec", dest="poll_sec", default=1.0, type=float)
    sp2.add_argument("--wait", action="store_true", help="wait for lease instead of failing fast")
    sp2.add_argument("--no-process-requests", action="store_true", help="disable inbox request processing")
    sp2.add_argument("--verbose", action="store_true", help="print processed request responses to stdout")
    sp2.set_defaults(func=cmd_orchestrator_run)

    sp2 = orch_sub.add_parser("start", help="start orchestrator in background")
    sp2.add_argument("--name", required=True, help="orchestrator name (e.g., 'dd-orch')")
    sp2.add_argument("--heartbeat-sec", dest="heartbeat_sec", default=3.0, type=float)
    sp2.add_argument("--poll-sec", dest="poll_sec", default=1.0, type=float)
    sp2.add_argument("--wait", action="store_true", help="wait for lease instead of failing fast")
    sp2.add_argument("--no-process-requests", action="store_true", help="disable inbox request processing")
    sp2.add_argument("--verbose", action="store_true", help="write request processing output to stdout.log")
    sp2.set_defaults(func=cmd_orchestrator_start)

    sp2 = orch_sub.add_parser("stop", help="stop orchestrator background process")
    sp2.add_argument("--force", action="store_true", help="SIGKILL")
    sp2.set_defaults(func=cmd_orchestrator_stop)

    sp2 = orch_sub.add_parser("request", help="write a request into the orchestrator inbox")
    sp2.add_argument("--action", required=True, choices=["memo", "lock", "unlock", "set_role", "assign_task"])
    sp2.add_argument("--payload-json", default=None, help="JSON string or @/path/to/file.json")
    sp2.add_argument("--wait-sec", dest="wait_sec", type=float, default=0.0, help="wait for response in outbox")
    sp2.set_defaults(func=cmd_orchestrator_request)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
