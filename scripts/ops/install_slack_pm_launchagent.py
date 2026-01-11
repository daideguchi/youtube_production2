#!/usr/bin/env python3
from __future__ import annotations

"""
install_slack_pm_launchagent.py — macOS launchd で slack_pm_loop を定期実行する（ローカル専用）

目的:
- Slack→PM Inbox 同期を「手動実行」だけに依存せず、30分ポーリング等で回す。
- LLMは使わない（決定論）。台本/モデル選定には一切触れない。

重要（安全）:
- Slackの channel/thread/user 等のIDは **gitに保存しない**。
  ここで作る plist は ~/Library/LaunchAgents/ に生成（ローカルのみ）。
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
    return f"com.dd.{repo}.slack_pm_loop"


def _launchagents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _slack_pm_loop_path() -> Path:
    return PROJECT_ROOT / "scripts" / "ops" / "slack_pm_loop.py"


def _log_dir() -> Path:
    return PROJECT_ROOT / "workspaces" / "logs" / "ops"


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _program_args(args: argparse.Namespace) -> List[str]:
    cmd: List[str] = [
        str(args.python or sys.executable),
        str(_slack_pm_loop_path()),
        "run",
        "--channel",
        str(args.channel),
        "--thread-ts",
        str(args.thread_ts),
    ]
    if args.dd_user:
        cmd += ["--dd-user", str(args.dd_user)]
    if bool(args.post_digest):
        cmd += ["--post-digest", "--digest-max", str(int(args.digest_max))]
    if bool(args.process):
        cmd += ["--process"]
        if bool(args.include_command):
            cmd += ["--include-command"]
    if bool(args.errors):
        cmd += [
            "--errors",
            "--errors-limit",
            str(int(args.errors_limit)),
            "--errors-grep",
            str(args.errors_grep),
        ]
    if bool(args.git_push_if_clean):
        cmd += ["--git-push-if-clean"]
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
    ap = argparse.ArgumentParser(description="Install a LaunchAgent that periodically runs slack_pm_loop (local only).")
    ap.add_argument("--channel", required=True, help="Slack channel id/name (stored in local plist, NOT git)")
    ap.add_argument("--thread-ts", required=True, help="Slack thread ts (stored in local plist, NOT git)")
    ap.add_argument("--dd-user", default="", help="dd Slack user id filter for thread messages (optional)")
    ap.add_argument("--interval-sec", type=int, default=1800, help="Polling interval seconds (default: 1800)")
    ap.add_argument("--label", default=_default_label(), help="LaunchAgent label (default: repo-based)")
    ap.add_argument("--python", default=sys.executable, help="Python executable to use in LaunchAgent")

    ap.add_argument("--post-digest", action="store_true", help="Reply digest of NEW inbox items to the thread")
    ap.add_argument("--digest-max", type=int, default=8)
    ap.add_argument("--errors", action="store_true", help="Also capture error-like channel history")
    ap.add_argument("--errors-grep", default=r"(error|failed|traceback|exception|LLM Smoke|smoke)")
    ap.add_argument("--errors-limit", type=int, default=200)
    ap.add_argument("--process", action="store_true", help="Also post PID snapshot to the thread")
    ap.add_argument("--include-command", action="store_true", help="Include redacted command line in PID snapshot")
    ap.add_argument("--git-push-if-clean", action="store_true", help="Auto git add/commit/push ONLY when repo is clean")

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

