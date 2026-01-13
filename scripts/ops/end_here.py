#!/usr/bin/env python3
"""
end_here — stable exitpoint for ops work.

What it does:
1) Ends the latest open ops_session (writes end.json)
2) Runs standard checks (ssot_audit text-audit core by default)

If your session died before this ran, you will have a start.json without end.json.
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
    ap = argparse.ArgumentParser(description="End ops_session + run standard checks.")
    ap.add_argument("--name", required=True, help="agent name (must match start_here)")
    ap.add_argument("--session-id", default="", help="explicit session id (default: latest open)")
    ap.add_argument("--ssot-scope", choices=["core", "all"], default="core", help="run ssot_audit --text-scope (default: core)")
    ap.add_argument("--run-pre-push", action="store_true", help="also run scripts/ops/pre_push_final_check.py")
    return ap


def main(argv: List[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)

    env = dict(os.environ)
    env["LLM_AGENT_NAME"] = str(args.name)

    cmd = [
        sys.executable,
        "scripts/ops/ops_session.py",
        "end",
        "--agent",
        str(args.name),
        "--ssot-scope",
        str(args.ssot_scope),
    ]
    if args.session_id:
        cmd += ["--session-id", str(args.session_id)]
    if args.run_pre_push:
        cmd += ["--run-pre-push"]
    return int(_run(cmd, env=env).returncode)


if __name__ == "__main__":
    raise SystemExit(main())

