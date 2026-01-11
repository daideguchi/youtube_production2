#!/usr/bin/env python3
"""
Import CH01 legacy A-text scripts (>=200) into this repo's workspace.

Scope:
- CH01 only
- text-only (does NOT import audio/video/images)
- writes: workspaces/scripts/CH01/{NNN}/content/assembled.md

Default is dry-run; use --run to write files.

Example:
  python3 scripts/ops/import_ch01_legacy_scripts.py \
    --src-root "/path/to/legacy/CH01_project_root" \
    --run
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.paths import channels_csv_path, logs_root, script_data_root  # noqa: E402
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402


REPORT_SCHEMA = "ytm.ops.import_ch01_legacy_scripts.v1"


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _save_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


_RE_SCRIPT = re.compile(r"^(?P<num>\d+)_script(?P<v2>_v2)?\.txt$")


@dataclass(frozen=True)
class Candidate:
    num: int
    path: Path
    variant: str  # "v2" | "base"


def _load_planning_numbers(csv_path: Path) -> set[int]:
    if not csv_path.exists():
        return set()
    out: set[int] = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = (row.get("動画番号") or row.get("No.") or "").strip()
            if not raw:
                continue
            try:
                out.add(int(raw))
            except Exception:
                continue
    return out


def _discover_candidates(legacy_scripts_dir: Path) -> list[Candidate]:
    out: list[Candidate] = []
    for p in sorted(legacy_scripts_dir.glob("*_script*.txt")):
        m = _RE_SCRIPT.match(p.name)
        if not m:
            continue
        try:
            num = int(m.group("num"))
        except Exception:
            continue
        out.append(Candidate(num=num, path=p, variant="v2" if m.group("v2") else "base"))
    return out


def _choose_best(candidates: list[Candidate], *, prefer_v2: bool) -> dict[int, Candidate]:
    best: dict[int, Candidate] = {}
    for cand in candidates:
        cur = best.get(cand.num)
        if cur is None:
            best[cand.num] = cand
            continue
        if prefer_v2 and cand.variant == "v2" and cur.variant != "v2":
            best[cand.num] = cand
            continue
        if not prefer_v2 and cand.variant == "base" and cur.variant != "base":
            best[cand.num] = cand
            continue
    return best


def _normalize_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def main() -> int:
    ap = argparse.ArgumentParser(description="Import CH01 legacy scripts into workspaces/scripts/CH01 (text only).")
    ap.add_argument(
        "--src-root",
        type=Path,
        required=True,
        help="Legacy CH01 project root (contains scripts/).",
    )
    ap.add_argument("--min-video", type=int, default=200, help="Minimum video number to import (default: 200).")
    ap.add_argument("--max-video", type=int, default=None, help="Maximum video number to import (optional).")
    ap.add_argument("--prefer-v2", action="store_true", help="Prefer *_script_v2.txt when both exist (default: true).")
    ap.add_argument("--no-prefer-v2", dest="prefer_v2", action="store_false", help="Prefer base *_script.txt instead.")
    ap.set_defaults(prefer_v2=True)
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing assembled.md (default: skip).")
    ap.add_argument("--run", action="store_true", help="Actually write files (default: dry-run).")
    ap.add_argument("--write-latest", action="store_true", help="Also write a *_latest.json report pointer.")
    args = ap.parse_args()

    src_root = args.src_root.expanduser().resolve()
    legacy_scripts_dir = src_root / "scripts"
    if not legacy_scripts_dir.exists():
        print(f"[import_ch01_legacy_scripts] missing legacy scripts dir: {legacy_scripts_dir}")
        return 2

    planning_csv = channels_csv_path("CH01")
    planning_numbers = _load_planning_numbers(planning_csv)

    discovered = _discover_candidates(legacy_scripts_dir)
    best = _choose_best(discovered, prefer_v2=bool(args.prefer_v2))

    min_no = int(args.min_video)
    max_no = int(args.max_video) if args.max_video is not None else None

    actions: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    locks = default_active_locks_for_mutation()
    do_run = bool(args.run)
    base_out = script_data_root() / "CH01"

    for num in sorted(best.keys()):
        if num < min_no:
            continue
        if max_no is not None and num > max_no:
            continue
        cand = best[num]

        if planning_numbers and num not in planning_numbers:
            skipped.append({"num": num, "reason": "not_in_planning_csv", "source": str(cand.path)})
            continue

        dest = base_out / f"{num:03d}" / "content" / "assembled.md"
        if dest.exists() and not args.overwrite:
            skipped.append({"num": num, "reason": "exists", "dest": str(dest), "source": str(cand.path)})
            continue

        blocker = find_blocking_lock(dest, locks)
        if blocker:
            skipped.append(
                {
                    "num": num,
                    "reason": "blocked_by_lock",
                    "dest": str(dest),
                    "source": str(cand.path),
                    "lock_id": blocker.lock_id,
                    "lock_scopes": list(blocker.scopes),
                }
            )
            continue

        action = {"num": num, "source": str(cand.path), "variant": cand.variant, "dest": str(dest)}
        if do_run:
            dest.parent.mkdir(parents=True, exist_ok=True)
            raw = cand.path.read_text(encoding="utf-8")
            dest.write_text(_normalize_text(raw), encoding="utf-8")
            action["written"] = True
        else:
            action["written"] = False
        actions.append(action)

    stamp = _utc_now_compact()
    report_dir = logs_root() / "regression" / "import_ch01_legacy_scripts"
    report_path = report_dir / f"import_ch01_legacy_scripts_{stamp}.json"
    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": not do_run,
        "src_root": str(src_root),
        "legacy_scripts_dir": str(legacy_scripts_dir),
        "planning_csv": str(planning_csv),
        "min_video": min_no,
        "max_video": max_no,
        "prefer_v2": bool(args.prefer_v2),
        "overwrite": bool(args.overwrite),
        "counts": {"candidates": len(discovered), "selected": len(best), "actions": len(actions), "skipped": len(skipped)},
        "actions": actions,
        "skipped": skipped,
    }
    _save_json(report_path, report)
    if args.write_latest:
        _save_json(report_dir / "import_ch01_legacy_scripts_latest.json", report)

    print(f"[import_ch01_legacy_scripts] report: {report_path}")
    print(f"[import_ch01_legacy_scripts] actions={len(actions)} skipped={len(skipped)} mode={'run' if do_run else 'dry-run'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
