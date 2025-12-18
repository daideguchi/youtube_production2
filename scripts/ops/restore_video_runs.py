#!/usr/bin/env python3
"""
restore_video_runs — Restore archived run dirs back to workspaces/video/runs.

This is a safety/recovery tool. It reverses an archive operation based on a JSON report:
- scripts/ops/cleanup_video_runs.py (schema: ytm.video_runs_cleanup_report.v1, key: moves[])
- scripts/episode_ssot.py archive-runs (schema: ytm.video_run_archive_report.v1, key: moved[])

Default is dry-run; pass --run to actually move directories back.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _move_dir(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise SystemExit(f"restore destination already exists: {dest}")
    try:
        src.rename(dest)
        return
    except OSError:
        import shutil

        shutil.move(str(src), str(dest))


def _iter_move_records(report: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(report.get("moves"), list):
        return [x for x in report["moves"] if isinstance(x, dict)]
    if isinstance(report.get("moved"), list):
        return [x for x in report["moved"] if isinstance(x, dict)]
    return []


def _record_run_id(rec: dict[str, Any]) -> str:
    for key in ("run_id", "archived_run_id"):
        v = rec.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    dest = rec.get("dest")
    if isinstance(dest, str) and dest.strip():
        return Path(dest).name
    src = rec.get("src")
    if isinstance(src, str) and src.strip():
        return Path(src).name
    return "(unknown)"


@dataclass(frozen=True)
class RestoreAction:
    run_id: str
    from_path: Path
    to_path: Path

    def as_dict(self) -> dict[str, Any]:
        return {"run_id": self.run_id, "from": str(self.from_path), "to": str(self.to_path)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Restore archived video run dirs based on an archive report (safe dry-run by default).")
    ap.add_argument("--report", required=True, help="Path to archive_report.json (from cleanup_video_runs or episode_ssot).")
    ap.add_argument("--run", action="store_true", help="Actually restore (move directories back).")
    ap.add_argument("--only-run-id", action="append", help="Restore only these run_id values (repeatable).")
    args = ap.parse_args()

    report_path = Path(args.report).expanduser().resolve()
    if not report_path.exists():
        raise SystemExit(f"report not found: {report_path}")

    report = _load_json(report_path)
    records = _iter_move_records(report)
    if not records:
        raise SystemExit("report has no moves (expected key: moves[] or moved[])")

    only = {str(x).strip() for x in (args.only_run_id or []) if str(x).strip()}

    actions: list[RestoreAction] = []
    for rec in records:
        src = rec.get("src")
        dest = rec.get("dest")
        if not (isinstance(src, str) and isinstance(dest, str)):
            continue
        run_id = _record_run_id(rec)
        if only and run_id not in only:
            continue
        actions.append(RestoreAction(run_id=run_id, from_path=Path(dest).expanduser().resolve(), to_path=Path(src).expanduser().resolve()))

    planned: list[dict[str, Any]] = []
    restored: list[dict[str, Any]] = []
    skipped: list[str] = []
    warnings: list[str] = []

    for a in actions:
        if not a.from_path.exists():
            skipped.append(f"{a.run_id}: missing archived dir: {a.from_path}")
            continue
        if a.to_path.exists():
            warnings.append(f"{a.run_id}: destination already exists: {a.to_path}")
            continue
        planned.append(a.as_dict())
        if args.run:
            try:
                _move_dir(a.from_path, a.to_path)
                restored.append(a.as_dict())
            except Exception as exc:
                warnings.append(f"{a.run_id}: restore failed: {exc}")

    out = {
        "schema": "ytm.video_runs_restore_report.v1",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "mode": "run" if args.run else "dry-run",
        "report": str(report_path),
        "only_run_id": sorted(only),
        "planned": planned,
        "restored": restored,
        "skipped": skipped,
        "warnings": warnings,
        "counts": {
            "records": len(records),
            "actions": len(actions),
            "planned": len(planned),
            "restored": len(restored),
            "skipped": len(skipped),
            "warnings": len(warnings),
        },
    }

    if args.run:
        dest = report_path.parent / f"restore_report_{_utc_now_compact()}.json"
        _save_json(dest, out)
        print(f"[restore_video_runs] mode=run restored={len(restored)} report={dest}")
    else:
        # Write next to stdout for review under logs/ (safe).
        # Avoid writing into archive roots on dry-run to reduce clutter.
        log_dir = REPO_ROOT / "workspaces" / "logs" / "regression"
        log_dir.mkdir(parents=True, exist_ok=True)
        dest = log_dir / f"restore_video_runs_dryrun_{_utc_now_compact()}.json"
        _save_json(dest, out)
        print(f"[restore_video_runs] mode=dry-run planned={len(planned)} report={dest}")

    return 0 if not warnings else 2


if __name__ == "__main__":
    raise SystemExit(main())
