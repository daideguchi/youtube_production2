#!/usr/bin/env python3
"""
Quick utility: find LLM task/model references across the repo.
Purpose: identify call sites for phased migration to new LLM client.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).resolve().parents[2]


def search(patterns: Dict[str, str], exts=(".py", ".js", ".ts", ".tsx")) -> Dict[str, List[str]]:
    results: Dict[str, List[str]] = {k: [] for k in patterns}
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in exts:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for key, patt in patterns.items():
            for m in re.finditer(patt, text):
                line_no = text.count("\n", 0, m.start()) + 1
                results[key].append(f"{path.relative_to(ROOT)}:{line_no}")
    return results


def main():
    patterns = {
        "get_router_call": r"get_router\(\)",
        "llm_registry_json": r"llm_registry\.json",
        "llm_router_yaml": r"llm_router\.yaml",
    }
    res = search(patterns)
    for key, hits in res.items():
        print(f"== {key} ({len(hits)})")
        for h in hits[:50]:
            print("  ", h)
        if len(hits) > 50:
            print("  ...")


if __name__ == "__main__":
    main()
