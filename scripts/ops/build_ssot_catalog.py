#!/usr/bin/env python3
"""
build_ssot_catalog — build the machine-readable SSOT catalog for UI visualization.

Usage:
  python3 scripts/ops/build_ssot_catalog.py
  python3 scripts/ops/build_ssot_catalog.py --json
  python3 scripts/ops/build_ssot_catalog.py --write
  python3 scripts/ops/build_ssot_catalog.py --check

Notes:
- Intended to be safe to run locally (writes only under workspaces/logs/ssot/ when --write).
- The UI reads the same schema via /api/ssot/catalog (read-only).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from _bootstrap import bootstrap

from factory_common.ssot_catalog import CATALOG_SCHEMA_V1, build_ssot_catalog

REPO_ROOT = bootstrap(load_env=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build SSOT catalog (code-derived).")
    ap.add_argument("--json", action="store_true", help="Print JSON to stdout.")
    ap.add_argument("--write", action="store_true", help="Write JSON under workspaces/logs/ssot/.")
    ap.add_argument("--check", action="store_true", help="Fail (exit=1) if catalog has missing task defs.")
    args = ap.parse_args(argv)

    cat = build_ssot_catalog()
    if cat.get("schema") != CATALOG_SCHEMA_V1:
        raise SystemExit(f"Unexpected schema: {cat.get('schema')}")

    if args.write:
        out_dir = REPO_ROOT / "workspaces" / "logs" / "ssot"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "ssot_catalog_latest.json"
        out_path.write_text(json.dumps(cat, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[ssot_catalog] wrote: {out_path}")

    if args.json and not args.write:
        print(json.dumps(cat, ensure_ascii=False, indent=2))

    missing = cat.get("llm", {}).get("missing_task_defs") or []
    if args.check and missing:
        print("[FAIL] missing llm task definitions in configs (used in code but not declared):")
        for t in missing:
            print(f"- {t}")
        return 1

    if missing:
        print(f"[warn] missing_task_defs={len(missing)} (run with --check to fail): {missing[:10]}")
    else:
        print("[ok] missing_task_defs=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

