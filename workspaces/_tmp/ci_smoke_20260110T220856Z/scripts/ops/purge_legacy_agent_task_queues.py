#!/usr/bin/env python3
"""
Purge legacy agent-task queue directories under logs_root().

Background:
- Current, canonical queue root is: workspaces/logs/agent_tasks/
- During early experiments, alternate roots were created (e.g. agent_tasks_ch04,
  agent_tasks_tmp, agent_tasks_test). They are no longer referenced by code and
  add confusion for operators and other agents.

This tool:
- Default dry-run: prints what would be removed.
- --run: archives the dirs to backups/graveyard/*.tar.gz (archive-first) then
  deletes the directories.
- Respects coordination locks unless --ignore-locks is set.
- Writes a JSON report under workspaces/logs/regression/.
"""

from __future__ import annotations

import argparse
import json
import tarfile
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402


REPORT_SCHEMA = "ytm.logs.legacy_agent_task_queues_purge.v1"
LEGACY_QUEUE_DIRNAMES = [
    "agent_tasks_ch04",
    "agent_tasks_tmp",
    "agent_tasks_test",
]


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class Candidate:
    path: Path
    name: str


def collect_candidates(logs_root: Path) -> list[Candidate]:
    out: list[Candidate] = []
    for name in LEGACY_QUEUE_DIRNAMES:
        p = logs_root / name
        if p.is_dir():
            out.append(Candidate(path=p, name=name))
    return out


def _archive_to_tar_gz(*, archive_path: Path, repo_root: Path, dirs: list[Path]) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tf:
        for d in dirs:
            rel = d
            try:
                rel = d.relative_to(repo_root)
            except Exception:
                # keep absolute (rare); still better than failing
                rel = d
            tf.add(str(d), arcname=str(rel))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="Actually purge legacy queue dirs (default: dry-run).")
    ap.add_argument(
        "--ignore-locks",
        action="store_true",
        help="Do not respect coordination locks (dangerous; default: respect locks).",
    )
    args = ap.parse_args()

    logs_root = repo_paths.logs_root()
    candidates = collect_candidates(logs_root)
    locks = [] if args.ignore_locks else default_active_locks_for_mutation()
    ts = _utc_now_compact()

    report_path = (
        logs_root
        / "regression"
        / "agent_tasks_legacy_purge"
        / f"agent_tasks_legacy_purge_{ts}.json"
    )
    archive_path = (repo_paths.repo_root() / "backups" / "graveyard" / f"{ts}_legacy_agent_task_queues.tar.gz").resolve()

    payload: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": ts,
        "mode": "run" if args.run else "dry-run",
        "ignore_locks": bool(args.ignore_locks),
        "logs_root": str(logs_root),
        "candidates": [{"path": str(c.path), "name": c.name} for c in candidates],
        "skipped_locked": [],
        "archive_path": str(archive_path),
        "deleted": [],
        "counters": {},
    }

    if not candidates:
        payload["counters"] = {"candidates": 0, "deleted": 0, "skipped_locked": 0}
        _save_json(report_path, payload)
        print(f"[purge_legacy_agent_task_queues] nothing to do report={report_path}")
        return 0

    print(f"[purge_legacy_agent_task_queues] candidates={len(candidates)} dry_run={not args.run}")
    for c in candidates:
        print(f"  - {c.name} path={c.path}")

    purge_targets: list[Candidate] = []
    skipped_locked = 0
    for c in candidates:
        if locks and find_blocking_lock(c.path, locks):
            skipped_locked += 1
            payload["skipped_locked"].append({"path": str(c.path), "name": c.name})
            continue
        purge_targets.append(c)

    if not args.run:
        payload["counters"] = {"candidates": len(candidates), "deleted": 0, "skipped_locked": skipped_locked}
        _save_json(report_path, payload)
        print(f"[purge_legacy_agent_task_queues] dry-run complete report={report_path}")
        return 0

    if not purge_targets:
        payload["counters"] = {"candidates": len(candidates), "deleted": 0, "skipped_locked": skipped_locked}
        _save_json(report_path, payload)
        print(f"[purge_legacy_agent_task_queues] all candidates locked; nothing deleted report={report_path}")
        return 0

    # archive-first
    _archive_to_tar_gz(archive_path=archive_path, repo_root=REPO_ROOT, dirs=[c.path for c in purge_targets])

    deleted = 0
    for c in purge_targets:
        try:
            shutil.rmtree(c.path)
            deleted += 1
            payload["deleted"].append({"path": str(c.path), "name": c.name})
        except Exception:
            continue

    payload["counters"] = {"candidates": len(candidates), "deleted": deleted, "skipped_locked": skipped_locked}
    _save_json(report_path, payload)
    print(f"[purge_legacy_agent_task_queues] deleted={deleted} archive={archive_path} report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
