#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from _bootstrap import bootstrap

PROJECT_ROOT = Path(bootstrap())


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _webhook_url(args: argparse.Namespace) -> str:
    raw = str(getattr(args, "webhook_url", "") or "").strip()
    if raw:
        return raw
    return str(os.getenv("YTM_SLACK_WEBHOOK_URL") or os.getenv("SLACK_WEBHOOK_URL") or "").strip()


def _bot_token() -> str:
    return str(os.getenv("SLACK_BOT_TOKEN") or os.getenv("YTM_SLACK_BOT_TOKEN") or "").strip()


def _channel() -> str:
    return str(os.getenv("SLACK_CHANNEL") or os.getenv("YTM_SLACK_CHANNEL") or "").strip()


def _post_webhook(url: str, payload: Dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "factory_commentary/slack_notify",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def _post_chat_post_message(token: str, channel: str, *, text: str) -> None:
    api = "https://slack.com/api/chat.postMessage"
    data = json.dumps({"channel": channel, "text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "factory_commentary/slack_notify",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = (resp.read() or b"").decode("utf-8", errors="ignore")
    try:
        obj = json.loads(body) if body else {}
    except Exception:
        obj = {}
    if isinstance(obj, dict) and obj.get("ok") is False:
        raise RuntimeError(f"slack chat.postMessage failed: {obj.get('error')}")


def _format_duration_ms(ms: Any) -> str:
    try:
        v = int(ms)
    except Exception:
        return "-"
    if v < 0:
        return "-"
    sec = v / 1000.0
    if sec < 90:
        return f"{sec:.1f}s"
    return f"{sec/60.0:.1f}m"


def _extract_actor(event: Dict[str, Any]) -> str:
    actor = event.get("actor")
    if isinstance(actor, dict):
        agent = str(actor.get("agent_name") or "").strip()
        user = str(actor.get("user") or "").strip()
        host = str(actor.get("host") or "").strip()
        main = agent or user or "-"
        return f"{main}@{host}" if host else main
    return str(event.get("created_by") or event.get("user") or "-").strip() or "-"


def _extract_episode_label(event: Dict[str, Any]) -> str:
    ep = event.get("episode")
    if not isinstance(ep, dict):
        return "-"
    return str(ep.get("episode_id") or "").strip() or "-"


def _status_label(event: Dict[str, Any]) -> str:
    state = str(event.get("state") or "").strip().upper()
    if state:
        return state
    pending = event.get("pending")
    if isinstance(pending, dict) and int(pending.get("count") or 0) > 0:
        return "PENDING"
    try:
        code = int(event.get("exit_code"))
    except Exception:
        return "FAILED"
    return "SUCCESS" if code == 0 else "FAILED"


def _build_text_from_ops_event(event: Dict[str, Any]) -> str:
    status = _status_label(event)
    run_id = str(event.get("run_id") or "-").strip()
    cmd = str(event.get("cmd") or "-").strip()
    op = str(event.get("op") or "-").strip()
    llm = str(event.get("llm") or "-").strip()
    exit_code = event.get("exit_code")
    duration = _format_duration_ms(event.get("duration_ms"))
    episode = _extract_episode_label(event)
    actor = _extract_actor(event)

    git = event.get("git") if isinstance(event.get("git"), dict) else {}
    head = str(git.get("head") or "").strip()
    head_short = head[:7] if head else "-"
    branch = str(git.get("branch") or "").strip() or "-"
    dirty = git.get("dirty")
    dirty_mark = " dirty" if dirty is True else ""

    pending = event.get("pending") if isinstance(event.get("pending"), dict) else {}
    pending_count = int(pending.get("count") or 0) if pending else 0
    pending_ids = pending.get("ids") if isinstance(pending.get("ids"), list) else []
    pending_ids = [str(x) for x in pending_ids if str(x).strip()]

    title = f"[ops] {status} cmd={cmd} op={op} episode={episode}"
    lines = [
        f"run_id: {run_id}",
        f"actor: {actor}",
        f"llm: {llm}",
        f"exit: {exit_code}",
        f"duration: {duration}",
        f"git: {head_short} branch={branch}{dirty_mark}",
    ]
    if pending_count > 0:
        qdir = str(pending.get("queue_dir") or "").strip()
        if qdir:
            try:
                qrel = str(Path(qdir).resolve().relative_to(PROJECT_ROOT))
            except Exception:
                qrel = qdir
        else:
            qrel = "-"
        lines.append(f"pending: {pending_count} (queue={qrel})")
        if pending_ids:
            lines.append("pending_ids: " + ", ".join(pending_ids[:8]))

    ep = event.get("episode") if isinstance(event.get("episode"), dict) else {}
    run_dir = str(ep.get("run_dir") or "").strip() if isinstance(ep, dict) else ""
    if run_dir:
        try:
            run_rel = str(Path(run_dir).resolve().relative_to(PROJECT_ROOT))
        except Exception:
            run_rel = run_dir
        lines.append(f"run_dir: {run_rel}")

    body = "```" + "\n".join(lines) + "\n```"
    return title + "\n" + body


def _build_text_from_agent_task_event(event: Dict[str, Any]) -> str:
    ev = str(event.get("event") or "").strip().upper() or "EVENT"
    task_id = str(event.get("task_id") or "-").strip()
    task = str(event.get("task") or "-").strip()
    agent = str(event.get("agent") or "-").strip()

    def rel(p: Any) -> str:
        raw = str(p or "").strip()
        if not raw:
            return "-"
        try:
            return str(Path(raw).resolve().relative_to(PROJECT_ROOT))
        except Exception:
            return raw

    queue_dir = rel(event.get("queue_dir"))
    runbook_path = rel(event.get("runbook_path"))
    pending_path = rel(event.get("pending_path"))
    result_path = rel(event.get("result_path"))
    response_format = str(event.get("response_format") or "").strip() or "-"

    title = f"[agent_task] {ev} task={task} id={task_id}"
    lines = [
        f"agent: {agent}",
        f"task_id: {task_id}",
        f"task: {task}",
        f"response_format: {response_format}",
        f"runbook: {runbook_path}",
        f"queue: {queue_dir}",
        f"pending: {pending_path}",
    ]
    if result_path != "-":
        lines.append(f"result: {result_path}")

    body = "```" + "\n".join(lines) + "\n```"
    return title + "\n" + body


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Best-effort Slack webhook notifier (optional).")
    ap.add_argument("--webhook-url", default="", help="Override webhook URL (default: env YTM_SLACK_WEBHOOK_URL)")
    ap.add_argument("--event-json", default="", help="JSON string describing an event")
    ap.add_argument("--event-file", default="", help="Path to JSON file describing an event")
    ap.add_argument("--text", default="", help="Send raw text (Slack mrkdwn) instead of formatting an event")
    ap.add_argument("--dry-run", action="store_true", help="Print payload to stdout without sending")
    args = ap.parse_args(argv)

    url = _webhook_url(args)
    token = _bot_token()
    channel = _channel()
    if not url and not (token and channel):
        return 0

    text = str(args.text or "").strip()
    if not text:
        raw = str(args.event_json or "").strip()
        if not raw and str(args.event_file or "").strip():
            raw = Path(str(args.event_file)).read_text(encoding="utf-8")
        if raw:
            try:
                event = json.loads(raw)
            except Exception:
                event = {"kind": "unknown", "raw": raw}
        else:
            event = {"kind": "unknown", "at": _now_iso_utc()}
        if isinstance(event, dict) and event.get("kind") == "ops_cli" and event.get("event") == "finish":
            text = _build_text_from_ops_event(event)
        elif isinstance(event, dict) and event.get("kind") == "agent_task" and event.get("event") in {"claim", "complete"}:
            text = _build_text_from_agent_task_event(event)
        else:
            text = json.dumps(event, ensure_ascii=False, indent=2)

    payload = {"text": text}
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    try:
        if url:
            _post_webhook(url, payload)
        else:
            _post_chat_post_message(token, channel, text=text)
    except urllib.error.HTTPError as exc:
        print(f"[slack_notify] http_error status={exc.code}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"[slack_notify] failed: {exc}", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
