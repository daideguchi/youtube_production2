#!/usr/bin/env python3
"""
antigravity_clear_brain.py — Antigravity "memory" cleanup (safe by default).

Goal:
- Prevent Antigravity/Gemini-script runs from accumulating "memory" artifacts that
  bloat context and increase failure rates.

This tool intentionally deletes ONLY rebuildable/derived artifacts:
- workspaces/scripts/_state/antigravity*.json
    - derived progress/manifests (NOT SSOT)
- workspaces/_scratch/gemini_batch_scripts/<run_dir>/
    - Gemini Batch submit/fetch scratch (JSONL + manifest)
    - by default, delete only when the run looks "done" (all output files exist)

Default is dry-run. Use --run to actually delete.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402


WORKSPACES = repo_paths.workspace_root()


def _now_ts() -> float:
    return time.time()


def _latest_mtime(path: Path) -> float:
    try:
        st = path.stat()
        latest = float(st.st_mtime)
    except Exception:
        return 0.0
    if path.is_dir():
        try:
            for p in path.rglob("*"):
                try:
                    latest = max(latest, float(p.stat().st_mtime))
                except Exception:
                    continue
        except Exception:
            return latest
    return latest


def _is_recent(path: Path, *, keep_recent_seconds: int) -> bool:
    if keep_recent_seconds <= 0:
        return False
    latest = _latest_mtime(path)
    if latest <= 0:
        return False
    return (_now_ts() - latest) < float(keep_recent_seconds)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


@dataclass(frozen=True)
class PlanItem:
    path: Path
    kind: str
    action: str  # delete|skip
    reason: str


def _iter_antigravity_state_files(state_dir: Path) -> Iterable[Path]:
    if not state_dir.exists():
        return []
    try:
        return sorted([p for p in state_dir.glob("antigravity*.json") if p.is_file()])
    except Exception:
        return []


def _scratch_run_done(*, manifest: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Heuristic:
    - We cannot rely on manifest.job.state (it is written at submit time).
    - Consider the run "done" when every item.output_path exists and is non-empty.
    """
    schema = str(manifest.get("schema") or "").strip()
    if schema != "ytm.gemini_batch_scripts.v1":
        return False, f"unsupported schema: {schema or 'missing'}"

    items = manifest.get("items") or []
    if not isinstance(items, list) or not items:
        return False, "manifest has no items"

    out_paths: List[Path] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        raw = str(it.get("output_path") or "").strip()
        if raw:
            out_paths.append(Path(raw))

    if not out_paths:
        return False, "manifest items missing output_path"

    missing = 0
    empty = 0
    for p in out_paths:
        try:
            if not p.exists():
                missing += 1
                continue
            if p.is_file() and p.stat().st_size <= 0:
                empty += 1
        except Exception:
            missing += 1

    if missing == 0 and empty == 0:
        return True, "all outputs exist"
    return False, f"outputs incomplete (missing={missing} empty={empty} total={len(out_paths)})"


def _plan_scratch_dir(
    run_dir: Path,
    *,
    include_incomplete: bool,
    keep_recent_seconds: int,
) -> List[PlanItem]:
    if _is_recent(run_dir, keep_recent_seconds=keep_recent_seconds):
        return [PlanItem(path=run_dir, kind="scratch", action="skip", reason="recently modified")]

    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        if include_incomplete:
            return [PlanItem(path=run_dir, kind="scratch", action="delete", reason="missing manifest.json (forced)")]
        return [PlanItem(path=run_dir, kind="scratch", action="skip", reason="missing manifest.json")]

    manifest = _read_json(manifest_path)
    if manifest is None:
        if include_incomplete:
            return [PlanItem(path=run_dir, kind="scratch", action="delete", reason="unreadable manifest.json (forced)")]
        return [PlanItem(path=run_dir, kind="scratch", action="skip", reason="unreadable manifest.json")]

    done, reason = _scratch_run_done(manifest=manifest)
    if done:
        return [PlanItem(path=run_dir, kind="scratch", action="delete", reason=reason)]
    if include_incomplete:
        return [PlanItem(path=run_dir, kind="scratch", action="delete", reason=f"{reason} (forced)")]
    return [PlanItem(path=run_dir, kind="scratch", action="skip", reason=reason)]


def build_plan(
    *,
    include_state: bool,
    include_scratch: bool,
    include_incomplete_scratch: bool,
    keep_recent_minutes: int,
) -> List[PlanItem]:
    keep_recent_seconds = max(0, int(keep_recent_minutes) * 60)
    plan: List[PlanItem] = []

    if include_state:
        state_dir = WORKSPACES / "scripts" / "_state"
        for p in _iter_antigravity_state_files(state_dir):
            if _is_recent(p, keep_recent_seconds=keep_recent_seconds):
                plan.append(PlanItem(path=p, kind="state", action="skip", reason="recently modified"))
            else:
                plan.append(PlanItem(path=p, kind="state", action="delete", reason="derived antigravity state"))

    if include_scratch:
        scratch_root = WORKSPACES / "_scratch" / "gemini_batch_scripts"
        if scratch_root.exists() and scratch_root.is_dir():
            try:
                children = sorted([p for p in scratch_root.iterdir() if p.is_dir()])
            except Exception:
                children = []
            for run_dir in children:
                plan.extend(
                    _plan_scratch_dir(
                        run_dir,
                        include_incomplete=bool(include_incomplete_scratch),
                        keep_recent_seconds=keep_recent_seconds,
                    )
                )

    # Stable ordering for predictable diffs/logs.
    return sorted(plan, key=lambda x: (x.action != "delete", x.kind, str(x.path)))


def _delete_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
        return
    path.unlink()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Antigravity memory cleanup (safe by default; dry-run).")
    ap.add_argument("--run", action="store_true", help="Actually delete (default: dry-run).")
    ap.add_argument("--dry-run", action="store_true", help="Dry-run (default).")
    ap.add_argument("--no-state", action="store_true", help="Do not delete workspaces/scripts/_state/antigravity*.json")
    ap.add_argument("--no-scratch", action="store_true", help="Do not delete workspaces/_scratch/gemini_batch_scripts/*")
    ap.add_argument(
        "--include-incomplete-scratch",
        action="store_true",
        help="Also delete scratch runs that do not look done (skips recent files still apply).",
    )
    ap.add_argument(
        "--keep-recent-minutes",
        type=int,
        default=60,
        help="Skip paths modified within this many minutes (default: 60). Use 0 to disable.",
    )
    ap.add_argument("--max-print", type=int, default=200, help="Max lines to print (default: 200). Use 0 for unlimited.")
    args = ap.parse_args(argv)

    do_run = bool(args.run) and not bool(args.dry_run)
    keep_recent_minutes = int(args.keep_recent_minutes)
    plan = build_plan(
        include_state=not bool(args.no_state),
        include_scratch=not bool(args.no_scratch),
        include_incomplete_scratch=bool(args.include_incomplete_scratch),
        keep_recent_minutes=keep_recent_minutes,
    )

    max_print = int(args.max_print)
    printed = 0
    deletes = [it for it in plan if it.action == "delete"]
    skips = [it for it in plan if it.action == "skip"]

    mode = "RUN" if do_run else "DRY"
    print(f"[{mode}] antigravity_clear_brain plan: delete={len(deletes)} skip={len(skips)} keep_recent_minutes={keep_recent_minutes}")

    def _print(line: str) -> None:
        nonlocal printed
        if max_print == 0 or printed < max_print:
            print(line)
        printed += 1

    for it in plan:
        tag = "DEL" if it.action == "delete" else "SKIP"
        _print(f"[{tag}] {it.kind} {it.path} — {it.reason}")

    if not do_run:
        return 0

    failed = 0
    for it in deletes:
        try:
            _delete_path(it.path)
        except Exception as exc:
            failed += 1
            print(f"[ERROR] failed to delete {it.path}: {exc}", file=sys.stderr)

    if failed:
        print(f"[DONE] failures={failed} (see stderr)", file=sys.stderr)
        return 2
    print("[DONE] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
