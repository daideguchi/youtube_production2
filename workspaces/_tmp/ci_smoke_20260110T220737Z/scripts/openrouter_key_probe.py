#!/usr/bin/env python3
"""Verify OPENROUTER_API_KEY is set and valid (cheap healthcheck).

Used by: apps/ui-backend/tools/start_manager.py guards.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable

import requests

from _bootstrap import bootstrap

bootstrap()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenRouter API key probe")
    parser.add_argument("--timeout", type=float, default=15.0, help="Request timeout seconds")
    args = parser.parse_args(list(argv) if argv is not None else None)

    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": os.getenv("OPENROUTER_REFERRER", "https://youtube-master.local/healthcheck"),
        "X-Title": os.getenv("OPENROUTER_TITLE", "YouTube Master Healthcheck"),
    }

    try:
        resp = requests.get("https://openrouter.ai/api/v1/models", headers=headers, timeout=float(args.timeout))
    except requests.RequestException as exc:
        print(f"request failed: {exc}", file=sys.stderr)
        return 1

    if resp.status_code in {401, 403}:
        print(f"OpenRouter auth failed (HTTP {resp.status_code})", file=sys.stderr)
        return 1
    if not resp.ok:
        print(f"OpenRouter probe failed: HTTP {resp.status_code}: {resp.text[:200]}", file=sys.stderr)
        return 1

    try:
        data = resp.json()
    except Exception:
        data = None

    models = []
    if isinstance(data, dict):
        raw = data.get("data")
        if isinstance(raw, list):
            models = raw

    print(f"OpenRouter key OK: models={len(models) if models else 'unknown'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

