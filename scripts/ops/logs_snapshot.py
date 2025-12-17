#!/usr/bin/env python3
"""
logs_snapshot — logs_root の現状スナップショット（件数/サイズ）を出す。

目的:
- どこに何が溜まっているかを“観測値”として素早く把握する
- cleanup の優先度付けや、SSOT更新（OPS_LOGGING_MAP）の材料にする

注意:
- これは観測用であり、L1/L3の確定ルールは SSOT を正とする
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factory_common import paths as repo_paths


def _human_mb(num_bytes: int) -> str:
    return f"{num_bytes / 1024 / 1024:.2f} MB"


def main() -> int:
    ap = argparse.ArgumentParser(description="Snapshot counts/sizes under logs_root()")
    ap.add_argument("--top", type=int, default=15, help="Top N files by size to print (default: 15)")
    args = ap.parse_args()

    logs_root = repo_paths.logs_root()
    if not logs_root.exists():
        print(f"[logs_snapshot] logs_root not found: {logs_root}")
        return 0

    files: list[Path] = [p for p in logs_root.rglob("*") if p.is_file()]
    print(f"logs_root: {logs_root}")
    print(f"file_count: {len(files)}")

    counts = Counter()
    for p in files:
        rel = p.relative_to(logs_root)
        top = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        counts[top] += 1

    print("\nfile_count_by_top_level:")
    for name, cnt in counts.most_common():
        print(f"- {name}: {cnt}")

    top_n = max(0, int(args.top))
    if top_n:
        print(f"\ntop_by_size (top {top_n}):")
        files_sorted = sorted(files, key=lambda p: p.stat().st_size, reverse=True)
        for p in files_sorted[:top_n]:
            try:
                size = p.stat().st_size
            except Exception:
                continue
            rel = p.relative_to(logs_root)
            # keep output aligned with SSOT notation: "logs/..." == logs_root()/...
            print(f"- {_human_mb(size):>10}  logs/{rel}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

