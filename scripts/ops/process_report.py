#!/usr/bin/env python3
from __future__ import annotations

"""
process_report.py — PIDごとの「いつから/何をしているか」を可視化し、Slackへ通知する

目的:
- dd が「いま回っているPID」をSlackで把握できるようにする（開発/運用の見通し改善）。
- agent / orchestrator / codex exec / UI dev server などを分類して、目的が一目でわかる形にする。

安全:
- コマンドラインに token-like 文字列が混ざっていても Slack に漏れないよう自動 redact する。
- LLMは使わない（決定論・ローカル情報のみ）。

使い方（例）:
- 自動検出（このrepo関連を抽出）→Slack投稿:
  python3 scripts/ops/process_report.py --auto --slack --channel C0123... --thread-ts 1234.567
- PIDを明示してSlack投稿:
  python3 scripts/ops/process_report.py --pid 52211 --pid 52239 --slack
"""

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from _bootstrap import bootstrap

PROJECT_ROOT = Path(bootstrap(load_env=True))


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_SUSPECT_SECRET_TOKEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bfw_[A-Za-z0-9]{8,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}\b"),
    re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b"),
]
_ENV_ASSIGNMENT_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
_SUSPECT_SECRET_NAME_HINTS = ("TOKEN", "API_KEY", "SECRET", "PASSWORD", "PRIVATE_KEY", "ACCESS_KEY")


def _redact_text(text: str) -> tuple[str, bool]:
    raw = str(text or "")
    out = raw.replace("\r\n", "\n").replace("\r", "\n")
    redacted = False
    for pat in _SUSPECT_SECRET_TOKEN_PATTERNS:
        if pat.search(out):
            redacted = True
        out = pat.sub("[REDACTED]", out)

    lines: list[str] = []
    for ln in out.splitlines():
        m = _ENV_ASSIGNMENT_RE.match(ln.strip())
        if not m:
            lines.append(ln)
            continue
        name = str(m.group("name") or "").strip()
        val = str(m.group("value") or "").strip()
        upper = name.upper()
        if name and val and any(h in upper for h in _SUSPECT_SECRET_NAME_HINTS):
            redacted = True
            lines.append(f"{name}=[REDACTED]")
        else:
            lines.append(ln)
    return ("\n".join(lines)).strip(), redacted


@dataclass(frozen=True)
class ProcRow:
    pid: int
    since: str
    etime: str
    command: str

    def redacted_command(self) -> tuple[str, bool]:
        return _redact_text(self.command)


def _ps_all() -> list[ProcRow]:
    """
    Cross-platform-ish process snapshot.
    Uses LC_ALL=C so lstart is stable (e.g. "Thu Jan  8 09:22:44 2026").
    """
    env = dict(os.environ)
    env["LC_ALL"] = "C"
    # pid lstart etime command
    cmd = ["ps", "-ax", "-o", "pid=,lstart=,etime=,command="]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, check=False)
    out = (proc.stdout or "").splitlines()
    rows: list[ProcRow] = []
    for ln in out:
        s = ln.strip()
        if not s:
            continue
        parts = s.split()
        if len(parts) < 8:
            continue
        try:
            pid = int(parts[0])
        except Exception:
            continue
        since = " ".join(parts[1:6])
        etime = parts[6]
        command = " ".join(parts[7:])
        rows.append(ProcRow(pid=pid, since=since, etime=etime, command=command))
    return rows


def _classify(row: ProcRow) -> tuple[str, str]:
    """
    Returns (section, summary_label).
    """
    cmd = row.command
    low = cmd.lower()

    if "agent_org.py" in cmd and "orchestrator run" in cmd:
        name = _extract_flag_value(cmd, "--name") or "unknown"
        return ("Orchestrator", f"agent_org orchestrator ({name})")
    if "agent_org.py" in cmd and "agents run" in cmd:
        name = _extract_flag_value(cmd, "--name") or "unknown"
        return ("Agent workers", f"{name}")
    if "uvicorn" in low and "backend.main:app" in cmd:
        return ("UI/Docs", "ui-backend (uvicorn backend.main:app)")
    if "start_manager.py" in cmd and "apps/ui-backend" in cmd:
        return ("UI/Docs", "ui-backend start_manager")
    if "react-scripts start" in low:
        return ("UI/Docs", "ui-frontend dev server (react-scripts)")
    if "python -m http.server" in low or "python3 -m http.server" in low:
        return ("UI/Docs", "docs http.server")
    if low.startswith("tail ") and "ui_hub" in cmd:
        return ("UI/Docs", "ui_hub logs tail")
    if "codex exec" in low:
        return ("Codex exec", "codex exec (non-interactive)")
    return ("Other", "other")


def _extract_flag_value(cmd: str, flag: str) -> str:
    parts = cmd.split()
    for i, p in enumerate(parts):
        if p == flag and i + 1 < len(parts):
            return parts[i + 1]
    return ""


def _auto_match(row: ProcRow) -> bool:
    cmd = row.command
    # Keep the default narrow (signal-only) to avoid noisy system/indexer processes.
    if "agent_org.py" in cmd and "workspaces/logs/agent_tasks" in cmd:
        return True
    if "codex exec" in cmd.lower() and f"-C {PROJECT_ROOT}" in cmd:
        return True
    if "backend.main:app" in cmd:
        return True
    if "start_manager.py" in cmd and "apps/ui-backend" in cmd:
        return True
    if "react-scripts start" in cmd.lower() and "apps/ui-frontend" in cmd:
        return True
    if "http.server" in cmd.lower() and "--directory docs" in cmd:
        return True
    if cmd.lower().startswith("tail ") and "workspaces/logs/ui_hub" in cmd:
        return True
    return False


def _format_report(
    *,
    requested_pids: list[int],
    rows: list[ProcRow],
    include_command: bool,
) -> str:
    by_pid: Dict[int, ProcRow] = {r.pid: r for r in rows}

    lines: list[str] = []
    lines.append(f"*【PID稼働状況】{PROJECT_ROOT.name}*")
    lines.append(f"_generated_at={_now_iso_utc()}_")
    lines.append("")

    if requested_pids:
        lines.append("■ 指定PID")
        for pid in requested_pids:
            r = by_pid.get(pid)
            if not r:
                lines.append(f"- {pid}: not running")
                continue
            section, label = _classify(r)
            cmd_txt, red = r.redacted_command()
            cmd_suffix = f" | cmd={cmd_txt}" if include_command else ""
            red_suffix = " redacted" if red else ""
            lines.append(f"- {pid}: etime={r.etime} since={r.since} [{section}] {label}{red_suffix}{cmd_suffix}")
        lines.append("")

    # Auto summary (grouped).
    groups: Dict[str, list[ProcRow]] = {}
    for r in rows:
        section, _ = _classify(r)
        groups.setdefault(section, []).append(r)

    for section in ["Orchestrator", "Agent workers", "UI/Docs", "Codex exec", "Other"]:
        procs = groups.get(section) or []
        if not procs:
            continue
        lines.append(f"■ {section}")
        # stable-ish ordering: longest-running first (rough, by etime string length + lexicographic)
        procs_sorted = sorted(procs, key=lambda x: (len(x.etime), x.etime), reverse=True)
        for r in procs_sorted[:30]:
            sec, label = _classify(r)
            cmd_txt, red = r.redacted_command()
            cmd_suffix = f" | cmd={cmd_txt}" if include_command else ""
            red_suffix = " redacted" if red else ""
            lines.append(f"- pid={r.pid} etime={r.etime} since={r.since} {label}{red_suffix}{cmd_suffix}")
        if len(procs) > 30:
            lines.append(f"- … +{len(procs) - 30} more")
        lines.append("")

    return "\n".join(lines).strip()


def _slack_notify_path() -> Path:
    return PROJECT_ROOT / "scripts" / "ops" / "slack_notify.py"


def _post_to_slack(*, text: str, channel: str, thread_ts: str) -> str:
    cmd = [sys.executable, str(_slack_notify_path()), "--text", text]
    if channel:
        cmd += ["--channel", channel]
    if thread_ts:
        cmd += ["--thread-ts", thread_ts]
    cmd += ["--print-ts"]
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit((proc.stderr or proc.stdout or "").strip() or f"slack_notify failed: exit={proc.returncode}")
    return (proc.stdout or "").strip()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Process/PID report (and optional Slack notify).")
    ap.add_argument("--pid", action="append", default=[], help="PID to report (repeatable).")
    ap.add_argument("--auto", action="store_true", help="Auto-detect repo-related processes (default when no --pid).")
    ap.add_argument("--grep", default="", help="Regex filter for command line (optional).")
    ap.add_argument("--include-command", action="store_true", help="Include full command line in output (redacted).")
    ap.add_argument("--slack", action="store_true", help="Post the report to Slack via scripts/ops/slack_notify.py")
    ap.add_argument("--channel", default="", help="Slack channel (ID or name; default: env SLACK_CHANNEL)")
    ap.add_argument("--thread-ts", default="", help="Slack thread ts to reply to (optional)")
    args = ap.parse_args(argv)

    requested_pids: list[int] = []
    for raw in args.pid:
        s = str(raw or "").strip()
        if not s:
            continue
        try:
            requested_pids.append(int(s))
        except Exception:
            raise SystemExit(f"invalid --pid: {s}")

    grep = str(args.grep or "").strip()
    grep_re = re.compile(grep, flags=re.IGNORECASE) if grep else None

    rows = _ps_all()
    if grep_re:
        rows = [r for r in rows if grep_re.search(r.command)]

    auto = bool(args.auto) or not requested_pids
    if auto:
        rows = [r for r in rows if _auto_match(r)]

    text = _format_report(requested_pids=requested_pids, rows=rows, include_command=bool(args.include_command))

    if args.slack:
        channel = str(args.channel or os.getenv("SLACK_CHANNEL") or os.getenv("YTM_SLACK_CHANNEL") or "").strip()
        thread_ts = str(args.thread_ts or "").strip()
        ts = _post_to_slack(text=text, channel=channel, thread_ts=thread_ts)
        print(ts)
        return 0

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
