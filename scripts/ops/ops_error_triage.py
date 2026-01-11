#!/usr/bin/env python3
from __future__ import annotations

"""
ops_error_triage.py — Slackの「[ops] FAILED ... episode=CHxx-NNN」洪水を、episode別に“読める形”へ要約する（LLM不使用）

目的:
- Slackに流れてくる ops 失敗通知（bot投稿）を、episode別に集約して停止原因を推定する。
- root cause は LLM を使わず、ローカルの `workspaces/scripts/.../status.json` を参照して決定論で出す。

安全:
- secrets を出さない（Slack投稿も含む）。
- kill/再実行などの“破壊的操作”は行わない（提案はテキストのみ）。
"""

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

PROJECT_ROOT = Path(bootstrap(load_env=True))
DEFAULT_INBOX_MD = Path("ssot/history/HISTORY_slack_pm_inbox.md")


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_INBOX_LINE_RE = re.compile(
    r"^- \[[ xX]\]\s+(?P<ts>\d{4}-\d{2}-\d{2}T[0-9:.]+Z)\b.*?\bplain\s+\|\s+(?P<plain>.+)$"
)
_OPS_FAILED_RE = re.compile(r"\[ops\]\s+FAILED\b.*?\bepisode=(?P<ep>CH\d{2}-\d{3})\b", flags=re.IGNORECASE)
_CMD_RE = re.compile(r"\bcmd=(?P<cmd>[A-Za-z0-9_-]+)\b")
_OP_RE = re.compile(r"\bop=(?P<op>[A-Za-z0-9_-]+)\b")


@dataclass(frozen=True)
class OpsFailEvent:
    ts: str
    episode: str
    cmd: str
    op: str
    plain: str


def _as_dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _as_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _stage_status(stages: dict[str, Any], name: str) -> str:
    ent = stages.get(name)
    if not isinstance(ent, dict):
        return "missing"
    return str(ent.get("status") or "unknown").strip() or "unknown"


def _read_inbox_events(path: Path, *, max_events: int) -> list[OpsFailEvent]:
    if not path.exists():
        raise SystemExit(f"inbox not found: {path.as_posix()}")

    in_box = False
    events: list[OpsFailEvent] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        ln = str(raw or "").rstrip("\n")
        if "<!-- inbox:start -->" in ln:
            in_box = True
            continue
        if "<!-- inbox:end -->" in ln:
            break
        if not in_box:
            continue

        m = _INBOX_LINE_RE.match(ln)
        if not m:
            continue
        ts = str(m.group("ts") or "").strip()
        plain = str(m.group("plain") or "").strip()
        if not plain:
            continue
        m_fail = _OPS_FAILED_RE.search(plain)
        if not m_fail:
            continue
        ep = str(m_fail.group("ep") or "").strip().upper()

        m_cmd = _CMD_RE.search(plain)
        m_op = _OP_RE.search(plain)
        cmd = str(m_cmd.group("cmd") if m_cmd else "").strip()
        op = str(m_op.group("op") if m_op else "").strip()
        events.append(OpsFailEvent(ts=ts, episode=ep, cmd=cmd, op=op, plain=plain))
        if len(events) >= int(max_events):
            break
    return events


def _status_json_path_for_episode(episode: str) -> Path:
    try:
        from factory_common.paths import status_path  # noqa: WPS433 (bootstrap already ran)

        ch, vid = episode.split("-", 1)
        return status_path(ch, vid)
    except Exception:
        ch, vid = episode.split("-", 1)
        return PROJECT_ROOT / "workspaces" / "scripts" / ch / vid / "status.json"


def _summarize_episode(episode: str) -> tuple[list[str], list[str]]:
    """
    Returns: (summary_lines, detail_lines)
    """
    if "-" not in episode:
        return ([f"- {episode}: invalid episode format"], [])

    p = _status_json_path_for_episode(episode)
    if not p.exists():
        return ([f"- {episode}: status.json not found"], [f"  - status_json: {p.as_posix()}"])

    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return ([f"- {episode}: status.json parse error ({type(exc).__name__})"], [f"  - status_json: {p.as_posix()}"])

    stages = _as_dict(obj.get("stages"))
    sv = _as_dict(stages.get("script_validation"))
    sv_status = str(sv.get("status") or "").strip() or "unknown"
    details = _as_dict(sv.get("details"))
    llm_qg = _as_dict(details.get("llm_quality_gate"))
    qg_verdict = str(llm_qg.get("verdict") or "").strip()
    warning_codes = _as_list(details.get("warning_codes"))
    hard_codes = _as_list(details.get("hard_issue_codes"))

    stage_line = f"script_validation={sv_status}"
    if qg_verdict:
        stage_line += f" (llm_quality_gate={qg_verdict})"

    extras: list[str] = []
    if warning_codes:
        extras.append(f"warnings={len(warning_codes)}")
    if hard_codes:
        extras.append(f"hard_issues={len(hard_codes)}")
    extra_txt = f" ({', '.join(extras)})" if extras else ""

    summary = [f"- {episode}: {stage_line}{extra_txt}"]

    detail: list[str] = []
    detail.append(f"  - status_json: {p.as_posix()}")

    key_stages = [
        "topic_research",
        "script_outline",
        "script_master_plan",
        "chapter_brief",
        "script_draft",
        "script_review",
        "script_validation",
        "audio_synthesis",
    ]
    detail.append("  - stages:")
    for name in key_stages:
        detail.append(f"    - {name}: {_stage_status(stages, name)}")

    if warning_codes:
        uniq = sorted({str(x) for x in warning_codes if str(x).strip()})
        if uniq:
            detail.append(f"  - warning_codes: {', '.join(uniq)}")
    if hard_codes:
        uniq = sorted({str(x) for x in hard_codes if str(x).strip()})
        if uniq:
            detail.append(f"  - hard_issue_codes: {', '.join(uniq)}")

    if sv_status == "pending" and qg_verdict == "fail":
        assembled = p.parent / "content" / "assembled_human.md"
        if assembled.exists():
            detail.append(f"  - assembled_human: {assembled.as_posix()}")
        detail.append("  - next: script_validation が fail のため、入力修正 or runbookの指示に沿ったやり直しが必要")
        detail.append("  - note: この状態で resume を連打しても改善しにくい（同じ入力なら結果が再現しやすい）")

    return summary, detail


def _render_report(events: list[OpsFailEvent], *, inbox_md: Path, top: int) -> str:
    lines: list[str] = []
    lines.append("*【ops失敗トリアージ】*")
    lines.append(f"_generated_at={_now_iso_utc()}_")
    lines.append(f"_source={inbox_md.as_posix()} (ops failed only)_")
    lines.append("")

    if not events:
        lines.append("(no [ops] FAILED events in inbox)")
        return "\n".join(lines).strip()

    by_ep: dict[str, list[OpsFailEvent]] = {}
    for ev in events:
        by_ep.setdefault(ev.episode, []).append(ev)

    episodes = sorted(by_ep.keys(), key=lambda ep: (len(by_ep[ep]), max(x.ts for x in by_ep[ep])), reverse=True)
    episodes = episodes[: max(1, int(top))]

    lines.append("■ 集約（episode別）")
    for ep in episodes:
        evs = sorted(by_ep[ep], key=lambda x: x.ts, reverse=True)
        last = evs[0]
        cmdop = " ".join([x for x in [f"cmd={last.cmd}" if last.cmd else "", f"op={last.op}" if last.op else ""] if x])
        cmdop_txt = f" ({cmdop})" if cmdop else ""
        lines.append(f"- {ep}: count={len(evs)} last={last.ts}{cmdop_txt}")
    lines.append("")

    lines.append("■ 状態（status.json から推定）")
    for ep in episodes:
        summ, detail = _summarize_episode(ep)
        lines.extend(summ)
        lines.extend(detail)
        lines.append("")

    return "\n".join(lines).strip()


def _slack_notify(text: str, *, channel: str, thread_ts: str) -> str:
    api = PROJECT_ROOT / "scripts" / "ops" / "slack_notify.py"
    cmd = [sys.executable, str(api), "--text", text, "--print-ts"]
    if channel:
        cmd += ["--channel", channel]
    if thread_ts:
        cmd += ["--thread-ts", thread_ts]
    p = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True, check=False)
    if p.returncode != 0:
        raise SystemExit((p.stderr or p.stdout or "").strip() or f"slack_notify failed: exit={p.returncode}")
    return (p.stdout or "").strip()


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Triage Slack [ops] FAILED floods by episode using local status.json (no LLM).")
    ap.add_argument("--inbox-md", default=str(DEFAULT_INBOX_MD.as_posix()), help="Path to HISTORY_slack_pm_inbox.md")
    ap.add_argument("--max-events", type=int, default=200, help="Max inbox events to scan (default: 200)")
    ap.add_argument("--top", type=int, default=5, help="Top N episodes to show (default: 5)")
    ap.add_argument("--episode", action="append", default=[], help="Triage this episode directly (repeatable). e.g. CH06-035")
    ap.add_argument("--slack", action="store_true", help="Post the triage report to Slack")
    ap.add_argument("--channel", default="", help="Slack channel id/name (required with --slack)")
    ap.add_argument("--thread-ts", default="", help="Slack thread ts (required with --slack)")
    args = ap.parse_args(argv)

    direct_eps = [str(x or "").strip().upper() for x in (args.episode or []) if str(x or "").strip()]
    direct_eps = sorted(set(direct_eps))

    if direct_eps:
        lines: list[str] = []
        lines.append("*【ops失敗トリアージ】*")
        lines.append(f"_generated_at={_now_iso_utc()}_")
        lines.append("_source=direct episodes_")
        lines.append("")
        lines.append("■ 状態（status.json から推定）")
        for ep in direct_eps:
            summ, detail = _summarize_episode(ep)
            lines.extend(summ)
            lines.extend(detail)
            lines.append("")
        text = "\n".join(lines).strip()
    else:
        inbox_md = Path(str(args.inbox_md or "").strip())
        events = _read_inbox_events(inbox_md, max_events=int(args.max_events))
        text = _render_report(events, inbox_md=inbox_md, top=int(args.top))
    if len(text) > 3800:
        text = text[:3790].rstrip() + "…\n_(truncated)_"

    if args.slack:
        channel = str(args.channel or "").strip()
        thread_ts = str(args.thread_ts or "").strip()
        if not channel:
            raise SystemExit("missing --channel for --slack")
        if not thread_ts:
            raise SystemExit("missing --thread-ts for --slack")
        ts = _slack_notify(text, channel=channel, thread_ts=thread_ts)
        print(ts)
        return 0

    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
