from __future__ import annotations
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Any

try:
    from factory_common.paths import logs_root

    DEFAULT_LOG_PATH = str(logs_root() / "llm_usage.jsonl")
except Exception:
    DEFAULT_LOG_PATH = "workspaces/logs/llm_usage.jsonl"


def load(path: Path):
    if not path.exists():
        raise SystemExit(f"log not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def summarize(path: Path):
    totals = Counter()
    by_model = Counter()
    by_status = Counter()
    latency = defaultdict(list)
    tokens = defaultdict(int)
    for rec in load(path):
        status = rec.get("status") or rec.get("status_code") or "unknown"
        totals[status] += 1
        model = rec.get("model") or "(none)"
        by_model[(model, status)] += 1
        if "latency_ms" in rec and isinstance(rec.get("latency_ms"), (int, float)):
            latency[status].append(rec["latency_ms"])
        usage = rec.get("usage") or {}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            v = usage.get(k)
            if isinstance(v, int):
                tokens[k] += v
        # also count HTTP status_code if present
        sc = rec.get("status_code")
        if isinstance(sc, int):
            by_status[sc] += 1
    return totals, by_model, latency, tokens, by_status


def pct(part, whole):
    return 0.0 if not whole else round(part * 100.0 / whole, 2)


def main():
    ap = argparse.ArgumentParser(description="Summarize llm_usage.jsonl")
    ap.add_argument("--log", default=DEFAULT_LOG_PATH, help="path to log file")
    args = ap.parse_args()
    log_path = Path(args.log)
    totals, by_model, latency, tokens, by_status = summarize(log_path)
    total = sum(totals.values())
    print(f"Log: {log_path}")
    print(f"Total records: {total}")
    for status, count in totals.most_common():
        durs = latency.get(status, [])
        avg = round(sum(durs) / len(durs), 1) if durs else 0
        print(f"  {status}: {count} ({pct(count, total)}%), avg_latency_ms={avg}")
    if tokens:
        print("\nTokens (sum):")
        for k, v in tokens.items():
            print(f"  {k}: {v}")
    if by_model:
        print("\nBy model:")
        for (model, status), count in by_model.most_common():
            print(f"  {model} {status}: {count}")
    if by_status:
        print("\nHTTP status codes:")
        for sc, count in by_status.most_common():
            print(f"  {sc}: {count}")


if __name__ == "__main__":
    main()
