"""
簡易Slack通知スクリプト
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Dict, Any


def send_slack(webhook_url: str, text: str, extra: Dict[str, Any] | None = None) -> None:
    """
    Best-effort Slack notification.

    Supports:
    - Incoming Webhook URL (explicit arg or env YTM_SLACK_WEBHOOK_URL / SLACK_WEBHOOK_URL)
    - Bot token mode (env SLACK_BOT_TOKEN + SLACK_CHANNEL)
    """
    url = (webhook_url or "").strip()
    if not url:
        url = str(os.getenv("YTM_SLACK_WEBHOOK_URL") or os.getenv("SLACK_WEBHOOK_URL") or "").strip()

    token = str(os.getenv("SLACK_BOT_TOKEN") or os.getenv("YTM_SLACK_BOT_TOKEN") or "").strip()
    channel = str(os.getenv("SLACK_CHANNEL") or os.getenv("YTM_SLACK_CHANNEL") or "").strip()

    payload: Dict[str, Any] = {"text": text}
    if extra:
        payload.update(extra)

    try:
        if url:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
            return

        if token and channel:
            api = "https://slack.com/api/chat.postMessage"
            bot_payload: Dict[str, Any] = {"channel": channel, "text": text}
            if extra:
                bot_payload.update(extra)
            data = json.dumps(bot_payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                api,
                data=data,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json; charset=utf-8",
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
                # Don't raise; keep best-effort semantics.
                return
            return
    except urllib.error.HTTPError:
        return
    except Exception:
        return


if __name__ == "__main__":
    # Quick smoke-test (no secrets printed).
    send_slack("", "job_runner test notification")
    print("done (best-effort)")
