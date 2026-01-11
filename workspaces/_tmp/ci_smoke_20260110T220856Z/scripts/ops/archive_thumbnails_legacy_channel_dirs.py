#!/usr/bin/env python3
"""
Archive legacy thumbnail channel directories under workspaces/thumbnails/.

Target:
  workspaces/thumbnails/CHxx_*  (legacy, gitignored)
  workspaces/thumbnails/CHxx-*  (legacy, gitignored)

Rationale:
- Current SoT for thumbnails is:
    - workspaces/thumbnails/projects.json
    - workspaces/thumbnails/templates.json
    - workspaces/thumbnails/assets/<CHxx>/<NNN>/*
- Legacy "CHxx_<name>/" style directories are not referenced by current code/UI
  and add significant clutter. Keep them as an archive (not Git) for safety.

Safety:
- Default is dry-run.
- --run moves directories into workspaces/thumbnails/_archive/<timestamp>/...
- Respects coordination locks.
- Writes a JSON report under workspaces/logs/regression/.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402


REPORT_SCHEMA = "ytm.thumbnails.legacy_channel_dir_archive.v1"
LEGACY_DIR_RE = re.compile(r"^CH\d{2}[-_].+")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _move_dir(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise SystemExit(f"archive destination already exists: {dest}")
    try:
        src.rename(dest)
        return
    except OSError:
        shutil.move(str(src), str(dest))


@dataclass(frozen=True)
class Candidate:
    src: Path
    name: str
    size_bytes: int


def _dir_size_bytes(path: Path) -> int:
    total = 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except Exception:
            continue
    return total


def _fmt_bytes(num: int) -> str:
    n = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


def collect_candidates(thumbnails_root: Path) -> list[Candidate]:
    out: list[Candidate] = []
    if not thumbnails_root.exists():
        return out
    for p in sorted(thumbnails_root.iterdir()):
        if not p.is_dir():
            continue
        if p.name in {"assets", "_archive", "ui", "compiler"}:
            continue
        if not LEGACY_DIR_RE.match(p.name):
            continue
        out.append(Candidate(src=p, name=p.name, size_bytes=_dir_size_bytes(p)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="Actually move directories (default: dry-run).")
    ap.add_argument(
        "--archive-dir",
        help="Optional override for archive destination root (default: workspaces/thumbnails/_archive/<timestamp>).",
    )
    ap.add_argument("--max-print", type=int, default=50, help="Max candidates to print (default: 50).")
    ap.add_argument(
        "--ignore-locks",
        action="store_true",
        help="Do not respect coordination locks (dangerous; default: respect locks).",
    )
    args = ap.parse_args()

    thumbnails_root = repo_paths.thumbnails_root()
    candidates = collect_candidates(thumbnails_root)
    locks = [] if args.ignore_locks else default_active_locks_for_mutation()

    ts = _utc_now_compact()
    archive_root = Path(args.archive_dir).expanduser().resolve() if args.archive_dir else thumbnails_root / "_archive" / ts
    report_path = repo_paths.logs_root() / "regression" / "thumbnails_legacy_archive" / f"thumbnails_legacy_archive_{ts}.json"

    payload: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": ts,
        "mode": "run" if args.run else "dry-run",
        "ignore_locks": bool(args.ignore_locks),
        "thumbnails_root": str(thumbnails_root),
        "archive_root": str(archive_root),
        "candidates": [{"path": str(c.src), "name": c.name, "size_bytes": c.size_bytes} for c in candidates],
        "skipped_locked": [],
        "moves": [],
        "counters": {},
    }

    if not candidates:
        payload["counters"] = {"candidates": 0, "moved": 0, "skipped_locked": 0, "total_bytes": 0}
        _save_json(report_path, payload)
        print(f"[archive_thumbnails_legacy_channel_dirs] nothing to do report={report_path}")
        return 0

    total_bytes = sum(c.size_bytes for c in candidates)
    print(
        f"[archive_thumbnails_legacy_channel_dirs] candidates={len(candidates)} total={_fmt_bytes(total_bytes)} "
        f"dry_run={not args.run}"
    )

    max_print = max(0, int(args.max_print))
    if max_print:
        for c in sorted(candidates, key=lambda x: x.size_bytes, reverse=True)[:max_print]:
            print(f"  - {c.name} size={_fmt_bytes(c.size_bytes)} path={c.src}")
        if len(candidates) > max_print:
            print(f"  ... ({len(candidates) - max_print} more)")

    moved = 0
    skipped_locked = 0
    if args.run:
        for c in candidates:
            if locks and find_blocking_lock(c.src, locks):
                skipped_locked += 1
                payload["skipped_locked"].append({"path": str(c.src), "name": c.name})
                continue
            dest = archive_root / c.name
            _move_dir(c.src, dest)
            moved += 1
            payload["moves"].append({"src": str(c.src), "dest": str(dest), "size_bytes": c.size_bytes})

    payload["counters"] = {
        "candidates": len(candidates),
        "moved": moved,
        "skipped_locked": skipped_locked,
        "total_bytes": total_bytes,
    }
    _save_json(report_path, payload)
    print(f"[archive_thumbnails_legacy_channel_dirs] wrote report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
