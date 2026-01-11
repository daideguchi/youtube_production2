#!/usr/bin/env python3
"""
Aggregate VOICEVOX reading logs (workspaces/logs/tts_voicevox_reading.jsonl).

Usage:
    python scripts/aggregate_voicevox_reading_logs.py [log_path] [--top 20]

Outputs:
    - total records
    - count by source (ruby_llm / vocab_llm / etc.)
    - count by reason (hazard / align_fallback / budget_exceeded... )
    - fallback ratio for ruby patches (align_fallback / align_missing)
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Any

try:
    from factory_common.paths import logs_root

    DEFAULT_LOG_PATH = str(logs_root() / "tts_voicevox_reading.jsonl")
except Exception:
    DEFAULT_LOG_PATH = "workspaces/logs/tts_voicevox_reading.jsonl"


def load_records(path: Path) -> list[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"log not found: {path}")
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except Exception:
                # skip malformed
                continue
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("log_path", nargs="?", default=DEFAULT_LOG_PATH)
    parser.add_argument("--top", type=int, default=20, help="Top N reasons to display")
    args = parser.parse_args()

    log_path = Path(args.log_path)
    recs = load_records(log_path)
    if not recs:
        print(f"No records in {log_path}")
        return

    by_source = Counter(r.get("source", "unknown") for r in recs)
    reasons = Counter(r.get("reason", "unknown") for r in recs)

    fallback = Counter()
    total_ruby = 0
    for r in recs:
        if r.get("source") != "ruby_llm":
            continue
        total_ruby += 1
        reason = str(r.get("reason", ""))
        if "align_fallback" in reason:
            fallback["align_fallback"] += 1
        if "align_missing" in reason:
            fallback["align_missing"] += 1

    print(f"Log: {log_path}")
    print(f"Total records: {len(recs)}")
    print("\nBy source:")
    for src, cnt in by_source.most_common():
        print(f"  {src:15s} {cnt}")

    print(f"\nTop {args.top} reasons:")
    for reason, cnt in reasons.most_common(args.top):
        print(f"  {reason:30s} {cnt}")

    if total_ruby:
        ratio_fallback = fallback["align_fallback"] / total_ruby
        ratio_missing = fallback["align_missing"] / total_ruby
        print("\nRuby alignment fallback summary:")
        print(f"  total_ruby_records: {total_ruby}")
        print(f"  align_fallback: {fallback['align_fallback']} ({ratio_fallback:.2%})")
        print(f"  align_missing:  {fallback['align_missing']} ({ratio_missing:.2%})")


if __name__ == "__main__":
    main()
