#!/usr/bin/env python3
"""
ops_session — start/end bookkeeping for interrupted work.

Why:
- Long runs + multi-agent work + LLM sessions can get interrupted.
- If we always record "start" and "end", we can reliably see:
  - which session never reached "end"
  - which locks were held at start/end
  - which standard checks ran (and where their logs are)

Writes:
  workspaces/logs/ops/sessions/<session_id>/{start.json,end.json,checks/*}
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import logs_root, repo_root  # noqa: E402


SCHEMA_VERSION = 1


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _root() -> Path:
    return repo_root()


def _queue_dir() -> Path:
    raw = (os.getenv("LLM_AGENT_QUEUE_DIR") or "").strip()
    if raw:
        try:
            p = Path(raw).expanduser()
            if not p.is_absolute():
                p = (_root() / p).resolve()
            return p
        except Exception:
            pass
    return logs_root() / "agent_tasks"


def _sessions_dir() -> Path:
    return logs_root() / "ops" / "sessions"


def _session_dir(session_id: str) -> Path:
    return _sessions_dir() / str(session_id)


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _run(cmd: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(_root()), capture_output=True, text=True, check=False)


def _git(args: List[str]) -> str:
    p = _run(["git", *args])
    if p.returncode != 0:
        return ""
    return (p.stdout or "").strip()


def _agent_name_fallback() -> str:
    return (
        (os.getenv("LLM_AGENT_NAME") or os.getenv("AGENT_NAME") or "").strip()
        or (os.getenv("USER") or os.getenv("LOGNAME") or "").strip()
        or "unknown"
    )


def _best_effort_relative(path: Path | str) -> str:
    try:
        return str(Path(path).resolve().relative_to(_root()))
    except Exception:
        return str(path)


def _read_board_row(*, agent: str) -> dict[str, Any] | None:
    p = _queue_dir() / "coordination" / "board.json"
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    agents = obj.get("agents") if isinstance(obj, dict) else None
    if not isinstance(agents, dict):
        return None
    row = agents.get(str(agent))
    return row if isinstance(row, dict) else None


def _active_locks(*, agent: str) -> list[dict[str, Any]]:
    p = _run([sys.executable, "scripts/agent_org.py", "locks", "--active", "--json"])
    if p.returncode != 0:
        return []
    try:
        arr = json.loads((p.stdout or "").strip() or "[]")
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    out: list[dict[str, Any]] = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        created_by = str(it.get("created_by") or "").strip()
        if created_by != str(agent):
            continue
        out.append(it)
    return out


def _lock_summary(lock_obj: dict[str, Any]) -> dict[str, Any]:
    scopes = lock_obj.get("scopes") if isinstance(lock_obj.get("scopes"), list) else []
    return {
        "id": str(lock_obj.get("id") or ""),
        "mode": str(lock_obj.get("mode") or ""),
        "created_at": str(lock_obj.get("created_at") or ""),
        "expires_at": str(lock_obj.get("expires_at") or ""),
        "note": str(lock_obj.get("note") or ""),
        "scopes": [str(x) for x in scopes if str(x).strip()][:25],
        "scopes_truncated": max(0, len(scopes) - 25),
    }


def _git_snapshot() -> dict[str, Any]:
    head = _git(["rev-parse", "HEAD"])
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"])
    dirty = _git(["status", "--porcelain=v1", "--untracked-files=no"])
    dirty_n = len([ln for ln in (dirty.splitlines() if dirty else []) if ln.strip()])
    return {"head": head or "-", "branch": branch or "-", "dirty_paths": dirty_n}


@dataclass(frozen=True)
class CheckResult:
    name: str
    rc: int
    stdout_path: str
    stderr_path: str


def _run_check(*, session_dir: Path, name: str, cmd: list[str]) -> CheckResult:
    checks_dir = session_dir / "checks"
    checks_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join([c if c.isalnum() or c in "._-" else "_" for c in str(name)])[:80]
    out_p = checks_dir / f"{safe}.stdout.txt"
    err_p = checks_dir / f"{safe}.stderr.txt"

    p = _run(cmd)
    out_p.write_text(p.stdout or "", encoding="utf-8", errors="replace")
    err_p.write_text(p.stderr or "", encoding="utf-8", errors="replace")
    return CheckResult(
        name=str(name),
        rc=int(p.returncode),
        stdout_path=_best_effort_relative(out_p),
        stderr_path=_best_effort_relative(err_p),
    )


def _pick_latest_open_session(*, agent: str) -> str | None:
    root = _sessions_dir()
    if not root.exists():
        return None
    candidates = sorted([p for p in root.iterdir() if p.is_dir() and p.name.startswith("session__")], key=lambda p: p.name, reverse=True)
    for d in candidates:
        start_p = d / "start.json"
        end_p = d / "end.json"
        if not start_p.exists() or end_p.exists():
            continue
        try:
            start = _read_json(start_p)
        except Exception:
            continue
        if str(start.get("agent") or "").strip() != str(agent):
            continue
        return d.name
    return None


def cmd_start(args: argparse.Namespace) -> int:
    agent = str(args.agent or "").strip() or _agent_name_fallback()
    now = _now_iso_utc()
    sid = f"session__{now.replace(':', '').replace('-', '').replace('.', '')}__{secrets.token_hex(4)}"
    sdir = _session_dir(sid)

    locks = [_lock_summary(x) for x in _active_locks(agent=agent)]
    board_row = _read_board_row(agent=agent)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ops_session_start",
        "session_id": sid,
        "agent": agent,
        "started_at": now,
        "note": str(args.note or "").strip(),
        "git": _git_snapshot(),
        "queue_dir": _best_effort_relative(_queue_dir()),
        "board_row": board_row or {},
        "locks": {"count": len(locks), "items": locks},
    }
    _write_json(sdir / "start.json", payload)
    print(sid)
    print(f"[ops_session] start: { _best_effort_relative(sdir / 'start.json') }")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    agent = str(args.agent or "").strip() or _agent_name_fallback()
    root = _sessions_dir()
    if not root.exists():
        print("(no sessions yet)")
        return 0

    rows: list[tuple[str, str, str]] = []
    verbose = bool(getattr(args, "verbose", False))
    all_agents = bool(getattr(args, "all_agents", False))
    for d in sorted([p for p in root.iterdir() if p.is_dir() and p.name.startswith("session__")], key=lambda p: p.name, reverse=True)[
        : int(args.limit)
    ]:
        start_p = d / "start.json"
        end_p = d / "end.json"
        if not start_p.exists():
            continue
        try:
            start = _read_json(start_p)
        except Exception:
            continue
        start_agent = str(start.get("agent") or "").strip()
        if not all_agents and start_agent != str(agent):
            continue
        ended_at = "-"
        if end_p.exists():
            try:
                end = _read_json(end_p)
                ended_at = str(end.get("ended_at") or "-")
            except Exception:
                ended_at = "(end.json unreadable)"
        if bool(args.open_only) and end_p.exists():
            continue
        rows.append((d.name, str(start.get("started_at") or "-"), ended_at))

    if not rows:
        print("(no sessions)")
        return 0
    for sid, started, ended in rows:
        state = "OPEN" if ended == "-" else "DONE"
        if not (verbose or all_agents):
            print(f"{state} {sid} started={started} ended={ended}")
            continue
        start_p = _session_dir(sid) / "start.json"
        start = _read_json(start_p) if start_p.exists() else {}
        start_agent = str(start.get("agent") or "-").strip() or "-"
        note = str(start.get("note") or "").strip()
        note_short = (note[:140] + "...") if len(note) > 140 else note
        extra = [f"agent={start_agent}"]
        if note_short:
            extra.append(f"note={note_short}")
        print(f"{state} {sid} started={started} ended={ended} " + " ".join(extra))
    return 0


def cmd_end(args: argparse.Namespace) -> int:
    agent = str(args.agent or "").strip() or _agent_name_fallback()
    sid = str(args.session_id or "").strip() or _pick_latest_open_session(agent=agent)
    if not sid:
        print(f"[ops_session] no open session found for agent={agent}", file=sys.stderr)
        return 2

    sdir = _session_dir(sid)
    start_p = sdir / "start.json"
    end_p = sdir / "end.json"
    if not start_p.exists():
        print(f"[ops_session] missing start.json: {sdir}", file=sys.stderr)
        return 2
    if end_p.exists():
        print(f"[ops_session] already ended: {sid}", file=sys.stderr)
        return 2

    now = _now_iso_utc()
    try:
        start = _read_json(start_p)
    except Exception:
        start = {}

    locks_start = [x for x in (start.get("locks", {}).get("items", []) if isinstance(start.get("locks"), dict) else []) if isinstance(x, dict)]
    start_lock_ids = {str(x.get("id") or "") for x in locks_start if str(x.get("id") or "").strip()}

    locks_end = [_lock_summary(x) for x in _active_locks(agent=agent)]
    end_lock_ids = {str(x.get("id") or "") for x in locks_end if str(x.get("id") or "").strip()}

    checks: list[CheckResult] = []
    checks.append(
        _run_check(
            session_dir=sdir,
            name=f"ssot_audit_text_{args.ssot_scope}",
            cmd=[
                sys.executable,
                "scripts/ops/ssot_audit.py",
                "--strict",
                "--text-audit",
                "--text-scope",
                str(args.ssot_scope),
            ],
        )
    )
    if bool(args.run_pre_push):
        checks.append(
            _run_check(
                session_dir=sdir,
                name="pre_push_final_check",
                cmd=[sys.executable, "scripts/ops/pre_push_final_check.py"],
            )
        )

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "kind": "ops_session_end",
        "session_id": sid,
        "agent": agent,
        "ended_at": now,
        "git": _git_snapshot(),
        "queue_dir": _best_effort_relative(_queue_dir()),
        "board_row": _read_board_row(agent=agent) or {},
        "locks": {"count": len(locks_end), "items": locks_end},
        "locks_diff": {
            "added": sorted([x for x in (end_lock_ids - start_lock_ids) if x]),
            "removed": sorted([x for x in (start_lock_ids - end_lock_ids) if x]),
        },
        "checks": [
            {"name": c.name, "rc": c.rc, "stdout": c.stdout_path, "stderr": c.stderr_path}
            for c in checks
        ],
    }
    _write_json(end_p, payload)

    any_fail = any(int(c.rc) != 0 for c in checks)
    print(sid)
    print(f"[ops_session] end: { _best_effort_relative(end_p) }")
    if any_fail:
        bad = [c for c in checks if int(c.rc) != 0]
        print("[ops_session] FAILED checks: " + ", ".join([f"{c.name}(rc={c.rc})" for c in bad]))
    if locks_end:
        print(f"[ops_session] WARNING: still holding locks ({len(locks_end)})")
        for it in locks_end[:10]:
            lid = str(it.get("id") or "").strip()
            if not lid:
                continue
            print(f"  - {lid}  (hint: python3 scripts/agent_org.py unlock {lid})")
        if len(locks_end) > 10:
            print(f"  ... ({len(locks_end) - 10} more)")
    return 1 if any_fail else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Start/end bookkeeping for interrupted work.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("start", help="start a new session (writes start.json)")
    sp.add_argument("--agent", default="", help="agent name (default: env LLM_AGENT_NAME)")
    sp.add_argument("--note", default="", help="optional note")
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("end", help="end the latest open session (writes end.json)")
    sp.add_argument("--agent", default="", help="agent name (default: env LLM_AGENT_NAME)")
    sp.add_argument("--session-id", default="", help="explicit session id (default: latest open)")
    sp.add_argument("--ssot-scope", choices=["core", "all"], default="core", help="run ssot_audit --text-scope (default: core)")
    sp.add_argument("--run-pre-push", action="store_true", help="also run scripts/ops/pre_push_final_check.py")
    sp.set_defaults(func=cmd_end)

    sp = sub.add_parser("list", help="list recent sessions for this agent")
    sp.add_argument("--agent", default="", help="agent name (default: env LLM_AGENT_NAME)")
    sp.add_argument("--all-agents", action="store_true", help="list sessions across all agents")
    sp.add_argument("--open-only", action="store_true", help="only show sessions missing end.json")
    sp.add_argument("--verbose", action="store_true", help="include agent + note")
    sp.add_argument("--limit", type=int, default=20, help="max sessions to show (default: 20)")
    sp.set_defaults(func=cmd_list)

    return ap


def main(argv: Optional[List[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    func = getattr(args, "func", None)
    if not func:
        return 2
    return int(func(args))


if __name__ == "__main__":
    raise SystemExit(main())
