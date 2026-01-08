#!/usr/bin/env python3
"""
orchestrator_bootstrap — start/refresh the single Orchestrator seat and guardrails.

This script is meant to be run by the human-designated Orchestrator before
spinning up many agents.

What it does:
  - (Optional) Locks `.git` (rollback guard) using scripts/ops/git_write_lock.py
  - Starts orchestrator lease process (background) via scripts/agent_org.py
  - Starts/refreshes orchestrator heartbeat entry + board status
  - Runs parallel preflight and writes a report
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import List

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)


def _run(cmd: List[str]) -> int:
    p = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False)
    return int(p.returncode)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Bootstrap the single Orchestrator seat + guardrails.")
    ap.add_argument("--name", default="dd-orch", help="orchestrator/agent name")
    ap.add_argument("--wait", action="store_true", help="wait for orchestrator lease instead of failing fast")
    ap.add_argument("--heartbeat-sec", type=float, default=3.0, help="agent heartbeat interval (seconds)")
    ap.add_argument("--doing", default="orchestrator: coordinating parallel run", help="board: doing")
    ap.add_argument("--next", default="assign tasks + enforce locks", help="board: next")
    ap.add_argument("--tags", default="coordination", help="board tags")
    ap.add_argument(
        "--ensure-git-lock",
        action="store_true",
        help="Lock `.git` (write-lock). This blocks commit/push until explicitly unlocked.",
    )
    return ap


def main(argv: List[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    # 1) (Optional) Lock .git (best-effort; harmless if already locked)
    if args.ensure_git_lock:
        rc = _run([sys.executable, "scripts/ops/git_write_lock.py", "lock"])
        if rc != 0:
            return rc

    # 2) Start orchestrator lease holder (background)
    orch_cmd = [sys.executable, "scripts/agent_org.py", "orchestrator", "start", "--name", str(args.name)]
    if args.wait:
        orch_cmd.append("--wait")
    rc = _run(orch_cmd)
    if rc != 0:
        return rc

    # 3) Start agent heartbeat + update board status for orchestrator identity
    rc = _run(
        [
            sys.executable,
            "scripts/ops/agent_bootstrap.py",
            "--name",
            str(args.name),
            "--role",
            "orchestrator",
            "--heartbeat-sec",
            str(args.heartbeat_sec),
            "--doing",
            str(args.doing),
            "--next",
            str(args.next),
            "--tags",
            str(args.tags),
        ]
    )
    if rc != 0:
        return rc

    # 4) Preflight report (writes JSON under workspaces/logs)
    preflight_cmd = [
        sys.executable,
        "scripts/ops/parallel_ops_preflight.py",
        "--ensure-orchestrator",
        "--orchestrator-name",
        str(args.name),
    ]
    if args.ensure_git_lock:
        preflight_cmd.insert(3, "--ensure-git-lock")
    rc = _run(
        preflight_cmd
    )
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
