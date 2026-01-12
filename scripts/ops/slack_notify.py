#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from _bootstrap import bootstrap

PROJECT_ROOT = Path(bootstrap())

_DEFAULT_OUTBOX_DIR = PROJECT_ROOT / "workspaces" / "logs" / "ops" / "slack_outbox"
_DEFAULT_DEDUPE_STATE_PATH = PROJECT_ROOT / "workspaces" / "logs" / "ops" / "slack_notify_dedupe_state.json"


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _env_truthy(key: str) -> bool:
    v = str(os.getenv(key) or "").strip().lower()
    return v in {"1", "true", "yes", "y", "on"}


def _slack_dedupe_off() -> bool:
    return _env_truthy("YTM_SLACK_DEDUPE_OFF")


def _slack_dedupe_window_sec() -> int:
    raw = str(os.getenv("YTM_SLACK_DEDUPE_WINDOW_SEC") or "").strip()
    if not raw:
        return 3600
    try:
        v = int(float(raw))
    except Exception:
        return 3600
    return max(0, v)


def _load_slack_dedupe_state(path: Path) -> Dict[str, float]:
    """
    Local-only Slack notification dedupe state (not tracked by git).
    """
    try:
        if not path.exists():
            return {}
        obj = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and isinstance(obj.get("sent"), dict):
            raw = obj.get("sent") or {}
            return {str(k): float(v) for k, v in raw.items() if str(k).strip() and isinstance(v, (int, float))}
        if isinstance(obj, dict):
            return {str(k): float(v) for k, v in obj.items() if str(k).strip() and isinstance(v, (int, float))}
        return {}
    except Exception:
        return {}


def _save_slack_dedupe_state(path: Path, sent: Dict[str, float]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "updated_at": _now_iso_utc(), "sent": sent}
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
    except Exception:
        return


def _ops_event_dedupe_key(event: Dict[str, Any]) -> Optional[str]:
    status = _status_label(event)
    if status not in {"FAILED", "WARN", "PENDING"}:
        return None

    cmd = str(event.get("cmd") or "").strip()
    op = str(event.get("op") or "").strip()
    episode = _extract_episode_label(event)

    try:
        exit_code = int(event.get("exit_code"))
    except Exception:
        exit_code = -1

    key_obj = {
        "kind": "ops_cli.finish",
        "status": status,
        "cmd": cmd,
        "op": op,
        "episode": episode,
        "exit_code": exit_code,
    }
    raw = json.dumps(key_obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _slack_dedupe_should_skip(key: str, *, now_ts: float, window_sec: int) -> bool:
    state = _load_slack_dedupe_state(_DEFAULT_DEDUPE_STATE_PATH)
    last = state.get(str(key))
    return bool(last and (now_ts - float(last)) < float(window_sec))


def _slack_dedupe_mark_sent(key: str, *, now_ts: float, window_sec: int) -> None:
    state = _load_slack_dedupe_state(_DEFAULT_DEDUPE_STATE_PATH)
    state[str(key)] = float(now_ts)

    # Prune: keep the file bounded.
    max_age = max(7 * 24 * 3600, int(window_sec) * 4)
    cutoff = float(now_ts) - float(max_age)
    for k, ts in list(state.items()):
        try:
            if float(ts) < cutoff:
                del state[k]
        except Exception:
            del state[k]

    _save_slack_dedupe_state(_DEFAULT_DEDUPE_STATE_PATH, state)


def _outbox_dir(args: argparse.Namespace) -> Path:
    raw = str(getattr(args, "outbox_dir", "") or "").strip()
    if raw:
        return Path(raw)
    return _DEFAULT_OUTBOX_DIR


def _write_outbox_message(
    outbox_dir: Path,
    *,
    channel: str,
    thread_ts: Optional[str],
    text: str,
    error: str,
) -> Optional[Path]:
    """
    Write a Slack message into a local outbox when sending fails.

    NOTE:
    - Outbox is local-only under workspaces/ (not tracked by git).
    - Content is already guarded (no secrets / no env dumps). Still, we store only the intended text.
    """
    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        key = f"slack_outbox:{channel}:{thread_ts or ''}:{text}"
        suffix = hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]
        p = outbox_dir / f"outbox__{ts}__{suffix}.json"
        payload = {
            "kind": "slack_outbox",
            "created_at": _now_iso_utc(),
            "channel": str(channel or "").strip(),
            "thread_ts": str(thread_ts or "").strip(),
            "text": str(text or ""),
            "error": str(error or "").strip(),
        }
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(p)
        return p
    except Exception:
        return None


def _iter_outbox_files(outbox_dir: Path) -> list[Path]:
    if not outbox_dir.exists():
        return []
    return sorted([p for p in outbox_dir.glob("outbox__*.json") if p.is_file()])


def _flush_outbox(
    outbox_dir: Path,
    *,
    token: str,
    fallback_channel: str,
    webhook_url: str,
    limit: int,
    dry_run: bool,
) -> dict:
    """
    Best-effort resend for local outbox messages.
    - On success: move to outbox/sent/
    - On failure: keep in place
    """
    out = {
        "ok": True,
        "outbox_dir": str(outbox_dir),
        "attempted": 0,
        "sent": 0,
        "failed": 0,
        "moved": 0,
        "errors": [],
    }

    files = _iter_outbox_files(outbox_dir)[: max(0, int(limit or 0))]
    if not files:
        return out

    sent_dir = outbox_dir / "sent"
    if not dry_run:
        sent_dir.mkdir(parents=True, exist_ok=True)

    for p in files:
        out["attempted"] += 1
        try:
            raw = p.read_text(encoding="utf-8")
            obj = json.loads(raw) if raw else {}
            if not isinstance(obj, dict):
                raise ValueError("invalid outbox json")
            text = str(obj.get("text") or "").strip()
            if not text:
                raise ValueError("empty text")
            ch = str(obj.get("channel") or "").strip() or str(fallback_channel or "").strip()
            thread_ts = str(obj.get("thread_ts") or "").strip() or None

            if dry_run:
                continue

            # If a thread_ts is present, webhook cannot be used; require bot token + channel.
            if thread_ts:
                if not (token and ch):
                    raise RuntimeError("missing SLACK_BOT_TOKEN/SLACK_CHANNEL for thread reply")
                _post_chat_post_message(token, ch, text=text, thread_ts=thread_ts)
            else:
                # Prefer webhook when available; otherwise use bot if configured.
                if webhook_url:
                    _post_webhook(webhook_url, {"text": text})
                elif token and ch:
                    _post_chat_post_message(token, ch, text=text, thread_ts=None)
                else:
                    raise RuntimeError("missing Slack credentials (webhook or bot token)")

            out["sent"] += 1
            dest = sent_dir / p.name
            p.replace(dest)
            out["moved"] += 1
        except Exception as exc:
            out["failed"] += 1
            out["errors"].append(f"{p.name}: {type(exc).__name__}: {exc}")
            continue

    return out


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


_SUSPECT_SECRET_TOKEN_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"), "slack_token_like"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"), "openai_key_like"),
    (re.compile(r"\bfw_[A-Za-z0-9]{8,}\b"), "fireworks_key_like"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"), "github_token_like"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{10,}\b"), "github_pat_like"),
    (re.compile(r"\bya29\.[A-Za-z0-9_-]{20,}\b"), "google_oauth_like"),
]

_ENV_ASSIGNMENT_RE = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>.*)$")
_SUSPECT_SECRET_NAME_HINTS = (
    "TOKEN",
    "API_KEY",
    "SECRET",
    "PASSWORD",
    "PRIVATE_KEY",
    "ACCESS_KEY",
)


def _looks_like_env_dump(text: str) -> bool:
    """
    Detect obvious environment dumps (env/set/typeset output).

    Rationale: even if values are redacted, dumping environment into Slack is noisy and risky.
    """
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()]
    if len(lines) < 20:
        return False
    envish = 0
    for ln in lines:
        s = ln.strip()
        if _ENV_ASSIGNMENT_RE.match(s):
            envish += 1
            continue
        # zsh `typeset -p` style: "tied path PATH=..." / "integer 10 ..."
        if s.startswith("tied ") or s.startswith("integer "):
            envish += 1
            continue
    return envish >= 20


def _detect_sensitive_reasons(text: str) -> list[str]:
    reasons: list[str] = []
    raw = str(text or "")

    for pat, label in _SUSPECT_SECRET_TOKEN_PATTERNS:
        if pat.search(raw):
            reasons.append(label)

    for ln in raw.splitlines():
        m = _ENV_ASSIGNMENT_RE.match(ln.strip())
        if not m:
            continue
        name = str(m.group("name") or "").strip()
        val = str(m.group("value") or "").strip()
        if not name or not val:
            continue
        upper = name.upper()
        if any(h in upper for h in _SUSPECT_SECRET_NAME_HINTS):
            reasons.append(f"env_var:{name}")

    if _looks_like_env_dump(raw):
        reasons.append("env_dump_like")

    # Stable order / dedupe.
    out: list[str] = []
    seen: set[str] = set()
    for r in reasons:
        if r in seen:
            continue
        seen.add(r)
        out.append(r)
    return out


def _redact_for_local_log(text: str) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    out = raw
    for pat, _label in _SUSPECT_SECRET_TOKEN_PATTERNS:
        out = pat.sub("[REDACTED]", out)
    redacted_lines: list[str] = []
    for ln in out.splitlines():
        m = _ENV_ASSIGNMENT_RE.match(ln.strip())
        if not m:
            redacted_lines.append(ln)
            continue
        name = str(m.group("name") or "").strip()
        val = str(m.group("value") or "").strip()
        upper = name.upper()
        if name and val and any(h in upper for h in _SUSPECT_SECRET_NAME_HINTS):
            redacted_lines.append(f"{name}=[REDACTED]")
        else:
            redacted_lines.append(ln)
    return "\n".join(redacted_lines).rstrip() + "\n"


def _guard_slack_text_or_block(text: str) -> bool:
    """
    Safety guard:
    - Do NOT send messages that look like env dumps or contain secret-like tokens/vars.
    - Return True if OK to send; False if blocked.
    """
    reasons = _detect_sensitive_reasons(text)
    if not reasons:
        return True

    try:
        out_dir = PROJECT_ROOT / "workspaces" / "logs" / "ops"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        p = out_dir / f"slack_notify_blocked_{ts}.txt"
        p.write_text(_redact_for_local_log(text), encoding="utf-8")
        print(f"[slack_notify] blocked: suspicious content ({', '.join(reasons[:6])}); wrote {p}", file=sys.stderr)
    except Exception:
        print(f"[slack_notify] blocked: suspicious content ({', '.join(reasons[:6])})", file=sys.stderr)

    return False


_SLACK_CONVERSATION_ID_RE = re.compile(r"^[CDG][A-Z0-9]{8,}$")


def _looks_like_conversation_id(value: str) -> bool:
    return bool(_SLACK_CONVERSATION_ID_RE.match((value or "").strip()))


def _resolve_channel_id(token: str, channel: str) -> str:
    """
    Resolve env-friendly channel spec to Slack conversation id.

    - If a conversation id (C*/D*/G*) is provided, return as-is.
    - Otherwise treat it as a channel name (#name or name) and look it up via conversations.list.
    """

    raw = str(channel or "").strip()
    if not raw:
        return raw
    if _looks_like_conversation_id(raw):
        return raw

    name = raw[1:] if raw.startswith("#") else raw
    name_norm = name.lower()
    cursor = ""
    for _ in range(20):
        data = _slack_api_get(
            token,
            "https://slack.com/api/conversations.list",
            {
                "limit": 200,
                "cursor": cursor or None,
                "types": "public_channel,private_channel,im,mpim",
                "exclude_archived": "true",
            },
        )
        chans = data.get("channels") if isinstance(data, dict) else None
        channels = chans if isinstance(chans, list) else []
        for ch in channels:
            if not isinstance(ch, dict):
                continue
            ch_id = str(ch.get("id") or "").strip()
            ch_name = str(ch.get("name") or "").strip()
            if ch_id and ch_id == raw:
                return raw
            if ch_id and ch_name and ch_name.lower() == name_norm:
                return ch_id

        meta = data.get("response_metadata") if isinstance(data, dict) else None
        cursor = str((meta or {}).get("next_cursor") or "").strip() if isinstance(meta, dict) else ""
        if not cursor:
            break

    return raw


def _post_chat_post_message(
    token: str,
    channel: str,
    *,
    text: str,
    thread_ts: str | None = None,
) -> Dict[str, Any]:
    api = "https://slack.com/api/chat.postMessage"
    channel_id = _resolve_channel_id(token, channel)
    payload: Dict[str, Any] = {"channel": channel_id, "text": text}
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


def _slack_reply_memo_suffix(channel: str, thread_ts: str, msg_ts: str) -> str:
    key = f"slack:{channel}:{thread_ts}:{msg_ts}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:10]


def _memo_id_for_slack_reply(channel: str, thread_ts: str, msg_ts: str) -> str:
    # Use the Slack message timestamp for stable id/dedup across polls.
    suffix = _slack_reply_memo_suffix(channel, thread_ts, msg_ts)
    try:
        compact = datetime.fromtimestamp(float(msg_ts), tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    except Exception:
        compact = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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

    # Stable dedupe: avoid writing duplicates when polling multiple times.
    suffix = _slack_reply_memo_suffix(channel, thread_ts, msg_ts)
    existing = sorted(memos_dir.glob(f"memo__*__slack__{suffix}.json"))
    if existing:
        return existing[0]

    memo_id = _memo_id_for_slack_reply(channel, thread_ts, msg_ts)
    path = memos_dir / f"{memo_id}.json"
    if path.exists():
        return path

    user = str(msg.get("user") or msg.get("username") or "-").strip() or "-"
    raw_text = str(msg.get("text") or "")
    already_redacted = bool(msg.get("redacted") is True)
    if already_redacted:
        raw_reasons = msg.get("redact_reasons") if isinstance(msg.get("redact_reasons"), list) else None
        reasons = [str(x) for x in (raw_reasons or ["already_redacted"]) if str(x).strip()]
        text = raw_text.rstrip()
    else:
        reasons = _detect_sensitive_reasons(raw_text)
        text = _redact_for_local_log(raw_text).rstrip("\n") if reasons else raw_text.rstrip()
    if not text.strip():
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
                f"- redacted: {'yes' if reasons else 'no'}",
                "",
                text,
            ]
        ),
        "tags": ["slack", "reply_intake"] + (["redacted"] if reasons else []),
    }
    path.write_text(json.dumps(memo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _write_slack_message_memo(*, channel: str, msg: Dict[str, Any]) -> Optional[Path]:
    """
    Intake a top-level Slack message (or reply) as a memo.

    Note: message text is best-effort redacted to avoid storing secret-like tokens locally.
    """
    q = _queue_dir()
    if not q:
        return None
    memos_dir = q / "coordination" / "memos"
    memos_dir.mkdir(parents=True, exist_ok=True)

    msg_ts = str(msg.get("ts") or "").strip()
    if not msg_ts:
        return None
    thread_ts = str(msg.get("thread_ts") or msg_ts).strip()

    suffix = _slack_reply_memo_suffix(channel, thread_ts, msg_ts)
    existing = sorted(memos_dir.glob(f"memo__*__slack__{suffix}.json"))
    if existing:
        return existing[0]

    memo_id = _memo_id_for_slack_reply(channel, thread_ts, msg_ts)
    path = memos_dir / f"{memo_id}.json"
    if path.exists():
        return path

    user = str(msg.get("user") or msg.get("username") or "-").strip() or "-"
    raw_text = str(msg.get("text") or "")
    already_redacted = bool(msg.get("redacted") is True)
    if already_redacted:
        raw_reasons = msg.get("redact_reasons") if isinstance(msg.get("redact_reasons"), list) else None
        reasons = [str(x) for x in (raw_reasons or ["already_redacted"]) if str(x).strip()]
        text = raw_text.rstrip()
    else:
        reasons = _detect_sensitive_reasons(raw_text)
        text = _redact_for_local_log(raw_text).rstrip("\n") if reasons else raw_text.rstrip()
    if not text.strip():
        text = "-"

    memo = {
        "schema_version": 1,
        "kind": "memo",
        "id": memo_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "from": f"slack:{user}",
        "to": ["*"],
        "subject": f"Slack message intake (thread={thread_ts})",
        "body": "\n".join(
            [
                "Slackメッセージを取り込みました。",
                f"- channel: {channel}",
                f"- thread_ts: {thread_ts}",
                f"- msg_ts: {msg_ts}",
                f"- user: {user}",
                f"- redacted: {'yes' if reasons else 'no'}",
                "",
                text,
            ]
        ),
        "tags": ["slack", "message_intake"] + (["redacted"] if reasons else []),
    }
    path.write_text(json.dumps(memo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _sanitize_slack_message_for_local_store(msg: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = dict(msg or {})
    raw_text = str(out.get("text") or "")
    reasons = _detect_sensitive_reasons(raw_text)
    if reasons:
        out["text"] = _redact_for_local_log(raw_text).rstrip("\n")
        out["redacted"] = True
        out["redact_reasons"] = reasons
    return out


def _poll_channel_history(
    token: str,
    channel_id: str,
    *,
    limit: int,
    oldest: str | None,
    latest: str | None,
    include_replies: bool,
    grep: str | None,
) -> list[Dict[str, Any]]:
    out: list[Dict[str, Any]] = []
    cursor = ""
    pattern = re.compile(grep, re.IGNORECASE) if grep else None

    for _ in range(30):
        remaining = max(0, int(limit) - len(out))
        if remaining <= 0:
            break
        data = _slack_api_get(
            token,
            "https://slack.com/api/conversations.history",
            {
                "channel": channel_id,
                "limit": min(200, remaining),
                "cursor": cursor or None,
                "oldest": (str(oldest).strip() if oldest else None),
                "latest": (str(latest).strip() if latest else None),
                "inclusive": "false",
            },
        )
        msgs = data.get("messages") if isinstance(data, dict) else None
        messages = msgs if isinstance(msgs, list) else []
        for m in messages:
            if not isinstance(m, dict):
                continue
            ts = str(m.get("ts") or "").strip()
            thread_ts = str(m.get("thread_ts") or "").strip()
            if not include_replies and thread_ts and ts and thread_ts != ts:
                continue
            if pattern:
                txt = str(m.get("text") or "")
                if not pattern.search(txt):
                    continue
            out.append(_sanitize_slack_message_for_local_store(m))
            if len(out) >= int(limit):
                break

        meta = data.get("response_metadata") if isinstance(data, dict) else None
        cursor = str((meta or {}).get("next_cursor") or "").strip() if isinstance(meta, dict) else ""
        if not cursor:
            break

    return out


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
    warnings = event.get("warnings")
    if isinstance(warnings, dict) and int(warnings.get("count") or 0) > 0:
        return "WARN"
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

    ops_latest = event.get("ops_latest") if isinstance(event.get("ops_latest"), dict) else {}
    ops_latest_path = str(ops_latest.get("path") or "").strip() if isinstance(ops_latest, dict) else ""
    if ops_latest_path:
        lines.append(f"ops_latest: {ops_latest_path}")

    run_logs = event.get("run_logs") if isinstance(event.get("run_logs"), dict) else {}
    run_logs_dir = str(run_logs.get("dir") or "").strip() if isinstance(run_logs, dict) else ""
    if run_logs_dir:
        lines.append(f"run_logs: {run_logs_dir}")

    exit_raw = event.get("exit_code_raw")
    if exit_raw not in (None, ""):
        lines.append(f"exit_raw: {exit_raw}")

    failed_run = event.get("failed_run") if isinstance(event.get("failed_run"), dict) else {}
    failed_log = str(failed_run.get("log_path") or "").strip() if isinstance(failed_run, dict) else ""
    if failed_log:
        try:
            failed_rel = str(Path(failed_log).resolve().relative_to(PROJECT_ROOT))
        except Exception:
            failed_rel = failed_log
        lines.append(f"failed_log: {failed_rel}")

    warn_run = event.get("warn_run") if isinstance(event.get("warn_run"), dict) else {}
    warn_log = str(warn_run.get("log_path") or "").strip() if isinstance(warn_run, dict) else ""
    if warn_log:
        try:
            warn_rel = str(Path(warn_log).resolve().relative_to(PROJECT_ROOT))
        except Exception:
            warn_rel = warn_log
        lines.append(f"warn_log: {warn_rel}")

    warnings = event.get("warnings") if isinstance(event.get("warnings"), dict) else {}
    warn_count = int(warnings.get("count") or 0) if isinstance(warnings, dict) else 0
    if warn_count > 0:
        lines.append(f"warnings: {warn_count}")
        manifest_path = str(warnings.get("manifest_path") or "").strip()
        if manifest_path:
            try:
                manifest_rel = str(Path(manifest_path).resolve().relative_to(PROJECT_ROOT))
            except Exception:
                manifest_rel = manifest_path
            lines.append(f"manifest: {manifest_rel}")
        items = warnings.get("items") if isinstance(warnings.get("items"), list) else []
        for w in [str(x) for x in items if str(x).strip()][:3]:
            lines.append(f"- {w}")

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
    ap.add_argument("--outbox-dir", default="", help="Local outbox dir for failed sends (default: workspaces/logs/ops/slack_outbox)")
    ap.add_argument("--flush-outbox", action="store_true", help="Resend local outbox messages (best effort)")
    ap.add_argument("--flush-outbox-limit", type=int, default=50, help="Max outbox messages to try per flush (default: 50)")
    ap.add_argument("--out-json", default="", help="Write Slack API response JSON to this path (bot mode only)")
    ap.add_argument("--print-ts", action="store_true", help="Print Slack message ts (bot mode only)")
    ap.add_argument("--poll-thread", default="", help="Poll replies for this thread ts (bot mode only)")
    ap.add_argument("--poll-limit", type=int, default=200, help="Max replies to fetch (bot mode only)")
    ap.add_argument("--poll-oldest", default="", help="Only replies newer than this ts (bot mode only)")
    ap.add_argument("--poll-out-json", default="", help="Write polled replies JSON to this path (bot mode only)")
    ap.add_argument("--poll-write-memos", action="store_true", help="Write each reply as agent_org memo")
    ap.add_argument("--history", action="store_true", help="Poll recent channel history (bot mode only)")
    ap.add_argument("--history-limit", type=int, default=200, help="Max messages to fetch (bot mode only)")
    ap.add_argument("--history-oldest", default="", help="Only messages newer than this ts (bot mode only)")
    ap.add_argument("--history-latest", default="", help="Only messages older than this ts (bot mode only)")
    ap.add_argument("--history-grep", default="", help="Only include messages whose text matches this regex (case-insensitive)")
    ap.add_argument(
        "--history-include-replies",
        action="store_true",
        help="Include thread replies in channel history (default: top-level only)",
    )
    ap.add_argument("--history-out-json", default="", help="Write polled history JSON to this path (bot mode only)")
    ap.add_argument("--history-write-memos", action="store_true", help="Write each message as agent_org memo")
    ap.add_argument("--dry-run", action="store_true", help="Print payload to stdout without sending")
    args = ap.parse_args(argv)

    url = _webhook_url(args)
    token = _bot_token()
    channel = str(args.channel or "").strip() or _channel()
    outbox_dir = _outbox_dir(args)

    poll_thread = str(getattr(args, "poll_thread", "") or "").strip()
    if poll_thread:
        if not (token and channel):
            return 0
        channel_id = _resolve_channel_id(token, channel)
        try:
            data = _slack_api_get(
                token,
                "https://slack.com/api/conversations.replies",
                {
                    "channel": channel_id,
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
        replies_raw = [m for m in messages if isinstance(m, dict) and str(m.get("ts") or "") != poll_thread]
        replies = [_sanitize_slack_message_for_local_store(m) for m in replies_raw]
        out = {
            "ok": True,
            "channel": channel_id,
            "thread_ts": poll_thread,
            "fetched_at": _now_iso_utc(),
            "reply_count": len(replies),
            "replies": replies,
        }
        if args.poll_write_memos:
            memo_paths: list[str] = []
            for msg in replies:
                p = _write_slack_reply_memo(channel=channel_id, thread_ts=poll_thread, msg=msg)
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

    if bool(getattr(args, "flush_outbox", False)):
        out = _flush_outbox(
            outbox_dir,
            token=token,
            fallback_channel=channel,
            webhook_url=url,
            limit=int(getattr(args, "flush_outbox_limit", 50) or 50),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
        if args.dry_run:
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    history_mode = bool(getattr(args, "history", False)) or bool(str(getattr(args, "history_out_json", "") or "").strip())
    if history_mode:
        if not (token and channel):
            return 0
        channel_id = _resolve_channel_id(token, channel)
        try:
            messages = _poll_channel_history(
                token,
                channel_id,
                limit=max(1, int(getattr(args, "history_limit", 200) or 200)),
                oldest=(str(getattr(args, "history_oldest", "") or "").strip() or None),
                latest=(str(getattr(args, "history_latest", "") or "").strip() or None),
                include_replies=bool(getattr(args, "history_include_replies", False)),
                grep=(str(getattr(args, "history_grep", "") or "").strip() or None),
            )
        except Exception as exc:
            print(f"[slack_notify] history poll failed: {exc}", file=sys.stderr)
            return 0

        out = {
            "ok": True,
            "channel": channel_id,
            "fetched_at": _now_iso_utc(),
            "message_count": len(messages),
            "messages": messages,
        }
        if bool(getattr(args, "history_write_memos", False)):
            memo_paths: list[str] = []
            for msg in messages:
                p = _write_slack_message_memo(channel=channel_id, msg=msg)
                if p:
                    try:
                        memo_paths.append(str(p.resolve().relative_to(PROJECT_ROOT)))
                    except Exception:
                        memo_paths.append(str(p))
            out["memo_paths"] = memo_paths

        if args.dry_run:
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0
        out_path = str(getattr(args, "history_out_json", "") or "").strip()
        if out_path:
            Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return 0
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if not url and not (token and channel):
        return 0

    ops_event: Optional[Dict[str, Any]] = None
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
            ops_event = event
            text = _build_text_from_ops_event(event)
        elif isinstance(event, dict) and event.get("kind") == "agent_task" and event.get("event") in {"claim", "complete"}:
            text = _build_text_from_agent_task_event(event)
        else:
            text = json.dumps(event, ensure_ascii=False, indent=2)

    payload = {"text": text}
    if args.dry_run:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not _guard_slack_text_or_block(text):
        return 0

    dedupe_key = None
    dedupe_now = time.time()
    dedupe_window = _slack_dedupe_window_sec()
    if ops_event is not None and not _slack_dedupe_off() and dedupe_window > 0:
        key = _ops_event_dedupe_key(ops_event)
        if key and _slack_dedupe_should_skip(key, now_ts=dedupe_now, window_sec=dedupe_window):
            return 0
        dedupe_key = key

    thread_ts = str(getattr(args, "thread_ts", "") or "").strip() or None
    try:
        # Thread replies require bot mode; webhook cannot post into a thread.
        if thread_ts:
            if not (token and channel):
                raise RuntimeError("missing SLACK_BOT_TOKEN/SLACK_CHANNEL for thread reply")
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
        elif url:
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
        p = _write_outbox_message(
            outbox_dir,
            channel=channel,
            thread_ts=thread_ts,
            text=text,
            error=f"{type(exc).__name__}: {exc}",
        )
        if p:
            try:
                prel = str(p.resolve().relative_to(PROJECT_ROOT))
            except Exception:
                prel = str(p)
            print(f"[slack_notify] failed: {exc} (saved outbox: {prel})", file=sys.stderr)
        else:
            print(f"[slack_notify] failed: {exc}", file=sys.stderr)
        return 0

    if dedupe_key:
        _slack_dedupe_mark_sent(dedupe_key, now_ts=dedupe_now, window_sec=dedupe_window)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
