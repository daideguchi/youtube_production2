#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from factory_common.agent_mode import get_queue_dir

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agent_name(args: argparse.Namespace) -> str | None:
    raw = (getattr(args, "agent_name", None) or os.getenv("LLM_AGENT_NAME") or os.getenv("AGENT_NAME") or "").strip()
    return raw or None


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _coord_dir(q: Path) -> Path:
    return q / "coordination"


def _locks_dir(q: Path) -> Path:
    return _coord_dir(q) / "locks"


def _memos_dir(q: Path) -> Path:
    return _coord_dir(q) / "memos"


def _new_id(prefix: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}__{stamp}__{secrets.token_hex(4)}"


def _parse_iso(dt_str: str | None) -> datetime | None:
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(str(dt_str))
    except Exception:
        return None


def _to_project_relative_str(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT)).replace(os.sep, "/")
    except Exception:
        return str(path).replace(os.sep, "/")


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


def cmd_memo(args: argparse.Namespace) -> int:
    q = Path(args.queue_dir) if args.queue_dir else get_queue_dir()
    d = _memos_dir(q)
    d.mkdir(parents=True, exist_ok=True)

    agent = _agent_name(args) or "unknown"
    memo_id = _new_id("memo")
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

    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": "memo",
        "id": memo_id,
        "created_at": _now_iso_utc(),
        "from": agent,
        "to": [str(x).strip() for x in to_list if str(x).strip()],
        "subject": str(args.subject),
        "body": body,
    }
    if args.task_id:
        payload["related_task_id"] = str(args.task_id)
    if args.tags:
        payload["tags"] = [t.strip() for t in str(args.tags).split(",") if t.strip()]

    out = d / f"{memo_id}.json"
    _atomic_write_json(out, payload)
    print(str(out))
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
    d = _locks_dir(q)
    d.mkdir(parents=True, exist_ok=True)

    agent = _agent_name(args) or "unknown"
    lock_id = _new_id("lock")
    scopes = [_normalize_scope(s) for s in (args.scopes or []) if str(s).strip()]
    if not scopes:
        print("at least one scope is required")
        return 2

    now = datetime.now(timezone.utc)
    expires_at = None
    if args.ttl_min is not None:
        try:
            mins = int(args.ttl_min)
            if mins > 0:
                expires_at = (now.replace(microsecond=0) + timedelta(minutes=mins)).isoformat()
        except Exception:
            pass

    payload: dict = {
        "schema_version": SCHEMA_VERSION,
        "kind": "lock",
        "id": lock_id,
        "created_at": _now_iso_utc(),
        "created_by": agent,
        "mode": str(args.mode),
        "scopes": scopes,
    }
    if args.note:
        payload["note"] = str(args.note)
    if expires_at:
        payload["expires_at"] = expires_at

    out = d / f"{lock_id}.json"
    _atomic_write_json(out, payload)
    print(str(out))
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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Agent coordination helpers (locks/memos)")
    p.add_argument("--queue-dir", default=None, help="override queue dir (default: env/ logs/agent_tasks)")
    p.add_argument("--agent-name", default=None, help="agent name (or set env LLM_AGENT_NAME)")

    sub = p.add_subparsers(dest="cmd", required=True)

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

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

