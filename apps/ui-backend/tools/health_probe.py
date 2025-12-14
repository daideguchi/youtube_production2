#!/usr/bin/env python3
"""Simple health probe for UI services."""

from __future__ import annotations

import argparse
from typing import Iterable, Optional

import requests

DEFAULT_URLS = (
    "http://127.0.0.1:8000/health",
    "http://127.0.0.1:3000/",
)


def probe(url: str, timeout: float) -> bool:
    try:
        resp = requests.get(url, timeout=timeout)
        status = resp.status_code
        ok = 200 <= status < 400
        body = resp.text.strip()
    except requests.RequestException as exc:
        print(f"{url} -> ERROR ({exc})")
        return False
    else:
        preview = body[:120].replace("\n", " ")
        print(f"{url} -> {status} {preview}")
        return ok


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="UI health probe")
    parser.add_argument(
        "urls",
        nargs="*",
        default=DEFAULT_URLS,
        help="URLs to query (default: backend /health and frontend root)",
    )
    parser.add_argument("--timeout", type=float, default=3.0, help="Request timeout (seconds)")
    args = parser.parse_args(list(argv) if argv is not None else None)

    success = True
    for url in args.urls:
        success &= probe(url, args.timeout)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
