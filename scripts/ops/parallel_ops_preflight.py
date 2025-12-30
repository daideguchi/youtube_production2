#!/usr/bin/env python3
"""
parallel_ops_preflight — guardrails checklist before running many agents in parallel.

This is intentionally lightweight:
  - It does NOT change pipeline logic.
  - It prints a human-readable summary.
  - It optionally writes a JSON report under workspaces/logs/agent_tasks/coordination/preflight/.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: List[str]) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True)
    return int(p.returncode), (p.stdout or "").strip(), (p.stderr or "").strip()


def _run_json(cmd: List[str]) -> Dict[str, Any]:
    rc, out, err = _run(cmd)
    if rc != 0:
        raise RuntimeError(f"command failed (rc={rc}): {' '.join(cmd)}\n{err or out}")
    try:
        return json.loads(out)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"invalid JSON output: {' '.join(cmd)}\n{exc}\n{out[:2000]}") from exc


def _coord_root() -> Path:
    from factory_common.paths import logs_root

    return logs_root() / "agent_tasks" / "coordination"


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _agent_records(coord_root: Path) -> List[Dict[str, Any]]:
    agents_dir = coord_root / "agents"
    if not agents_dir.exists():
        return []
    out: List[Dict[str, Any]] = []
    for fp in sorted(agents_dir.glob("agent__*.json")):
        try:
            out.append(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def _classify_agents(agents: List[Dict[str, Any]], *, stale_sec: int) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)

    def _age_sec(ts: str | None) -> int | None:
        dt = _parse_iso(ts)
        if not dt:
            return None
        try:
            return int((now - dt).total_seconds())
        except Exception:
            return None

    active: List[Dict[str, Any]] = []
    stale: List[Dict[str, Any]] = []
    dead: List[Dict[str, Any]] = []
    for a in agents:
        age = _age_sec(str(a.get("last_seen_at") or ""))
        if age is None:
            dead.append(a)
        elif age <= stale_sec:
            active.append(a)
        else:
            stale.append(a)

    def _short(a: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": a.get("id"),
            "name": a.get("name"),
            "role": a.get("role"),
            "assigned_role": a.get("assigned_role"),
            "pid": a.get("pid"),
            "last_seen_at": a.get("last_seen_at"),
            "queue_dir": a.get("queue_dir"),
        }

    return {
        "stale_sec": stale_sec,
        "counts": {"active": len(active), "stale": len(stale), "dead": len(dead)},
        "active": [_short(a) for a in active],
        "stale": [_short(a) for a in stale],
        "dead": [_short(a) for a in dead],
    }


def _git_write_lock_status() -> str:
    rc, out, err = _run([sys.executable, "scripts/ops/git_write_lock.py", "status"])
    if rc != 0:
        return f"unknown (rc={rc}) {err or out}".strip()
    return out.strip() or "unknown"


def _git_guard_path() -> str | None:
    try:
        return shutil.which("git")
    except Exception:
        return None


def _ensure_git_write_lock(errors: List[str]) -> None:
    rc, out, err = _run([sys.executable, "scripts/ops/git_write_lock.py", "lock"])
    if rc != 0:
        errors.append(f"failed to lock .git: {err or out}".strip())


def _ensure_orchestrator_running(
    *,
    name: str,
    wait: bool,
    errors: List[str],
) -> None:
    cmd = [sys.executable, "scripts/agent_org.py", "orchestrator", "start", "--name", name]
    if wait:
        cmd.append("--wait")
    rc, out, err = _run(cmd)
    if rc != 0:
        errors.append(f"failed to start orchestrator: {err or out}".strip())


def _execpolicy_rollback_probe() -> Dict[str, Any]:
    rules_path = os.path.expanduser("~/.codex/rules/default.rules")
    if not Path(rules_path).exists():
        return {"ok": False, "note": f"rules not found: {rules_path}"}

    rc, out, err = _run(
        [
            "codex",
            "execpolicy",
            "check",
            "--rules",
            rules_path,
            "git",
            "restore",
            "--",
            "README.md",
        ]
    )
    if rc != 0:
        return {"ok": False, "note": f"execpolicy check failed (rc={rc}): {err or out}".strip()}

    try:
        j = json.loads(out)
    except Exception:
        j = {"raw": out}
    decision = j.get("decision")
    return {"ok": True, "decision": decision, "raw": j}


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Preflight checklist for parallel multi-agent operation.")
    ap.add_argument("--stale-sec", type=int, default=30, help="agent is active if last_seen_at within N seconds")
    ap.add_argument("--ensure-orchestrator", action="store_true", help="start orchestrator if not running")
    ap.add_argument("--orchestrator-name", default="dd-orch", help="orchestrator name for start")
    ap.add_argument("--wait", action="store_true", help="wait for orchestrator lease instead of failing fast")
    ap.add_argument("--ensure-git-lock", action="store_true", help="lock .git if unlocked")
    ap.add_argument("--no-report", action="store_true", help="do not write JSON report to workspaces/logs")
    ap.add_argument("--json", action="store_true", help="print full JSON report to stdout")
    return ap


def main(argv: List[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    errors: List[str] = []
    warnings: List[str] = []

    coord_root = _coord_root()

    # Agent identity is optional, but strongly recommended for collaboration.
    agent_name = os.getenv("LLM_AGENT_NAME") or ""
    if not agent_name.strip():
        warnings.append("LLM_AGENT_NAME is not set (agent_org write commands require it; also recommended for attribution)")

    # Orchestrator status (do not assume running).
    orch_status: Dict[str, Any] | None = None
    try:
        orch_status = _run_json([sys.executable, "scripts/agent_org.py", "orchestrator", "status"])
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"cannot read orchestrator status: {exc}")

    if args.ensure_orchestrator:
        if orch_status and bool(orch_status.get("lock_held")) and bool(orch_status.get("pid_alive")):
            pass
        else:
            _ensure_orchestrator_running(name=str(args.orchestrator_name), wait=bool(args.wait), errors=errors)
            try:
                orch_status = _run_json([sys.executable, "scripts/agent_org.py", "orchestrator", "status"])
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"cannot read orchestrator status after start: {exc}")

    # Git rollback guard (physical lock).
    git_lock = _git_write_lock_status()
    if args.ensure_git_lock and git_lock.startswith("unlocked"):
        _ensure_git_write_lock(errors)
        git_lock = _git_write_lock_status()

    # Git rollback guard (Codex Git Guard wrapper) — best-effort check.
    git_path = _git_guard_path()
    if os.getenv("CODEX_MANAGED_BY_NPM"):
        expected = str(Path.home() / ".codex" / "bin" / "git")
        if git_path and git_path != expected:
            warnings.append(f"git guard not first in PATH: got={git_path} expected={expected}")
        if not git_path:
            warnings.append("git guard probe failed: cannot resolve git in PATH")

    # Coordination locks hygiene (no-expiry locks older than 6h are suspicious).
    no_expiry_old: List[Dict[str, Any]] = []
    try:
        no_expiry_old = _run_json(
            [sys.executable, "scripts/agent_org.py", "locks-audit", "--older-than-hours", "6", "--json"]
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"cannot run locks-audit: {exc}")

    if no_expiry_old:
        warnings.append(f"found {len(no_expiry_old)} no-expiry locks older than 6h (run locks-audit)")

    # Shared board usage (visibility): warn when lock owners are not visible on the board.
    board_path = coord_root / "board.json"
    board_agents: Dict[str, Any] = {}
    if board_path.exists():
        try:
            board_obj = json.loads(board_path.read_text(encoding="utf-8"))
            if isinstance(board_obj, dict) and isinstance(board_obj.get("agents"), dict):
                board_agents = board_obj.get("agents") or {}
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"cannot parse shared board: {exc}")

    active_locks: List[Dict[str, Any]] = []
    try:
        locks_any = _run_json([sys.executable, "scripts/agent_org.py", "locks", "--json"])
        if isinstance(locks_any, list):
            active_locks = [x for x in locks_any if isinstance(x, dict)]
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"cannot list active locks: {exc}")

    lock_owners = sorted({str(l.get('created_by') or '').strip() for l in active_locks if str(l.get('created_by') or '').strip()})
    if lock_owners and not board_agents:
        warnings.append(
            f"found {len(lock_owners)} active lock owner(s), but board has no status entries "
            "(run scripts/ops/agent_bootstrap.py --name <NAME> ...)"
        )
    elif lock_owners:
        missing = [a for a in lock_owners if a not in board_agents]
        if missing:
            preview = ", ".join(missing[:6]) + (" ..." if len(missing) > 6 else "")
            warnings.append(f"{len(missing)} lock owner(s) have no board status entry: {preview}")

    # Agent heartbeat overview (active/stale/dead).
    agents = _agent_records(coord_root)
    agents_summary = _classify_agents(agents, stale_sec=int(args.stale_sec))

    # Execpolicy probe (best-effort; environment-specific).
    execpolicy_probe = _execpolicy_rollback_probe()
    if execpolicy_probe.get("ok") and execpolicy_probe.get("decision") != "forbidden":
        warnings.append(f"execpolicy rollback probe decision != forbidden: {execpolicy_probe.get('decision')}")
    if not execpolicy_probe.get("ok"):
        warnings.append(str(execpolicy_probe.get("note") or "execpolicy probe unavailable"))

    report: Dict[str, Any] = {
        "schema_version": 1,
        "kind": "parallel_ops_preflight",
        "created_at": _now_iso_utc(),
        "repo_root": str(REPO_ROOT),
        "agent": {"name": agent_name or None},
        "git_in_path": git_path,
        "git_write_lock": git_lock,
        "orchestrator": orch_status,
        "board": {"path": str(board_path), "exists": bool(board_path.exists()), "agent_status_count": len(board_agents)},
        "locks": {"active_count": len(active_locks), "owners": lock_owners},
        "locks_audit": {"no_expiry_older_than_6h": no_expiry_old},
        "agents": agents_summary,
        "execpolicy": execpolicy_probe,
        "errors": errors,
        "warnings": warnings,
    }

    # Print concise summary.
    print("[parallel preflight]")
    print(f"- git_write_lock: {git_lock}")
    if orch_status:
        print(f"- orchestrator: lock_held={orch_status.get('lock_held')} pid_alive={orch_status.get('pid_alive')}")
    print(f"- agents: {agents_summary['counts']}")
    if errors:
        print(f"- errors: {len(errors)} (see --json/report)")
    if warnings:
        print(f"- warnings: {len(warnings)} (see --json/report)")

    # Write report.
    if not args.no_report:
        out_dir = coord_root / "preflight"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"preflight__{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"- report: {out_path}")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
