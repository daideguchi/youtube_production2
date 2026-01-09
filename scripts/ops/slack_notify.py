#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
import urllib.parse
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


def _slack_api_get(token: str, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
    q = urllib.parse.urlencode({k: str(v) for k, v in params.items() if v is not None and str(v) != ""})
    url = f"{endpoint}?{q}" if q else endpoint
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "factory_commentary/slack_notify",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = (resp.read() or b"").decode("utf-8", errors="ignore")
    try:
        obj = json.loads(body) if body else {}
    except Exception:
        obj = {}
    if isinstance(obj, dict) and obj.get("ok") is False:
        raise RuntimeError(f"slack api failed: {obj.get('error')}")
    return obj if isinstance(obj, dict) else {}


def _post_chat_post_message(
    token: str,
    channel: str,
    *,
    text: str,
    thread_ts: str | None = None,
) -> Dict[str, Any]:
    api = "https://slack.com/api/chat.postMessage"
    payload: Dict[str, Any] = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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
    return obj if isinstance(obj, dict) else {}


def _queue_dir() -> Optional[Path]:
    try:
        from factory_common.agent_mode import get_queue_dir

        return Path(get_queue_dir())
    except Exception:
        return None


def _memo_id_for_slack_reply(channel: str, thread_ts: str, msg_ts: str) -> str:
    compact = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"slack:{channel}:{thread_ts}:{msg_ts}"
    suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
    return f"memo__{compact}__slack__{suffix}"


def _write_slack_reply_memo(*, channel: str, thread_ts: str, msg: Dict[str, Any]) -> Optional[Path]:
    q = _queue_dir()
    if not q:
        return None
    memos_dir = q / "coordination" / "memos"
    memos_dir.mkdir(parents=True, exist_ok=True)

    msg_ts = str(msg.get("ts") or "").strip()
    if not msg_ts:
        return None
    memo_id = _memo_id_for_slack_reply(channel, thread_ts, msg_ts)
    path = memos_dir / f"{memo_id}.json"
    if path.exists():
        return path

    user = str(msg.get("user") or msg.get("username") or "-").strip() or "-"
    text = str(msg.get("text") or "").rstrip()
    if not text:
        text = "-"

    memo = {
        "schema_version": 1,
        "kind": "memo",
        "id": memo_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "from": f"slack:{user}",
        "to": ["*"],
        "subject": f"Slack reply intake (thread={thread_ts})",
        "body": "\n".join(
            [
                "Slack返信を取り込みました。",
                f"- channel: {channel}",
                f"- thread_ts: {thread_ts}",
                f"- msg_ts: {msg_ts}",
                f"- user: {user}",
                "",
                text,
            ]
        ),
        "tags": ["slack", "reply_intake"],
    }
    path.write_text(json.dumps(memo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


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
    ap.add_argument("--channel", default="", help="Override Slack channel (default: env SLACK_CHANNEL)")
    ap.add_argument("--thread-ts", default="", help="Post as a reply in this thread (bot mode only)")
    ap.add_argument("--out-json", default="", help="Write Slack API response JSON to this path (bot mode only)")
    ap.add_argument("--print-ts", action="store_true", help="Print Slack message ts (bot mode only)")
    ap.add_argument("--poll-thread", default="", help="Poll replies for this thread ts (bot mode only)")
    ap.add_argument("--poll-limit", type=int, default=200, help="Max replies to fetch (bot mode only)")
    ap.add_argument("--poll-oldest", default="", help="Only replies newer than this ts (bot mode only)")
    ap.add_argument("--poll-out-json", default="", help="Write polled replies JSON to this path (bot mode only)")
    ap.add_argument("--poll-write-memos", action="store_true", help="Write each reply as agent_org memo")
    ap.add_argument("--dry-run", action="store_true", help="Print payload to stdout without sending")
    args = ap.parse_args(argv)

    url = _webhook_url(args)
    token = _bot_token()
    channel = str(args.channel or "").strip() or _channel()

    poll_thread = str(getattr(args, "poll_thread", "") or "").strip()
    if poll_thread:
        if not (token and channel):
            return 0
        try:
            data = _slack_api_get(
                token,
                "https://slack.com/api/conversations.replies",
                {
                    "channel": channel,
                    "ts": poll_thread,
                    "limit": max(1, int(args.poll_limit or 200)),
                    "oldest": (str(args.poll_oldest or "").strip() or None),
                },
            )
        except Exception as exc:
            print(f"[slack_notify] poll failed: {exc}", file=sys.stderr)
            return 0

        msgs = data.get("messages") if isinstance(data, dict) else None
        messages = msgs if isinstance(msgs, list) else []
        # Exclude the parent message itself.
        replies = [m for m in messages if isinstance(m, dict) and str(m.get("ts") or "") != poll_thread]
        out = {
            "ok": True,
            "channel": channel,
            "thread_ts": poll_thread,
            "fetched_at": _now_iso_utc(),
            "reply_count": len(replies),
            "replies": replies,
        }
        if args.poll_write_memos:
            memo_paths: list[str] = []
            for msg in replies:
                p = _write_slack_reply_memo(channel=channel, thread_ts=poll_thread, msg=msg)
                if p:
                    try:
                        memo_paths.append(str(p.resolve().relative_to(PROJECT_ROOT)))
                    except Exception:
                        memo_paths.append(str(p))
            out["memo_paths"] = memo_paths

        if args.dry_run:
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        if str(args.poll_out_json or "").strip():
            Path(str(args.poll_out_json)).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 0
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

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

    thread_ts = str(getattr(args, "thread_ts", "") or "").strip() or None
    try:
        if url:
            _post_webhook(url, payload)
        else:
            resp = _post_chat_post_message(token, channel, text=text, thread_ts=thread_ts)
            out_path = str(getattr(args, "out_json", "") or "").strip()
            if out_path:
                Path(out_path).write_text(json.dumps(resp, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if bool(getattr(args, "print_ts", False)):
                ts = str(resp.get("ts") or "")
                if not ts and isinstance(resp.get("message"), dict):
                    ts = str(resp["message"].get("ts") or "")
                if ts:
                    print(ts)
    except urllib.error.HTTPError as exc:
        print(f"[slack_notify] http_error status={exc.code}", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"[slack_notify] failed: {exc}", file=sys.stderr)
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
