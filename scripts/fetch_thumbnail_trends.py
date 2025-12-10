#!/usr/bin/env python3
"""Fetch thumbnail trend candidates from configured sources."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from trend_feed import load_feed, load_source_candidates, save_feed, upsert_entries


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update thumbnail trend feed from configured sources.")
    parser.add_argument(
        "--sources",
        type=Path,
        default=None,
        help="Optional path to JSON file listing trend sources (list[dict]).",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="Print resulting feed to stdout after update.",
    )
    return parser.parse_args()


def load_sources_from(path: Path | None) -> list[dict]:
    if path is None:
        return load_source_candidates()
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Expected list in {path}")
    return data


def main() -> None:
    args = parse_args()
    feed = load_feed()
    sources = load_sources_from(args.sources)
    inserted = upsert_entries(feed, sources)
    save_feed(feed)
    print(f"Trend feed updated: {inserted} new entries (total {len(feed.get('items', []))})")
    if args.dump:
        print(json.dumps(feed, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
