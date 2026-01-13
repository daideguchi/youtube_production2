#!/usr/bin/env python3
from __future__ import annotations

"""
install_slack_ops_launchagent.py — macOS launchd で slack_ops_loop を定期実行する（ローカル専用）

目的:
- Slackスレを「ローカル操作窓口」にして、スマホから `./ops ...` を叩けるようにする。
- 仕組みはローカル専用（このMacが起動している時だけ動作）。

重要（安全）:
- Slackの channel/thread/user 等のIDは **gitに保存しない**。
  ここで作る plist は ~/Library/LaunchAgents/ に生成（ローカルのみ）。

SSOT:
- ssot/ops/OPS_SLACK_OPS_GATEWAY.md
"""

import argparse
import os
import plistlib
import sys
from pathlib import Path
from typing import Any, Dict, List

from _bootstrap import bootstrap

PROJECT_ROOT = Path(bootstrap(load_env=True))


def _default_label() -> str:
    repo = PROJECT_ROOT.name
    return f"com.dd.{repo}.slack_ops_loop"


def _launchagents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _slack_ops_loop_path() -> Path:
    return PROJECT_ROOT / "scripts" / "ops" / "slack_ops_loop.py"


def _log_dir() -> Path:
    return PROJECT_ROOT / "workspaces" / "logs" / "ops"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _program_args(args: argparse.Namespace) -> List[str]:
    cmd: List[str] = [
        str(args.python or sys.executable),
        str(_slack_ops_loop_path()),
        "run",
        "--channel",
        str(args.channel),
        "--thread-ts",
        str(args.thread_ts),
        "--poll-limit",
        str(int(args.poll_limit)),
        "--max-commands",
        str(int(args.max_commands)),
        "--max-reply-chars",
        str(int(args.max_reply_chars)),
    ]
    if args.dd_user:
        cmd += ["--dd-user", str(args.dd_user)]
    for u in args.allow_user or []:
        u = str(u).strip()
        if u:
            cmd += ["--allow-user", u]
    if bool(args.dry_run):
        cmd += ["--dry-run"]
    return cmd


def _plist_dict(args: argparse.Namespace) -> Dict[str, Any]:
    _ensure_dir(_log_dir())
    label = str(args.label or _default_label()).strip() or _default_label()
    out_base = label.replace("/", "_")
    return {
        "Label": label,
        "ProgramArguments": _program_args(args),
        "RunAtLoad": True,
        "StartInterval": int(args.interval_sec),
        "WorkingDirectory": str(PROJECT_ROOT),
        "StandardOutPath": str((_log_dir() / f"{out_base}.out.log").resolve()),
        "StandardErrorPath": str((_log_dir() / f"{out_base}.err.log").resolve()),
        "EnvironmentVariables": {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
        },
    }


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Install a LaunchAgent that periodically runs slack_ops_loop (local only).")
    ap.add_argument("--channel", required=True, help="Slack channel id/name (stored in local plist, NOT git)")
    ap.add_argument("--thread-ts", required=True, help="Slack thread ts (stored in local plist, NOT git)")
    ap.add_argument("--dd-user", default="", help="Allowed Slack user id (recommended)")
    ap.add_argument("--allow-user", action="append", default=[], help="Additional allowed user ids (repeatable)")
    ap.add_argument("--interval-sec", type=int, default=1800, help="Polling interval seconds (default: 1800 = 30 min)")
    ap.add_argument("--poll-limit", type=int, default=200, help="Max replies fetched per run (default: 200)")
    ap.add_argument("--max-commands", type=int, default=3, help="Max commands executed per run (default: 3)")
    ap.add_argument("--max-reply-chars", type=int, default=2400, help="Max chars included in Slack reply (default: 2400)")
    ap.add_argument("--label", default=_default_label(), help="LaunchAgent label (default: repo-based)")
    ap.add_argument("--python", default=sys.executable, help="Python executable to use in LaunchAgent")
    ap.add_argument("--out", default="", help="Write plist to this path (default: ~/Library/LaunchAgents/<label>.plist)")
    ap.add_argument("--dry-run", action="store_true", help="Print plist path + commands without writing")
    args = ap.parse_args(argv)

    label = str(args.label or _default_label()).strip() or _default_label()
    out = Path(str(args.out).strip()) if str(args.out).strip() else (_launchagents_dir() / f"{label}.plist")

    if sys.platform != "darwin":
        raise SystemExit("This installer is intended for macOS (launchd).")

    _ensure_dir(out.parent)
    payload = plistlib.dumps(_plist_dict(args), fmt=plistlib.FMT_XML, sort_keys=False)

    if args.dry_run:
        print("[dry-run] write:", out)
    else:
        out.write_bytes(payload)
        print("[ok] wrote:", out)

    uid = os.getuid()
    print("")
    print("Load / reload (choose ONE):")
    print(f"- launchctl unload -w {out} 2>/dev/null || true && launchctl load -w {out}")
    print(f"- launchctl bootout gui/{uid} {out} 2>/dev/null || true && launchctl bootstrap gui/{uid} {out}")
    print("")
    print("Check:")
    print(f"- launchctl list | rg -n \"{label}\" || true")
    print("")
    print("Uninstall:")
    print(f"- launchctl bootout gui/{uid} {out} 2>/dev/null || true && rm -f {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
