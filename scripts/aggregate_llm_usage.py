#!/usr/bin/env python3
"""Aggregate LLM router usage logs (logs/llm_usage.jsonl by default)."""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path


def load_logs(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Log file not found: {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except Exception:
            continue


def main():
    ap = argparse.ArgumentParser(description="Aggregate llm_usage.jsonl")
    ap.add_argument("--log", default="logs/llm_usage.jsonl", help="Path to llm_usage.jsonl")
    ap.add_argument("--top", type=int, default=10, help="Top N models/tasks to show")
    args = ap.parse_args()

    path = Path(args.log)
    records = list(load_logs(path))
    if not records:
        print("No records found")
        return 0

    model_cnt = Counter()
    task_cnt = Counter()
    fail_cnt = Counter()
    latency = defaultdict(list)

    for r in records:
        status = r.get("status")
        model = r.get("model")
        task = r.get("task")
        if status == "success":
            model_cnt[model] += 1
            task_cnt[task] += 1
            if "latency_ms" in r:
                latency[model].append(r["latency_ms"])
        else:
            fail_cnt[task] += 1

    def avg(xs):
        return sum(xs) / len(xs) if xs else 0

    print("=== Top models by success count ===")
    for m, c in model_cnt.most_common(args.top):
        print(f"{m:30s} {c:6d} avg_latency={avg(latency[m]):.1f}ms")

    print("\n=== Top tasks by success count ===")
    for t, c in task_cnt.most_common(args.top):
        print(f"{t:30s} {c:6d}")

    print("\n=== Failures by task ===")
    for t, c in fail_cnt.most_common():
        print(f"{t:30s} {c:6d}")

    # Fallback chain stats
    chain_cnt = Counter()
    for r in records:
        chain = r.get("chain")
        if chain:
            chain_cnt[tuple(chain)] += 1
    print("\n=== Top fallback chains ===")
    for ch, c in chain_cnt.most_common(args.top):
        print(f"{list(ch)} -> {c}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
