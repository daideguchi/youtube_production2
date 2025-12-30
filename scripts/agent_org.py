#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

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


def _agent_name_explicit(args: argparse.Namespace) -> str | None:
    """
    Explicit agent identity used for write operations.

    Important:
    - No USER/LOGNAME fallback here. In parallel work that fallback makes attribution ambiguous
      and increases the chance of "I overwrote someone else's work" accidents.
    """
    raw = (getattr(args, "agent_name", None) or os.getenv("LLM_AGENT_NAME") or os.getenv("AGENT_NAME") or "").strip()
    return raw or None


def _identities_path(q: Path) -> Path:
    return _coord_dir(q) / "identities.json"


def _identity_key() -> str | None:
    """
    Best-effort key to remember agent name per terminal/session.

    Priority:
    1) TTY (best for "each terminal tab gets a name")
    2) Codex host PID (best-effort, when TTY unavailable)
    """
    try:
        if sys.stdin.isatty():
            tty = os.ttyname(sys.stdin.fileno())
            if tty:
                return f"tty:{tty}"
    except Exception:
        pass

    try:
        host_pid, _cmd = _detect_codex_host_pid()
        if host_pid:
            return f"host_pid:{int(host_pid)}"
    except Exception:
        pass

    return None


def _load_identity_name(q: Path, key: str) -> str | None:
    p = _identities_path(q)
    obj = _load_json_maybe(p)
    by_key = obj.get("by_key") if isinstance(obj, dict) else None
    if not isinstance(by_key, dict):
        return None
    rec = by_key.get(key)
    if isinstance(rec, dict):
        name = str(rec.get("name") or "").strip()
        return name or None
    if isinstance(rec, str):
        name = rec.strip()
        return name or None
    return None


def _store_identity_name(q: Path, key: str, name: str) -> None:
    p = _identities_path(q)
    now = _now_iso_utc()

    def _update(cur: dict) -> dict:
        if not isinstance(cur, dict):
            cur = {}
        cur.setdefault("schema_version", 1)
        cur.setdefault("kind", "agent_identities")
        cur["updated_at"] = now
        by_key = cur.get("by_key")
        if not isinstance(by_key, dict):
            by_key = {}
        by_key[key] = {"name": str(name), "set_at": now}
        cur["by_key"] = by_key
        return cur

    _locked_update_json(p, _update)


def _ensure_board_agent_entry(q: Path, agent: str) -> None:
    """
    Make sure the shared board contains a status entry for this agent.

    This enables "who is doing what" visibility without requiring humans to
    remember extra commands.
    """
    p = _board_path(q)
    now = _now_iso_utc()

    def _update(cur: dict) -> dict:
        cur = _board_ensure_shape(cur if isinstance(cur, dict) else {})
        agents = cur.get("agents") if isinstance(cur.get("agents"), dict) else {}
        if not isinstance(agents, dict):
            agents = {}

        st = agents.get(agent)
        if not isinstance(st, dict):
            st = {"doing": "-", "blocked": "-", "next": "-", "note": "(auto)"}
            st["updated_at"] = now
            agents[agent] = st
            cur["agents"] = agents
            cur["updated_at"] = now
            return cur

        # Do not clobber existing status. Only ensure minimum fields.
        st.setdefault("doing", "-")
        st.setdefault("blocked", "-")
        st.setdefault("next", "-")
        st.setdefault("note", "-")
        st.setdefault("updated_at", now)
        agents[agent] = st
        cur["agents"] = agents
        cur["updated_at"] = now
        return cur

    _locked_update_json(p, _update)


def _require_agent_name(args: argparse.Namespace, *, action: str) -> str | None:
    name = _agent_name_explicit(args)
    if name:
        try:
            q = Path(args.queue_dir) if getattr(args, "queue_dir", None) else get_queue_dir()
            _ensure_board_agent_entry(q, name)
        except Exception:
            pass
        return name

    q = Path(args.queue_dir) if getattr(args, "queue_dir", None) else get_queue_dir()
    key = _identity_key()
    if key:
        cached = _load_identity_name(q, key)
        if cached:
            try:
                _ensure_board_agent_entry(q, cached)
            except Exception:
                pass
            return cached

    # Interactive fallback: prompt once and remember (no need to export env vars).
    if sys.stdin.isatty():
        print(f"[agent_org] agent name is required for {action}.", file=sys.stderr)
        print("Enter agent name (suggest: dd-<area>-<nn>, e.g. dd-ui-01).", file=sys.stderr)
        try:
            sys.stderr.write("> ")
            sys.stderr.flush()
            typed = (sys.stdin.readline() or "").strip()
        except Exception:
            typed = ""
        if typed:
            if key:
                try:
                    _store_identity_name(q, key, typed)
                except Exception:
                    pass
            try:
                _ensure_board_agent_entry(q, typed)
            except Exception:
                pass
            return typed

    print(f"[error] agent name is required for {action}.", file=sys.stderr)
    print("        Set env `LLM_AGENT_NAME` or pass global `--agent-name <NAME>`.", file=sys.stderr)
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

def _board_path(q: Path) -> Path:
    return _coord_dir(q) / "board.json"


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
    # Prefer the earliest single-glob char as the cut point.
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
    taking conflicting locks. If you truly need overlap, use --force and
    coordinate via the shared board.
    """
    a = (scope_a or "").strip().replace("\\", "/").strip("/")
    b = (scope_b or "").strip().replace("\\", "/").strip("/")
    if not a or not b:
        return False

    a_glob = _scope_has_glob(a)
    b_glob = _scope_has_glob(b)

    # Exact path/dir relationship.
    if not a_glob and not b_glob:
        return _is_same_or_parent(a, b) or _is_same_or_parent(b, a)

    a_prefix = _scope_static_prefix(a).replace("\\", "/").strip("/")
    b_prefix = _scope_static_prefix(b).replace("\\", "/").strip("/")
    if a_prefix and b_prefix and (_is_same_or_parent(a_prefix, b_prefix) or _is_same_or_parent(b_prefix, a_prefix)):
        return True

    # Probe pattern match using the most specific stable candidate we have.
    a_probe = a_prefix if a_glob and a_prefix else a
    b_probe = b_prefix if b_glob and b_prefix else b
    try:
        if fnmatch.fnmatchcase(a_probe, b) or fnmatch.fnmatchcase(b_probe, a):
            return True
    except Exception:
        pass

    return False


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
    agent = _require_agent_name(args, action="memo")
    if not agent:
        return 2
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
    agent = _require_agent_name(args, action="lock")
    if not agent:
        return 2
    scopes = [_normalize_scope(s) for s in (args.scopes or []) if str(s).strip()]
    if not scopes:
        print("at least one scope is required")
        return 2

    ttl_int = None
    ttl_warned = False
    if args.ttl_min is not None:
        try:
            ttl_int = int(args.ttl_min)
        except Exception:
            ttl_int = None
            ttl_warned = True
            print(
                f"[warn] invalid --ttl-min={args.ttl_min!r}; lock will not auto-expire (use --ttl-min <minutes>)",
                file=sys.stderr,
            )
    if ttl_int is None or ttl_int <= 0:
        if not ttl_warned:
            if args.ttl_min is None:
                print("[warn] lock has no TTL; it will not auto-expire (use --ttl-min <minutes>)", file=sys.stderr)
            else:
                print(f"[warn] --ttl-min must be > 0 (got {args.ttl_min!r}); lock will not auto-expire", file=sys.stderr)
        ttl_int = None

    if not bool(getattr(args, "force", False)):
        existing = _find_locks(q, include_expired=False)
        conflicts: list[dict] = []
        for lk in existing:
            if str(lk.get("_status") or "") != "active":
                continue
            created_by = str(lk.get("created_by") or "").strip()
            if created_by and created_by == agent:
                continue
            lk_scopes = lk.get("scopes") or []
            if not isinstance(lk_scopes, list):
                lk_scopes = [str(lk_scopes)]
            for new_sc in scopes:
                hit = False
                for old_sc in lk_scopes:
                    if _scopes_may_intersect(new_sc, str(old_sc)):
                        hit = True
                        break
                if hit:
                    conflicts.append(lk)
                    break

        if conflicts:
            print("[blocked] lock scope intersects existing active locks:", file=sys.stderr)
            for lk in conflicts[:10]:
                lid = str(lk.get("id") or Path(str(lk.get("_path") or "")).stem)
                created_by = str(lk.get("created_by") or "-")
                mode = str(lk.get("mode") or "-")
                expires_at = str(lk.get("expires_at") or "-")
                note = str(lk.get("note") or "-")
                lk_scopes = lk.get("scopes") or []
                if not isinstance(lk_scopes, list):
                    lk_scopes = [str(lk_scopes)]
                scopes_str = ",".join(str(s) for s in lk_scopes[:6]) if lk_scopes else "-"
                if isinstance(lk_scopes, list) and len(lk_scopes) > 6:
                    scopes_str += ",..."
                print(
                    f"  - {lid} created_by={created_by} mode={mode} expires_at={expires_at} scopes={scopes_str} note={note}",
                    file=sys.stderr,
                )
            if len(conflicts) > 10:
                print(f"  ... ({len(conflicts) - 10} more)", file=sys.stderr)
            print("Resolve by coordinating (board/memo) or unlocking the conflicting lock.", file=sys.stderr)
            print("If you really need overlap, re-run with `--force` (not recommended).", file=sys.stderr)
            return 3

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

    if bool(getattr(args, "announce", False)):
        try:
            lock_obj = _load_json_maybe(out)
            lock_id = str(lock_obj.get("id") or Path(str(out)).stem)
            expires_at = str(lock_obj.get("expires_at") or "-")
            announce_tags = _parse_tags_csv(getattr(args, "announce_tags", None)) or ["lock", "coordination"]
            msg_lines = [
                "scope:",
                *[f"- {s}" for s in scopes],
                "locks:",
                f"- {lock_id}",
                "mode:",
                f"- {str(args.mode)}",
                "ttl_min:",
                f"- {ttl_int if ttl_int is not None else '-'}",
                "expires_at:",
                f"- {expires_at}",
                "note:",
                f"- {str(args.note) if args.note else '-'}",
            ]
            _, note_id = _board_append_note(
                q,
                agent=agent,
                topic=f"[FYI][lock] {agent}",
                message="\n".join(msg_lines) + "\n",
                tags=announce_tags,
            )
            print(f"[info] board note created: {note_id}", file=sys.stderr)
        except Exception as e:
            print(f"[warn] failed to create board note: {e}", file=sys.stderr)
    return 0


def cmd_unlock(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    d = _locks_dir(q)
    p = d / f"{args.lock_id}.json"
    agent = _require_agent_name(args, action="unlock")
    if not agent:
        return 2
    if not p.exists():
        print(f"lock not found: {p}")
        return 2
    p.unlink()
    print(str(p))
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
    if bool(getattr(args, "active", False)) and bool(getattr(args, "all", False)):
        print("cannot combine --active with --all", file=sys.stderr)
        return 2

    rel_path = None
    if args.path:
        p = Path(str(args.path)).expanduser()
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        rel_path = _to_project_relative_str(p)

    out_json: list[dict] = []
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

        if args.json:
            out_json.append(
                {
                    "status": status,
                    "id": lock_id,
                    "mode": mode,
                    "created_by": created_by,
                    "created_at": created_at,
                    "expires_at": expires_at if exp_dt else None,
                    "scopes": [str(s) for s in scopes],
                    "note": note if note != "-" else None,
                }
            )
            continue

        rows.append((status, lock_id, mode, created_by, created_at, expires_at, scopes_str, note))

    if args.json:
        print(json.dumps(out_json, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print("(no locks)")
        return 0

    print("status\tlock_id\tmode\tcreated_by\tcreated_at\texpires_at\tscopes\tnote")
    for r in rows:
        print("\t".join(r))
    return 0


def cmd_locks_audit(args: argparse.Namespace) -> int:
    """
    Report potentially risky locks:
    - active locks with no TTL (expires_at missing)

    The goal is to reduce "forgot to unlock" accidents in multi-agent work.
    """
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    d = _locks_dir(q)
    d.mkdir(parents=True, exist_ok=True)

    rel_path = None
    if args.path:
        p = Path(str(args.path)).expanduser()
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        rel_path = _to_project_relative_str(p)

    try:
        older_than_hours = float(args.older_than_hours)
    except Exception:
        older_than_hours = 0.0
    threshold = timedelta(hours=max(0.0, older_than_hours))

    now = datetime.now(timezone.utc)
    rows: list[tuple[str, str, str, str, str, str, str, str]] = []
    out_json: list[dict] = []

    for fp in sorted(d.glob("*.json")):
        obj = _load_json_maybe(fp)
        if not obj:
            continue
        lock_id = str(obj.get("id") or fp.stem)
        mode = str(obj.get("mode") or "-")
        created_by = str(obj.get("created_by") or "-")
        created_at = str(obj.get("created_at") or "-")
        note = str(obj.get("note") or "-")
        scopes = obj.get("scopes") or []
        if not isinstance(scopes, list):
            scopes = [str(scopes)]
        scopes_str = ",".join(str(s) for s in scopes) if scopes else "-"

        exp_dt = _parse_iso(obj.get("expires_at"))
        if exp_dt is not None:
            continue  # only audit no-expiry locks

        if rel_path:
            if not any(_scope_matches_path(str(s), rel_path) for s in scopes):
                continue

        created_dt = _parse_iso(obj.get("created_at"))
        age_td = (now - created_dt) if created_dt else None
        if age_td is not None and age_td < threshold:
            continue
        age_hours = f"{(age_td.total_seconds() / 3600.0):.1f}" if age_td is not None else "-"

        if args.json:
            out_json.append(
                {
                    "status": "active_no_expiry",
                    "id": lock_id,
                    "mode": mode,
                    "created_by": created_by,
                    "created_at": created_at,
                    "age_hours": None if age_hours == "-" else float(age_hours),
                    "scopes": [str(s) for s in scopes],
                    "note": note if note != "-" else None,
                }
            )
            continue

        rows.append(("active_no_expiry", lock_id, mode, created_by, created_at, "-", age_hours, scopes_str))

    if args.json:
        print(json.dumps(out_json, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print("(no risky locks)")
        return 0

    print("status\tlock_id\tmode\tcreated_by\tcreated_at\texpires_at\tage_hours\tscopes")
    for r in rows:
        print("\t".join(r))
    return 0


def cmd_locks_prune(args: argparse.Namespace) -> int:
    """
    Archive (or delete) expired locks that have been expired for N days.

    This keeps coordination/locks from growing unbounded while preserving safety:
    - active locks are never touched
    - no-expiry locks are never touched (they require manual review)
    """
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    agent = _require_agent_name(args, action="locks-prune")
    if not agent:
        return 2
    d = _locks_dir(q)
    d.mkdir(parents=True, exist_ok=True)

    try:
        older_than_days = float(args.older_than_days)
    except Exception:
        older_than_days = 30.0
    threshold = timedelta(days=max(0.0, older_than_days))

    dry_run = bool(args.dry_run)
    delete_mode = bool(args.delete)

    now = datetime.now(timezone.utc)
    to_archive: list[tuple[Path, Path]] = []
    to_delete: list[Path] = []
    skipped_no_expiry: list[str] = []

    for fp in sorted(d.glob("*.json")):
        obj = _load_json_maybe(fp)
        if not obj:
            continue
        exp_dt = _parse_iso(obj.get("expires_at"))
        if exp_dt is None:
            skipped_no_expiry.append(fp.name)
            continue
        if exp_dt > now:
            continue  # active
        if (now - exp_dt) < threshold:
            continue  # recently expired

        if delete_mode:
            to_delete.append(fp)
            continue

        yyyymm = exp_dt.strftime("%Y%m")
        dest_dir = d / "_archive" / yyyymm
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / fp.name
        if dest.exists():
            dest = dest_dir / f"{fp.stem}__{secrets.token_hex(2)}.json"
        to_archive.append((fp, dest))

    if not to_archive and not to_delete:
        if skipped_no_expiry:
            print(f"(no expired locks to prune; {len(skipped_no_expiry)} no-expiry locks need manual review)")
        else:
            print("(no expired locks to prune)")
        return 0

    for fp in to_delete:
        if dry_run:
            print(f"DELETE\t{fp}")
        else:
            fp.unlink(missing_ok=True)

    for src, dst in to_archive:
        if dry_run:
            print(f"ARCHIVE\t{src}\t->\t{dst}")
        else:
            src.replace(dst)

    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": _now_iso_utc(),
            "actor": agent,
            "action": "locks_pruned",
            "dry_run": dry_run,
            "delete": delete_mode,
            "older_than_days": older_than_days,
            "archived_count": len(to_archive),
            "deleted_count": len(to_delete),
            "skipped_no_expiry_count": len(skipped_no_expiry),
        },
    )

    print(
        f"done (dry_run={dry_run}) archived={len(to_archive)} deleted={len(to_delete)} "
        f"skipped_no_expiry={len(skipped_no_expiry)}"
    )
    return 0


def _board_default_payload() -> dict:
    return {
        "schema_version": 1,
        "kind": "agent_board",
        "updated_at": _now_iso_utc(),
        "agents": {},
        "areas": {},
        "log": [],
    }


def _board_ensure_shape(obj: dict) -> dict:
    if not isinstance(obj, dict):
        obj = {}
    base = _board_default_payload()
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
    return obj


def _load_board(q: Path) -> dict | None:
    p = _board_path(q)
    if not p.exists():
        return None
    obj = _load_json_maybe(p)
    if not isinstance(obj, dict):
        obj = {}
    return _board_ensure_shape(obj)


def _parse_tags_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    tags: list[str] = []
    for part in str(raw).split(","):
        t = part.strip()
        if t:
            tags.append(t)
    return tags


def _parse_reviewers_csv(raw: str | None) -> list[str]:
    if not raw:
        return []
    reviewers: list[str] = []
    for part in str(raw).split(","):
        name = part.strip()
        if name:
            reviewers.append(name)
    # de-dup preserve order
    out: list[str] = []
    seen: set[str] = set()
    for r in reviewers:
        if r in seen:
            continue
        out.append(r)
        seen.add(r)
    return out


def cmd_board_template(args: argparse.Namespace) -> int:
    """
    Print a common notation template for board usage.
    Keep it shell-safe: recommend heredoc with quoted delimiter (<<'EOF').
    """
    print(
        """# Shared Board Notation (BEP-1)

## 1) Status (board set)
python scripts/agent_org.py board set --doing "<AREA>: <WHAT>" --blocked "<BLOCKER or ->" --next "<NEXT>" --tags <csv>

例:
python scripts/agent_org.py board set --doing "remotion: CH08 rerender prep" --blocked "-" --next "render loop start" --tags remotion,video

## 1.5) Ownership (board area-set)
python scripts/agent_org.py board area-set <AREA> --owner <AGENT> --reviewers <csv> --note "<optional>"

## 2) Notes (board note)
topic は必ず先頭に種別を付ける:
[Q]=質問 / [DECISION]=決定 / [BLOCKER]=停止 / [FYI]=共有 / [REVIEW]=レビュー / [DONE]=完了

zshでの安全な投稿（展開事故を防ぐ）:
python scripts/agent_org.py board note --topic "[Q][remotion] ..." <<'EOF'
scope:
- <paths/globs>
locks:
- <lock_id or (none)>
now:
- <what happened / current state>
options:
1) ...
2) ...
ask:
- <what you need from others / decision needed>
commands:
- <commands to run (plain text)>
artifacts:
- <outputs/dirs touched>
EOF

※ backtick(`) や $(...) はシェルが展開するので、--message="..." 直書きは避ける。
   どうしても1行で渡すなら **単引用符** を使う（例: --message '...`...`...'）。

## 3) Threads
- `board show` で note_id を確認
- `board note-show <note_id>` で全文
- `board note --reply-to <note_id>` で同スレッドに返信
- `board threads` / `board thread-show <thread_id|note_id>` でスレッド単位に閲覧

## 4) Legacy cleanup (optional)
過去の投稿で note_id が無いものが混じった場合:
python scripts/agent_org.py board normalize --dry-run
python scripts/agent_org.py board normalize
"""
    )
    return 0


def _legacy_note_id(entry: dict) -> str:
    ts = str(entry.get("ts") or entry.get("created_at") or entry.get("time") or _now_iso_utc())
    dt = _parse_iso(ts)
    stamp = (dt.astimezone(timezone.utc) if dt else datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    base = "\n".join(
        [
            ts,
            str(entry.get("agent") or ""),
            str(entry.get("topic") or ""),
            str(entry.get("message") or "")[:2000],
        ]
    )
    digest = hashlib.sha1(base.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"note__{stamp}__{digest}"


def cmd_board_normalize(args: argparse.Namespace) -> int:
    """
    Normalize board entries:
    - add note_id (id) to legacy notes
    - ensure thread_id exists
    """
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    agent = _require_agent_name(args, action="board normalize")
    if not agent:
        return 2
    p = _board_path(q)
    if not p.exists():
        print("(no board)")
        return 0

    dry_run = bool(getattr(args, "dry_run", False))

    obj = _load_json_maybe(p)
    obj = _board_ensure_shape(obj if isinstance(obj, dict) else {})
    log = obj.get("log") or []
    if not isinstance(log, list) or not log:
        print("(no notes)")
        return 0

    def _compute_stats(entries: list[dict]) -> dict:
        id_missing = 0
        thread_missing = 0
        for e in entries:
            if not isinstance(e, dict):
                continue
            if not str(e.get("id") or "").strip():
                id_missing += 1
            if not str(e.get("thread_id") or "").strip():
                thread_missing += 1
        return {"id_missing": id_missing, "thread_id_missing": thread_missing, "total": len(entries)}

    stats_before = _compute_stats([e for e in log if isinstance(e, dict)])
    if dry_run:
        print(json.dumps({"path": str(p), "dry_run": True, "before": stats_before}, ensure_ascii=False, indent=2))
        return 0

    updated_at = _now_iso_utc()

    def _update(cur: dict) -> dict:
        cur = _board_ensure_shape(cur if isinstance(cur, dict) else {})
        entries = cur.get("log") if isinstance(cur.get("log"), list) else []
        if not isinstance(entries, list):
            entries = []

        seen: set[str] = set()
        for e in entries:
            if not isinstance(e, dict):
                continue
            nid = str(e.get("id") or "").strip()
            if not nid:
                nid = _legacy_note_id(e)
                while nid in seen:
                    nid = f"{nid}__{secrets.token_hex(2)}"
                e["id"] = nid
            seen.add(nid)

            tid = str(e.get("thread_id") or "").strip()
            if not tid:
                e["thread_id"] = nid

        cur["log"] = entries
        cur["updated_at"] = updated_at
        return cur

    _locked_update_json(p, _update)

    # Recompute stats after
    obj2 = _load_json_maybe(p)
    obj2 = _board_ensure_shape(obj2 if isinstance(obj2, dict) else {})
    log2 = obj2.get("log") if isinstance(obj2.get("log"), list) else []
    stats_after = _compute_stats([e for e in log2 if isinstance(e, dict)])

    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": updated_at,
            "actor": agent,
            "action": "board_normalized",
            "board_path": str(p),
            "before": stats_before,
            "after": stats_after,
        },
    )

    print(json.dumps({"path": str(p), "dry_run": False, "before": stats_before, "after": stats_after}, ensure_ascii=False, indent=2))
    return 0


def cmd_board_show(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = _board_path(q)
    if not p.exists():
        print("(no board)")
        return 0

    obj = _load_json_maybe(p)
    obj = _board_ensure_shape(obj if isinstance(obj, dict) else {})

    if args.json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return 0

    agents = obj.get("agents") or {}
    if not isinstance(agents, dict) or not agents:
        print("(board has no agent statuses yet)")
    else:
        rows: list[tuple[str, str, str, str, str]] = []
        for name, st in agents.items():
            if not isinstance(st, dict):
                st = {}
            updated_at = str(st.get("updated_at") or "-")
            doing = str(st.get("doing") or "-").replace("\t", " ").replace("\n", "\\n")
            blocked = str(st.get("blocked") or "-").replace("\t", " ").replace("\n", "\\n")
            nxt = str(st.get("next") or "-").replace("\t", " ").replace("\n", "\\n")
            rows.append((str(name), doing, blocked, nxt, updated_at))
        rows.sort(key=lambda r: r[4], reverse=True)

        print("agent\tdoing\tblocked\tnext\tupdated_at")
        for r in rows:
            print("\t".join(r))

    areas = obj.get("areas") or {}
    if isinstance(areas, dict) and areas:
        area_rows: list[tuple[str, str, str, str, str]] = []
        for area, st in areas.items():
            if not isinstance(st, dict):
                st = {}
            owner = str(st.get("owner") or "-")
            reviewers = st.get("reviewers") or []
            if not isinstance(reviewers, list):
                reviewers = [str(reviewers)]
            reviewers_str = ",".join(str(x) for x in reviewers) if reviewers else "-"
            updated_at = str(st.get("updated_at") or "-")
            note = str(st.get("note") or "-").replace("\t", " ").replace("\n", "\\n")
            if len(note) > 120:
                note = note[:117] + "..."
            area_rows.append((str(area), owner, reviewers_str, updated_at, note))
        area_rows.sort(key=lambda r: r[3], reverse=True)
        print("\narea\towner\treviewers\tupdated_at\tnote")
        for r in area_rows:
            print("\t".join(r))

    try:
        tail = int(args.tail)
    except Exception:
        tail = 5
    if tail <= 0:
        return 0

    log = obj.get("log") or []
    if not isinstance(log, list) or not log:
        return 0

    if any(isinstance(e, dict) and not str(e.get("id") or "").strip() for e in log):
        print("\n(warning) legacy notes without note_id detected; run: python scripts/agent_org.py board normalize")

    try:
        max_chars = int(args.max_chars)
    except Exception:
        max_chars = 240
    if getattr(args, "full", False):
        max_chars = 0

    print("\nrecent_log_ts\tnote_id\tagent\ttopic\tmessage")
    for e in log[-tail:]:
        if not isinstance(e, dict):
            continue
        ts = str(e.get("ts") or "-")
        note_id = str(e.get("id") or "-")
        agent = str(e.get("agent") or "-")
        topic = str(e.get("topic") or "-")
        msg = str(e.get("message") or "-").replace("\t", " ").replace("\n", "\\n")
        if max_chars > 0 and len(msg) > max_chars:
            msg = msg[:max_chars] + "…"
        print(f"{ts}\t{note_id}\t{agent}\t{topic}\t{msg}")

    return 0


def cmd_board_note_show(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = _board_path(q)
    if not p.exists():
        print("(no board)")
        return 0

    obj = _load_json_maybe(p)
    obj = _board_ensure_shape(obj if isinstance(obj, dict) else {})
    log = obj.get("log") or []
    if not isinstance(log, list) or not log:
        print("(no notes)")
        return 0

    target = str(args.note_id).strip()
    if not target:
        print("note_id is required")
        return 2

    found = None
    for e in reversed(log):
        if not isinstance(e, dict):
            continue
        if str(e.get("id") or "").strip() == target:
            found = e
            break

    if not found:
        print(f"note not found: {target}")
        return 2

    if args.json:
        print(json.dumps(found, ensure_ascii=False, indent=2))
        return 0

    print(f"id: {found.get('id') or '-'}")
    print(f"ts: {found.get('ts') or '-'}")
    print(f"agent: {found.get('agent') or '-'}")
    print(f"topic: {found.get('topic') or '-'}")
    tags = found.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    print(f"tags: {','.join(str(t) for t in tags) if tags else '-'}")
    print("\nmessage:\n" + str(found.get("message") or "-"))
    return 0


def cmd_board_threads(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = _board_path(q)
    if not p.exists():
        print("(no board)")
        return 0

    obj = _load_json_maybe(p)
    obj = _board_ensure_shape(obj if isinstance(obj, dict) else {})

    want_tag = str(getattr(args, "tag", "") or "").strip()

    log = obj.get("log") or []
    if not isinstance(log, list) or not log:
        print("(no threads)")
        return 0

    threads: dict[str, dict] = {}
    for e in log:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or "").strip()
        tid = str(e.get("thread_id") or eid).strip()
        if not tid:
            continue
        if want_tag:
            tags = e.get("tags") or []
            if not isinstance(tags, list):
                tags = [str(tags)]
            if want_tag not in [str(t) for t in tags]:
                continue
        ts = str(e.get("ts") or "")
        actor = str(e.get("agent") or "-")
        topic = str(e.get("topic") or "-")
        rec = threads.get(tid)
        if not rec:
            threads[tid] = {
                "thread_id": tid,
                "count": 1,
                "first_ts": ts,
                "last_ts": ts,
                "last_agent": actor,
                "root_topic": topic,
                "root_note_id": tid,
            }
            continue
        rec["count"] = int(rec.get("count") or 0) + 1
        if ts and (not rec.get("first_ts") or ts < str(rec["first_ts"])):
            rec["first_ts"] = ts
        if ts and (not rec.get("last_ts") or ts > str(rec["last_ts"])):
            rec["last_ts"] = ts
            rec["last_agent"] = actor

    # Try to set root_topic from the root note (id == thread_id) when present.
    by_id: dict[str, dict] = {}
    for e in log:
        if isinstance(e, dict) and str(e.get("id") or "").strip():
            by_id[str(e.get("id")).strip()] = e
    for tid, rec in threads.items():
        root = by_id.get(tid)
        if root and isinstance(root, dict):
            rec["root_topic"] = str(root.get("topic") or rec.get("root_topic") or "-")

    out = list(threads.values())
    out.sort(key=lambda r: str(r.get("last_ts") or ""), reverse=True)

    try:
        limit = int(getattr(args, "limit", 50))
    except Exception:
        limit = 50
    if limit > 0:
        out = out[:limit]

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print("last_ts\tthread_id\tcount\tlast_agent\troot_topic")
    for r in out:
        root_topic = str(r.get("root_topic") or "-").replace("\t", " ").replace("\n", "\\n")
        if len(root_topic) > 120:
            root_topic = root_topic[:117] + "..."
        print(
            "\t".join(
                [
                    str(r.get("last_ts") or "-"),
                    str(r.get("thread_id") or "-"),
                    str(r.get("count") or "0"),
                    str(r.get("last_agent") or "-"),
                    root_topic,
                ]
            )
        )
    return 0


def cmd_board_thread_show(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = _board_path(q)
    if not p.exists():
        print("(no board)")
        return 0

    obj = _load_json_maybe(p)
    obj = _board_ensure_shape(obj if isinstance(obj, dict) else {})

    log = obj.get("log") or []
    if not isinstance(log, list) or not log:
        print("(no notes)")
        return 0

    target = str(args.thread_id).strip()
    if not target:
        print("thread_id is required")
        return 2

    # Resolve note_id -> thread_id when needed.
    resolved = None
    for e in log:
        if not isinstance(e, dict):
            continue
        if str(e.get("id") or "").strip() == target:
            resolved = str(e.get("thread_id") or e.get("id") or target).strip()
            break
    tid = resolved or target

    thread_notes: list[dict] = []
    for e in log:
        if not isinstance(e, dict):
            continue
        eid = str(e.get("id") or "").strip()
        etid = str(e.get("thread_id") or eid).strip()
        if etid == tid or eid == tid:
            thread_notes.append(e)

    if not thread_notes:
        print(f"thread not found: {target}")
        return 2

    thread_notes.sort(key=lambda e: str(e.get("ts") or ""))

    if args.json:
        print(json.dumps(thread_notes, ensure_ascii=False, indent=2))
        return 0

    print(f"[thread] {tid} notes={len(thread_notes)}")
    for e in thread_notes:
        ts = str(e.get("ts") or "-")
        nid = str(e.get("id") or "-")
        agent = str(e.get("agent") or "-")
        topic = str(e.get("topic") or "-")
        reply_to = str(e.get("reply_to") or "-")
        print(f"\n- ts={ts} id={nid} agent={agent} reply_to={reply_to}\n  topic={topic}\n")
        print(str(e.get("message") or "-"))
    return 0


def cmd_board_set(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    agent = _require_agent_name(args, action="board set")
    if not agent:
        return 2
    p = _board_path(q)
    now = _now_iso_utc()
    tags = _parse_tags_csv(getattr(args, "tags", None))

    def _update(cur: dict) -> dict:
        cur = _board_ensure_shape(cur if isinstance(cur, dict) else {})
        agents = cur.get("agents") if isinstance(cur.get("agents"), dict) else {}
        if not isinstance(agents, dict):
            agents = {}

        if args.clear:
            agents.pop(agent, None)
            cur["agents"] = agents
            cur["updated_at"] = now
            return cur

        st = agents.get(agent)
        if not isinstance(st, dict):
            st = {}

        if args.doing is not None:
            st["doing"] = str(args.doing)
        if args.blocked is not None:
            st["blocked"] = str(args.blocked)
        if args.next is not None:
            st["next"] = str(args.next)
        if args.note is not None:
            st["note"] = str(args.note)
        if tags:
            st["tags"] = tags

        st["updated_at"] = now
        agents[agent] = st
        cur["agents"] = agents
        cur["updated_at"] = now
        return cur

    _locked_update_json(p, _update)

    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": now,
            "actor": agent,
            "action": "board_set",
            "board_path": str(p),
        },
    )
    print(str(p))
    return 0


def cmd_board_areas(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    p = _board_path(q)
    if not p.exists():
        print("(no board)")
        return 0

    obj = _load_json_maybe(p)
    obj = _board_ensure_shape(obj if isinstance(obj, dict) else {})

    areas = obj.get("areas") or {}
    if not isinstance(areas, dict) or not areas:
        print("(no areas)")
        return 0

    if args.json:
        print(json.dumps(areas, ensure_ascii=False, indent=2))
        return 0

    rows: list[tuple[str, str, str, str, str]] = []
    for area, st in areas.items():
        if not isinstance(st, dict):
            st = {}
        owner = str(st.get("owner") or "-")
        reviewers = st.get("reviewers") or []
        if not isinstance(reviewers, list):
            reviewers = [str(reviewers)]
        reviewers_str = ",".join(str(x) for x in reviewers) if reviewers else "-"
        updated_at = str(st.get("updated_at") or "-")
        note = str(st.get("note") or "-").replace("\t", " ").replace("\n", "\\n")
        if len(note) > 120:
            note = note[:117] + "..."
        rows.append((str(area), owner, reviewers_str, updated_at, note))
    rows.sort(key=lambda r: r[3], reverse=True)

    print("area\towner\treviewers\tupdated_at\tnote")
    for r in rows:
        print("\t".join(r))
    return 0


def cmd_board_area_set(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    agent = _require_agent_name(args, action="board area-set")
    if not agent:
        return 2
    p = _board_path(q)
    now = _now_iso_utc()
    area = str(args.area).strip()
    if not area:
        print("area is required", file=sys.stderr)
        return 2

    reviewers = _parse_reviewers_csv(getattr(args, "reviewers", None))

    def _update(cur: dict) -> dict:
        cur = _board_ensure_shape(cur if isinstance(cur, dict) else {})
        areas = cur.get("areas") if isinstance(cur.get("areas"), dict) else {}
        if not isinstance(areas, dict):
            areas = {}

        if args.clear:
            areas.pop(area, None)
            cur["areas"] = areas
            cur["updated_at"] = now
            return cur

        st = areas.get(area)
        if not isinstance(st, dict):
            st = {}
        if args.owner is not None:
            st["owner"] = str(args.owner).strip() if str(args.owner).strip() else None
        if args.reviewers is not None:
            st["reviewers"] = reviewers
        if args.note is not None:
            st["note"] = str(args.note)
        st["updated_at"] = now
        st["updated_by"] = agent
        areas[area] = st
        cur["areas"] = areas
        cur["updated_at"] = now
        return cur

    _locked_update_json(p, _update)

    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": now,
            "actor": agent,
            "action": "board_area_set",
            "board_path": str(p),
            "area": area,
        },
    )
    print(str(p))
    return 0


def _board_append_note(
    q: Path,
    *,
    agent: str,
    topic: str,
    message: str,
    tags: list[str] | None = None,
    reply_to: str | None = None,
) -> tuple[Path, str]:
    p = _board_path(q)
    now = _now_iso_utc()
    topic = str(topic or "").strip()
    msg = str(message or "").strip()
    tags = tags or []

    if not topic:
        raise ValueError("topic is required")
    if not msg:
        raise ValueError("message is required")

    note_id = _new_id("note")
    entry = {
        "id": note_id,
        "thread_id": note_id,  # overwritten for replies
        "ts": now,
        "agent": agent,
        "topic": topic,
        "message": msg,
        "tags": tags,
    }

    def _update(cur: dict) -> dict:
        cur = _board_ensure_shape(cur if isinstance(cur, dict) else {})
        agents = cur.get("agents") if isinstance(cur.get("agents"), dict) else {}
        if not isinstance(agents, dict):
            agents = {}
        st = agents.get(agent)
        if not isinstance(st, dict):
            st = {}
        st["last_note_at"] = now
        st.setdefault("updated_at", now)
        agents[agent] = st
        cur["agents"] = agents

        log = cur.get("log") if isinstance(cur.get("log"), list) else []
        if not isinstance(log, list):
            log = []

        if reply_to:
            parent = None
            for e in reversed(log):
                if isinstance(e, dict) and str(e.get("id") or "").strip() == reply_to:
                    parent = e
                    break
            if not parent:
                raise ValueError(f"reply_to note not found: {reply_to}")
            entry["reply_to"] = reply_to
            entry["thread_id"] = str(parent.get("thread_id") or parent.get("id") or reply_to)

        log.append(entry)
        max_log = 1000
        if len(log) > max_log:
            log = log[-max_log:]
        cur["log"] = log
        cur["updated_at"] = now
        return cur

    _locked_update_json(p, _update)
    _append_event(
        q,
        {
            "schema_version": SCHEMA_VERSION,
            "kind": "event",
            "created_at": now,
            "actor": agent,
            "action": "board_note",
            "board_path": str(p),
            "topic": topic,
        },
    )
    return p, note_id


def cmd_board_note(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    agent = _require_agent_name(args, action="board note")
    if not agent:
        return 2
    tags = _parse_tags_csv(getattr(args, "tags", None))

    msg = None
    if getattr(args, "message_file", None):
        msg = Path(str(args.message_file)).read_text(encoding="utf-8")
    elif getattr(args, "message", None):
        msg = str(args.message)
    else:
        raw = sys.stdin.read()
        if raw and raw.strip():
            msg = raw

    reply_to = str(getattr(args, "reply_to", "") or "").strip() or None
    try:
        p, note_id = _board_append_note(
            q,
            agent=agent,
            topic=str(args.topic),
            message=str(msg or ""),
            tags=tags,
            reply_to=reply_to,
        )
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2

    print(str(p))
    print(f"note_id: {note_id}")
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
    - shared board status (optional)
    """
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    stale_sec = int(args.stale_sec)
    limit_memos = int(args.limit_memos)
    include_expired_locks = bool(args.include_expired_locks)
    json_mode = bool(args.json)

    pinned_path = PROJECT_ROOT / "ssot" / "agent_runbooks" / "OVERVIEW_PINNED.md"
    pinned_preview: list[str] = []
    if pinned_path.exists():
        try:
            raw_lines = pinned_path.read_text(encoding="utf-8").splitlines()
            # Keep a short, non-empty preview (avoid flooding overview output).
            for ln in raw_lines:
                s = ln.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    continue
                pinned_preview.append(s)
                if len(pinned_preview) >= 12:
                    break
        except Exception:
            pinned_preview = []

    now = datetime.now(timezone.utc)
    agents = _find_agents(q)
    locks = _find_locks(q, include_expired=include_expired_locks)
    memos = _find_memos(q, limit=max(200, limit_memos * 20))
    assignments = _find_assignments(q)
    board = _load_board(q)
    board_agents = (board.get("agents") if isinstance(board, dict) else {}) or {}
    if not isinstance(board_agents, dict):
        board_agents = {}

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
        actor_board = board_agents.get(actor) if board_agents else None
        if not isinstance(actor_board, dict):
            actor_board = None

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
                "board": actor_board,
            }
        )

    summaries.sort(key=lambda s: (-len(s.get("locks") or []), str(s.get("status") or ""), str(s.get("actor") or "")))

    payload = {
        "generated_at": _now_iso_utc(),
        "queue_dir": str(q),
        "pinned": {
            "path": str(pinned_path.relative_to(PROJECT_ROOT)) if pinned_path.exists() else None,
            "preview": pinned_preview,
        },
        "counts": {
            "actors": len(summaries),
            "agents": len(agents),
            "locks": len(locks),
            "memos_scanned": len(memos),
            "assignments": len(assignments),
            "board_enabled": bool(board),
        },
        "actors": summaries,
    }

    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"[agent_org overview] queue_dir={payload['queue_dir']} actors={payload['counts']['actors']} locks={payload['counts']['locks']}")
    if pinned_path.exists():
        print(f"[pinned] {pinned_path.relative_to(PROJECT_ROOT)}")
        for ln in pinned_preview:
            print(f"  {ln}")
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

        board_st = s.get("board") or None
        if isinstance(board_st, dict) and board_st:
            doing = str(board_st.get("doing") or "-").replace("\t", " ").replace("\n", "\\n")
            blocked = str(board_st.get("blocked") or "-").replace("\t", " ").replace("\n", "\\n")
            nxt = str(board_st.get("next") or "-").replace("\t", " ").replace("\n", "\\n")
            upd = str(board_st.get("updated_at") or "-")
            print(f"  board: doing={doing} blocked={blocked} next={nxt} updated_at={upd}")

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

    # Soft warning: duplicate agent names make board/locks attribution ambiguous.
    try:
        stale_sec = 30
        now = datetime.now(timezone.utc)
        for rec in _find_agents_by_name(q, name):
            if str(rec.get("stopped_at") or "").strip():
                continue
            pid = rec.get("pid")
            pid_alive = False
            try:
                pid_alive = bool(pid and _pid_is_alive(int(pid)))
            except Exception:
                pid_alive = False
            last_dt = _parse_iso(rec.get("last_seen_at"))
            age = None
            if last_dt:
                try:
                    age = int((now - last_dt).total_seconds())
                except Exception:
                    age = None
            if pid_alive and age is not None and age <= stale_sec:
                rid = str(rec.get("id") or "-")
                last_seen = str(rec.get("last_seen_at") or "-")
                print(
                    f"[warn] agent name already in use: name={name!r} agent_id={rid} pid={pid} last_seen_at={last_seen}. "
                    "Use a unique LLM_AGENT_NAME (e.g., dd-ui-02) to avoid clobbering.",
                    file=sys.stderr,
                )
                break
    except Exception:
        pass

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
                lock_owner = str(payload.get("created_by") or from_agent or orch_name).strip() or orch_name
                mode = str(payload.get("mode") or "no_write")
                note = str(payload.get("note") or "") or None
                force = bool(payload.get("force") or False)
                announce = bool(payload.get("announce") if payload.get("announce") is not None else True)
                announce_tags = str(payload.get("announce_tags") or "").strip() or None

                ttl_int = None
                ttl_min = payload.get("ttl_min")
                if ttl_min is not None:
                    try:
                        ttl_int = int(ttl_min)
                    except Exception:
                        ttl_int = None

                scopes_norm = [_normalize_scope(str(s)) for s in scopes if str(s).strip()]
                if not scopes_norm:
                    raise ValueError("payload.scopes is required (list)")

                if not force:
                    existing = _find_locks(q, include_expired=False)
                    conflicts: list[dict] = []
                    for lk in existing:
                        if str(lk.get("_status") or "") != "active":
                            continue
                        created_by = str(lk.get("created_by") or "").strip()
                        if created_by and created_by == lock_owner:
                            continue
                        lk_scopes = lk.get("scopes") or []
                        if not isinstance(lk_scopes, list):
                            lk_scopes = [str(lk_scopes)]
                        hit = False
                        for new_sc in scopes_norm:
                            for old_sc in lk_scopes:
                                if _scopes_may_intersect(new_sc, str(old_sc)):
                                    hit = True
                                    break
                            if hit:
                                break
                        if hit:
                            conflicts.append(lk)

                    if conflicts:
                        lid = str(conflicts[0].get("id") or Path(str(conflicts[0].get("_path") or "")).stem)
                        by = str(conflicts[0].get("created_by") or "-")
                        raise ValueError(
                            f"lock scope intersects existing active locks (n={len(conflicts)}). "
                            f"example: {lid} created_by={by} (use force=true if you truly need overlap)"
                        )

                try:
                    _ensure_board_agent_entry(q, lock_owner)
                except Exception:
                    pass

                out = _create_lock(q, agent=lock_owner, scopes=scopes_norm, mode=mode, ttl_min=ttl_int, note=note)

                note_id = None
                if announce:
                    try:
                        lock_obj = _load_json_maybe(out)
                        lock_id = str(lock_obj.get("id") or Path(str(out)).stem)
                        expires_at = str(lock_obj.get("expires_at") or "-")
                        tags = _parse_tags_csv(announce_tags) or ["lock", "coordination"]
                        msg_lines = [
                            "scope:",
                            *[f"- {s}" for s in scopes_norm],
                            "locks:",
                            f"- {lock_id}",
                            "mode:",
                            f"- {mode}",
                            "ttl_min:",
                            f"- {ttl_int if ttl_int is not None else '-'}",
                            "expires_at:",
                            f"- {expires_at}",
                            "note:",
                            f"- {note if note else '-'}",
                        ]
                        _, note_id = _board_append_note(
                            q,
                            agent=lock_owner,
                            topic=f"[FYI][lock] {lock_owner}",
                            message="\n".join(msg_lines) + "\n",
                            tags=tags,
                        )
                    except Exception:
                        note_id = None

                result = {"lock_path": str(out), "board_note_id": note_id}
            elif action == "unlock":
                lock_id = str(payload.get("lock_id") or "")
                if not lock_id:
                    raise ValueError("payload.lock_id is required")
                lock_id = lock_id.replace("\\", "/").strip()
                if "/" in lock_id:
                    lock_id = lock_id.rsplit("/", 1)[-1]
                if lock_id.endswith(".json"):
                    lock_id = lock_id[: -len(".json")]

                lock_path = _locks_dir(q) / f"{lock_id}.json"
                existed = lock_path.exists()
                if existed:
                    lock_path.unlink()
                _append_event(
                    q,
                    {
                        "schema_version": SCHEMA_VERSION,
                        "kind": "event",
                        "created_at": _now_iso_utc(),
                        "actor": str(from_agent or orch_name),
                        "action": "lock_removed" if existed else "lock_remove_missing",
                        "lock_path": str(lock_path),
                    },
                )
                result = {"unlocked": existed, "lock_path": str(lock_path)}
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

    from_agent = _require_agent_name(args, action="orchestrator request")
    if not from_agent:
        return 2
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
    p.add_argument("--queue-dir", default=None, help="override queue dir (default: env/ workspaces/logs/agent_tasks)")
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
    sp.add_argument("--active", action="store_true", help="only active locks (default)")
    sp.add_argument("--all", action="store_true", help="include expired locks")
    sp.add_argument("--json", action="store_true", help="emit JSON array")
    sp.add_argument("--path", default=None, help="filter locks affecting this repo-relative path")
    sp.set_defaults(func=cmd_locks)

    sp = sub.add_parser("locks-audit", help="report potentially risky locks (e.g. no-expiry)")
    sp.add_argument("--older-than-hours", default=0, help="only show no-expiry locks older than N hours")
    sp.add_argument("--json", action="store_true", help="emit JSON array")
    sp.add_argument("--path", default=None, help="filter locks affecting this repo-relative path")
    sp.set_defaults(func=cmd_locks_audit)

    sp = sub.add_parser("locks-prune", help="archive old expired locks (housekeeping)")
    sp.add_argument("--older-than-days", default=30, help="prune locks expired for >= N days (default: 30)")
    sp.add_argument("--dry-run", action="store_true", help="print actions without modifying files")
    sp.add_argument("--delete", action="store_true", help="permanently delete instead of archiving")
    sp.set_defaults(func=cmd_locks_prune)

    sp = sub.add_parser("lock", help="create a lock")
    sp.add_argument("scopes", nargs="+", help="repo-relative paths or globs (e.g. ui/**)")
    sp.add_argument("--mode", default="no_write", choices=["no_write", "read_only", "no_touch"])
    sp.add_argument("--ttl-min", dest="ttl_min", default=None, help="optional TTL minutes (auto-expire)")
    sp.add_argument("--note", default=None, help="optional note/reason")
    sp.add_argument(
        "--force",
        action="store_true",
        help="allow creating a lock even if it overlaps existing active locks (not recommended)",
    )
    announce_grp = sp.add_mutually_exclusive_group()
    announce_grp.add_argument("--announce", dest="announce", action="store_true", default=True, help="post a shared-board note about this lock (default)")
    announce_grp.add_argument("--no-announce", dest="announce", action="store_false", help="do not post to the shared board")
    sp.add_argument("--announce-tags", default=None, help="board note tags (comma-separated; default: lock,coordination)")
    sp.set_defaults(func=cmd_lock)

    sp = sub.add_parser("unlock", help="remove a lock by id")
    sp.add_argument("lock_id")
    sp.set_defaults(func=cmd_unlock)

    # Shared board (single file)
    sp = sub.add_parser("board", help="shared board (single file) for multi-agent collaboration")
    board_sub = sp.add_subparsers(dest="board_cmd", required=True)

    sp2 = board_sub.add_parser("show", help="show board status and recent notes")
    sp2.add_argument("--json", action="store_true", help="emit raw JSON")
    sp2.add_argument("--tail", default=5, type=int, help="show last N log entries (default: 5; 0 disables)")
    sp2.add_argument("--max-chars", default=240, type=int, help="truncate message preview to N chars (default: 240; 0 disables)")
    sp2.add_argument("--full", action="store_true", help="do not truncate message preview (same as --max-chars 0)")
    sp2.set_defaults(func=cmd_board_show)

    sp2 = board_sub.add_parser("set", help="update my status on the board")
    sp2.add_argument("--doing", default=None, help="what I'm doing now")
    sp2.add_argument("--blocked", default=None, help="what I'm blocked on")
    sp2.add_argument("--next", default=None, help="what I'll do next")
    sp2.add_argument("--note", default=None, help="free-form note (optional)")
    sp2.add_argument("--tags", default=None, help="comma-separated tags")
    sp2.add_argument("--clear", action="store_true", help="remove my status entry from the board")
    sp2.set_defaults(func=cmd_board_set)

    sp2 = board_sub.add_parser("note", help="append a note to the board log")
    sp2.add_argument("--topic", required=True, help="short topic/title")
    sp2.add_argument("--message", default=None, help="note body (if omitted, read stdin or --message-file)")
    sp2.add_argument("--message-file", default=None, help="file containing note body (utf-8)")
    sp2.add_argument("--reply-to", dest="reply_to", default=None, help="reply to an existing note_id (same thread)")
    sp2.add_argument("--tags", default=None, help="comma-separated tags")
    sp2.set_defaults(func=cmd_board_note)

    sp2 = board_sub.add_parser("note-show", help="show a single note by note_id (full message)")
    sp2.add_argument("note_id", help="note id shown in `board show` output")
    sp2.add_argument("--json", action="store_true", help="emit raw JSON entry")
    sp2.set_defaults(func=cmd_board_note_show)

    sp2 = board_sub.add_parser("template", help="print common notation template (BEP-1)")
    sp2.set_defaults(func=cmd_board_template)

    sp2 = board_sub.add_parser("normalize", help="normalize legacy notes (add note_id/thread_id)")
    sp2.add_argument("--dry-run", action="store_true", help="print stats only without modifying files")
    sp2.set_defaults(func=cmd_board_normalize)

    sp2 = board_sub.add_parser("areas", help="list ownership areas (who owns what)")
    sp2.add_argument("--json", action="store_true", help="emit raw JSON")
    sp2.set_defaults(func=cmd_board_areas)

    sp2 = board_sub.add_parser("area-set", help="set ownership for an area")
    sp2.add_argument("area", help="area key (e.g., script/audio/video/ui)")
    sp2.add_argument("--owner", default=None, help="owner agent name")
    sp2.add_argument("--reviewers", default=None, help="comma-separated reviewers")
    sp2.add_argument("--note", default=None, help="optional note")
    sp2.add_argument("--clear", action="store_true", help="remove this area entry")
    sp2.set_defaults(func=cmd_board_area_set)

    sp2 = board_sub.add_parser("threads", help="list recent threads")
    sp2.add_argument("--json", action="store_true", help="emit JSON")
    sp2.add_argument("--tag", default=None, help="filter threads that include tag")
    sp2.add_argument("--limit", default=50, type=int, help="max threads to show (default: 50; 0 disables)")
    sp2.set_defaults(func=cmd_board_threads)

    sp2 = board_sub.add_parser("thread-show", help="show a thread by thread_id (or note_id)")
    sp2.add_argument("thread_id", help="thread_id or note_id belonging to the thread")
    sp2.add_argument("--json", action="store_true", help="emit JSON list")
    sp2.set_defaults(func=cmd_board_thread_show)

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
