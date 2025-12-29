#!/usr/bin/env python3
"""
agent_bootstrap — convenience wrapper for stable multi-agent collaboration.

What it does (safe-by-default):
  - Starts/refreshes an agent heartbeat (agent registry).
  - Updates Shared Board status for the same agent name.

It does NOT take locks automatically (locks must be scoped to what you touch).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import List

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)


def _run(cmd: List[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, check=False)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Start agent heartbeat + update board status.")
    ap.add_argument("--name", required=True, help="agent name (must be unique in your team)")
    ap.add_argument("--role", default="worker", help="role label (worker/ui/script/thumb/orchestrator)")
    ap.add_argument("--heartbeat-sec", type=float, default=3.0, help="heartbeat interval (seconds)")
    ap.add_argument("--note", default="", help="optional note for agent registry")

    ap.add_argument("--doing", default="", help="board: what I'm doing now")
    ap.add_argument("--blocked", default="", help="board: what I'm blocked on")
    ap.add_argument("--next", default="", help="board: what I'll do next")
    ap.add_argument("--tags", default="", help="board: comma-separated tags")

    ap.add_argument("--no-heartbeat", action="store_true", help="do not start heartbeat (board-only)")
    return ap


def main(argv: List[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    # 1) Agent heartbeat (registry)
    if not args.no_heartbeat:
        cmd = [
            sys.executable,
            "scripts/agent_org.py",
            "agents",
            "start",
            "--name",
            str(args.name),
            "--role",
            str(args.role),
            "--heartbeat-sec",
            str(args.heartbeat_sec),
        ]
        if args.note:
            cmd += ["--note", str(args.note)]
        p = _run(cmd)
        if p.returncode != 0:
            print(p.stderr or p.stdout, file=sys.stderr)
            return 2
        try:
            j = json.loads((p.stdout or "").strip() or "{}")
            print(f"[ok] heartbeat started: agent_id={j.get('agent_id')} pid={j.get('pid')}")
        except Exception:
            print("[ok] heartbeat started")

    # 2) Board status
    board_cmd = [
        sys.executable,
        "scripts/agent_org.py",
        "--agent-name",
        str(args.name),
        "board",
        "set",
    ]
    if args.doing:
        board_cmd += ["--doing", str(args.doing)]
    if args.blocked:
        board_cmd += ["--blocked", str(args.blocked)]
    if args.next:
        board_cmd += ["--next", str(args.next)]
    if args.tags:
        board_cmd += ["--tags", str(args.tags)]

    if any(x in board_cmd for x in ("--doing", "--blocked", "--next", "--tags")):
        p = _run(board_cmd)
        if p.returncode != 0:
            print(p.stderr or p.stdout, file=sys.stderr)
            return 2
        print("[ok] board status updated")
    else:
        print("[skip] board status unchanged (no --doing/--blocked/--next/--tags)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

