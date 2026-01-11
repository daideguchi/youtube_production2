from __future__ import annotations
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict

try:
    from factory_common.paths import logs_root

    DEFAULT_LOG_PATH = str(logs_root() / "image_usage.log")
except Exception:
    DEFAULT_LOG_PATH = "workspaces/logs/image_usage.log"


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
    duration = defaultdict(list)
    errors = Counter()
    for rec in load(path):
        success = rec.get("success")
        model = rec.get("model") or "(none)"
        status = "success" if success else "fail"
        totals[status] += 1
        if model:
            by_model[(model, status)] += 1
        dur = rec.get("duration_ms")
        if isinstance(dur, (int, float)):
            duration[status].append(dur)
        for err in rec.get("errors", []) or []:
            errors[err.get("model") or "(unknown)"] += 1
    return totals, by_model, duration, errors


def pct(part, whole):
    return 0.0 if not whole else round(part * 100.0 / whole, 2)


def main():
    ap = argparse.ArgumentParser(description="Summarize image_usage.log")
    ap.add_argument("--log", default=DEFAULT_LOG_PATH, help="path to log file")
    args = ap.parse_args()
    log_path = Path(args.log)
    totals, by_model, duration, errors = summarize(log_path)
    total = sum(totals.values())
    print(f"Log: {log_path}")
    print(f"Total records: {total}")
    for status in ("success", "fail"):
        count = totals[status]
        durs = duration.get(status, [])
        avg = round(sum(durs) / len(durs), 1) if durs else 0
        print(f"  {status}: {count} ({pct(count, total)}%), avg_latency_ms={avg}")
    if by_model:
        print("\nBy model:")
        for (model, status), count in by_model.most_common():
            print(f"  {model} {status}: {count}")
    if errors:
        print("\nErrors by model:")
        for model, count in errors.most_common():
            print(f"  {model}: {count}")


if __name__ == "__main__":
    main()
