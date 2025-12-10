#!/usr/bin/env python3
"""
api_health_check.py -- REST API smoke test.

Hits the critical FastAPI endpoints and reports status / latency.
Results are serialized to logs/regression/api_health_<timestamp>.log and stderr/stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import urllib.request
import urllib.error

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_ROOT = PROJECT_ROOT / "logs" / "regression"
DEFAULT_ENDPOINTS = [
    "/api/healthz",
    "/api/prompts",
    "/api/channels",
    "/api/planning",
    "/api/video-production/projects",
]

def _discover_channels() -> List[str]:
    try:
        from commentary_01_srtfile_v2.core.tools import planning_store  # type: ignore

        planning_store.refresh(force=True)
        channels = list(planning_store.list_channels())
        if channels:
            return list(channels)
    except Exception:
        pass
    channel_dir = PROJECT_ROOT / "progress" / "channels"
    if channel_dir.exists():
        return sorted(path.stem.upper() for path in channel_dir.glob("*.csv"))
    return []

@dataclass
class ProbeResult:
    endpoint: str
    method: str
    status: Optional[int]
    ok: bool
    elapsed_ms: int
    detail: Optional[str] = None


def _http_request(base_url: str, path: str, method: str) -> ProbeResult:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, method=method)
    start = time.perf_counter()
    status = None
    detail = None
    ok = False
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310
            body = resp.read()
            status = resp.status
            ok = 200 <= resp.status < 300
            if not ok:
                detail = body.decode("utf-8", errors="replace")[:400]
    except urllib.error.HTTPError as exc:
        status = exc.code
        ok = False
        detail = exc.read().decode("utf-8", errors="replace")[:400]
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    return ProbeResult(endpoint=path, method=method, status=status, ok=ok, elapsed_ms=elapsed_ms, detail=detail)


def run_health_checks(base_url: str, endpoints: List[str]) -> List[ProbeResult]:
    results: List[ProbeResult] = []
    for endpoint in endpoints:
        result = _http_request(base_url, endpoint, "GET")
        results.append(result)
    return results


def write_log(results: List[ProbeResult]) -> Path:
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = LOG_ROOT / f"api_health_{timestamp}.log"
    payload = {
        "timestamp": timestamp,
        "results": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="FastAPI health check")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base URL")
    parser.add_argument("--endpoint", action="append", help="Additional endpoint to probe")
    parser.add_argument("--channel", action="append", help="CHコード。指定すると planning 関連エンドポイントもチェック")
    parser.add_argument("--all-channels", action="store_true", help="planning_store から全チャンネルを読み出してチェック")
    args = parser.parse_args()

    endpoints = list(DEFAULT_ENDPOINTS)
    if args.endpoint:
        endpoints.extend(args.endpoint)

    channel_targets: List[str] = []
    if args.all_channels:
        channel_targets.extend(_discover_channels())
    if args.channel:
        channel_targets.extend(code.strip().upper() for code in args.channel if code.strip())

    if channel_targets:
        seen = set()
        deduped: List[str] = []
        for code in channel_targets:
            if code not in seen:
                deduped.append(code)
                seen.add(code)
        for code in deduped:
            endpoints.append(f"/api/planning?channel={code}")
            endpoints.append(f"/api/planning/spreadsheet?channel={code}")

    results = run_health_checks(args.base_url, endpoints)
    log_path = write_log(results)

    print(f"API health log written to {log_path}")
    failures = [r for r in results if not r.ok]
    for result in results:
        status = result.status if result.status is not None else "ERR"
        line = f"[{status}] {result.endpoint} ({result.elapsed_ms} ms)"
        if not result.ok and result.detail:
            line += f" detail={result.detail}"
        print(line)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
