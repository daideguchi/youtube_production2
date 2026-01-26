#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _slack_token() -> str | None:
    token = (os.getenv("SLACK_BOT_TOKEN") or os.getenv("YTM_SLACK_BOT_TOKEN") or "").strip()
    if token:
        return token
    return _read_text(Path.home() / ".config" / "slack" / "bot_token")


def _slack_channel() -> str | None:
    ch = (os.getenv("SLACK_CHANNEL") or os.getenv("YTM_SLACK_CHANNEL") or "").strip()
    if ch:
        return ch
    return _read_text(Path.home() / ".config" / "slack" / "channel")


def _slack_thread_ts() -> str | None:
    ts = (os.getenv("SLACK_THREAD_TS") or os.getenv("YTM_SLACK_THREAD_TS") or "").strip()
    if ts:
        return ts
    return _read_text(Path.home() / ".config" / "slack" / "hq_thread_ts")


def _post_message(*, token: str, channel: str, text: str, thread_ts: str | None) -> bool:
    api = "https://slack.com/api/chat.postMessage"
    payload: dict[str, object] = {"channel": channel, "text": text}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        api,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = (resp.read() or b"").decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        print(f"[slack_notify] HTTPError: {e}", file=sys.stderr)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[slack_notify] error: {e}", file=sys.stderr)
        return False

    try:
        obj = json.loads(body) if body else {}
    except Exception:
        obj = {}
    if isinstance(obj, dict) and obj.get("ok") is True:
        return True

    err = obj.get("error") if isinstance(obj, dict) else None
    print(f"[slack_notify] failed: {err or 'unknown_error'}", file=sys.stderr)
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Send a Slack message (best-effort).")
    ap.add_argument("text", nargs="*", help="Message text")
    ap.add_argument("--no-thread", action="store_true", help="Do not post into thread even if configured.")
    args = ap.parse_args()

    text = " ".join(args.text).strip()
    if not text:
        print("Usage: python3 scripts/slack_notify.py \"message\"", file=sys.stderr)
        return 2

    token = _slack_token()
    channel = _slack_channel()
    if not token or not channel:
        print("[slack_notify] missing token/channel config", file=sys.stderr)
        return 0

    thread_ts = None if bool(args.no_thread) else _slack_thread_ts()
    _post_message(token=token, channel=channel, text=text, thread_ts=thread_ts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

