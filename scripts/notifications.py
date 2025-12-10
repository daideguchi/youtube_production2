"""
簡易Slack通知スクリプト
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Dict, Any


def send_slack(webhook_url: str, text: str, extra: Dict[str, Any] | None = None) -> None:
    payload: Dict[str, Any] = {"text": text}
    if extra:
        payload.update(extra)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


if __name__ == "__main__":
    url = os.getenv("SLACK_WEBHOOK_URL")
    if not url:
        print("SLACK_WEBHOOK_URL not set")
    else:
        send_slack(url, "job_runner test notification")
        print("sent")
