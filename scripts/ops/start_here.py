#!/usr/bin/env python3
"""
start_here — stable entrypoint for ops work.

What it does:
1) Starts/refreshes agent heartbeat + updates shared board (agent_bootstrap)
2) Starts an ops_session record (writes workspaces/logs/ops/sessions/.../start.json)

This makes "what was in progress when the session died?" traceable.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from typing import List

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)


def _run(cmd: List[str], *, env: dict | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(REPO_ROOT), text=True, capture_output=False, check=False, env=env)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Start agent heartbeat + board + ops_session start.")
    ap.add_argument("--name", required=True, help="agent name (must be unique in your team)")
    ap.add_argument("--role", default="worker", help="role label (worker/ui/script/thumb/orchestrator)")
    ap.add_argument("--heartbeat-sec", type=float, default=3.0, help="heartbeat interval (seconds)")
    ap.add_argument("--note", default="", help="optional note (recorded in agent registry + session log)")

    ap.add_argument("--doing", default="", help="board: what I'm doing now")
    ap.add_argument("--blocked", default="", help="board: what I'm blocked on")
    ap.add_argument("--next", default="", help="board: what I'll do next")
    ap.add_argument("--tags", default="", help="board: comma-separated tags")
    ap.add_argument("--no-heartbeat", action="store_true", help="do not start heartbeat (board-only)")
    return ap


def main(argv: List[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    bootstrap_cmd = [
        sys.executable,
        "scripts/ops/agent_bootstrap.py",
        "--name",
        str(args.name),
        "--role",
        str(args.role),
        "--heartbeat-sec",
        str(args.heartbeat_sec),
    ]
    if args.note:
        bootstrap_cmd += ["--note", str(args.note)]
    if args.doing:
        bootstrap_cmd += ["--doing", str(args.doing)]
    if args.blocked:
        bootstrap_cmd += ["--blocked", str(args.blocked)]
    if args.next:
        bootstrap_cmd += ["--next", str(args.next)]
    if args.tags:
        bootstrap_cmd += ["--tags", str(args.tags)]
    if args.no_heartbeat:
        bootstrap_cmd += ["--no-heartbeat"]

    p = _run(bootstrap_cmd)
    if p.returncode != 0:
        return int(p.returncode)

    env = dict(os.environ)
    env["LLM_AGENT_NAME"] = str(args.name)
    session_cmd = [
        sys.executable,
        "scripts/ops/ops_session.py",
        "start",
        "--agent",
        str(args.name),
    ]
    if args.note:
        session_cmd += ["--note", str(args.note)]
    return int(_run(session_cmd, env=env).returncode)


if __name__ == "__main__":
    raise SystemExit(main())

