#!/usr/bin/env python3
from __future__ import annotations

"""
slack_pm_loop.py — PM作業の「Slack取りこぼし防止」ループを1コマンド化する

目的:
- Slackスレの返信を取り込み → SSOT Inboxへ要約保存 →（任意で）要点をSlackへ返す
- 併せて、いま回っているプロセス（PID）状況もSlackへ投げられるようにする

重要:
- Slack ID は git に保存しない（`slack_inbox_sync.py` が hash key 化してSSOTへ保存する）
- secrets を Slack に出さない（各ツール側で redact 済み）
"""

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional

from _bootstrap import bootstrap

PROJECT_ROOT = Path(bootstrap(load_env=True))


def _run(cmd: list[str], *, dry_run: bool) -> int:
    if dry_run:
        print("[dry-run]", " ".join(cmd))
        return 0
    p = subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=False)
    return int(p.returncode)


def _slack_inbox_sync_path() -> Path:
    return PROJECT_ROOT / "scripts" / "ops" / "slack_inbox_sync.py"


def _process_report_path() -> Path:
    return PROJECT_ROOT / "scripts" / "ops" / "process_report.py"


def cmd_run(args: argparse.Namespace) -> int:
    channel = str(args.channel or "").strip()
    thread_ts = str(args.thread_ts or "").strip()
    dd_user = str(args.dd_user or "").strip()

    if not channel:
        raise SystemExit("missing --channel (Slack channel id/name)")
    if not thread_ts:
        raise SystemExit("missing --thread-ts (Slack thread ts)")

    dry_run = bool(args.dry_run)

    inbox_cmd: list[str] = [
        sys.executable,
        str(_slack_inbox_sync_path()),
        "sync",
        "--channel",
        channel,
        "--thread-ts",
        thread_ts,
        "--write-ssot",
    ]
    if dd_user:
        inbox_cmd += ["--dd-user", dd_user]
    if bool(args.errors):
        grep = str(args.errors_grep or "").strip()
        if not grep:
            raise SystemExit("missing --errors-grep (cannot run --errors with empty grep)")
        inbox_cmd += [
            "--include-history",
            "--history-limit",
            str(int(args.errors_limit)),
            "--history-grep",
            grep,
        ]
    if bool(args.post_digest):
        inbox_cmd += ["--post-digest", "--digest-max", str(int(args.digest_max))]
    if bool(args.include_nonactionable):
        inbox_cmd += ["--include-nonactionable"]

    rc = _run(inbox_cmd, dry_run=dry_run)
    if rc != 0:
        return rc

    if bool(args.process):
        proc_cmd: list[str] = [
            sys.executable,
            str(_process_report_path()),
            "--slack",
            "--channel",
            channel,
            "--thread-ts",
            thread_ts,
        ]
        for raw in args.pid or []:
            proc_cmd += ["--pid", str(raw)]
        if bool(args.auto_process) and not (args.pid or []):
            proc_cmd += ["--auto"]
        if bool(args.include_command):
            proc_cmd += ["--include-command"]

        rc = _run(proc_cmd, dry_run=dry_run)
        if rc != 0:
            return rc

    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="One-command PM loop for Slack (Inbox sync + optional process snapshot).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="Run PM loop: sync inbox (+ optional digest reply) and optionally post process snapshot.")
    sp.add_argument("--channel", default="", help="Slack channel id/name")
    sp.add_argument("--thread-ts", default="", help="Slack thread ts (reply target)")
    sp.add_argument("--dd-user", default="", help="dd Slack user id (optional filter)")
    sp.add_argument("--post-digest", action="store_true", help="Reply a short digest of NEW inbox items into the thread")
    sp.add_argument("--digest-max", type=int, default=8, help="Max digest items to include (default: 8)")
    sp.add_argument("--errors", action="store_true", help="Also capture error-like channel history into SSOT inbox (grep)")
    sp.add_argument(
        "--errors-grep",
        default=r"(error|failed|traceback|exception|LLM Smoke|smoke)",
        help="Regex for --errors history-grep (default: error/failed/traceback/exception/LLM Smoke/smoke)",
    )
    sp.add_argument("--errors-limit", type=int, default=200, help="History limit for --errors (default: 200)")
    sp.add_argument("--include-nonactionable", action="store_true", help="Also include ack/thanks/note in SSOT inbox")
    sp.add_argument("--process", action="store_true", help="Also post process/PID snapshot to the thread")
    sp.add_argument("--pid", action="append", default=[], help="PID to include (repeatable)")
    sp.add_argument("--auto-process", action="store_true", help="Auto-detect repo-related processes when --process (default)")
    sp.add_argument("--include-command", action="store_true", help="Include redacted command line in process snapshot")
    sp.add_argument("--dry-run", action="store_true", help="Print commands without executing")
    sp.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    if getattr(args, "cmd", "") == "run" and not args.auto_process:
        # default to auto process selection when enabled.
        args.auto_process = True
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
