#!/usr/bin/env python3
"""
L3 ログ（短期保持）のクリーンアップ。

目的:
- ログの増殖で探索が重くなるのを防ぐ
- L1（監査/使用量/DB/agentキュー）には触れない

安全のため:
- default は dry-run（削除しない）
- `--run` 指定時のみ削除する
- いつ何を削除したかの追跡のため、JSON report を `workspaces/logs/regression/logs_cleanup/` に出力する

SSOT:
- `ssot/ops/OPS_LOGGING_MAP.md`
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock


REPORT_SCHEMA = "ytm.logs_cleanup_report.v1"


@dataclass(frozen=True)
class DeletionCandidate:
    path: Path
    reason: str


KEEP_ALWAYS_FILENAMES = {
    # L1 usage / audit
    "llm_usage.jsonl",
    "image_usage.log",
    "tts_llm_usage.log",
    "tts_voicevox_reading.jsonl",
    "audit_global_execution.log",
    "audit_report_global.txt",
    # state / db
    "image_rr_state.json",
    "lock_metrics.db",
    "ui_tasks.db",
}

KEEP_ALWAYS_SUFFIXES = {
    ".db",
    ".jsonl",
}

SKIP_DIRS = {
    # queue/coordination SoT (do not purge here; separate tool later)
    "agent_tasks",
    "agent_tasks_ch04",
    "agent_tasks_test",
    "agent_tasks_tmp",
    # UI queue SoT/configs (do not purge here)
    "queue_configs",
    "queue_progress",
}


def _now() -> datetime:
    return datetime.now()


def _is_older_than(path: Path, cutoff: datetime) -> bool:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime) < cutoff
    except Exception:
        return False


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        # don't walk into agent queues, etc.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            out.append(dp / name)
    return out


def _should_keep(path: Path) -> bool:
    if path.name in KEEP_ALWAYS_FILENAMES:
        return True
    if path.name.endswith("__latest.json") or path.name.endswith("__latest.md"):
        return True
    # ops_cli keep-latest pointers (L1; used for "what is the latest run?" lookups)
    if "ops_cli" in path.parts and "latest" in path.parts:
        return True
    if path.suffix in KEEP_ALWAYS_SUFFIXES:
        return True
    if path.suffix == ".pid":
        return True
    parts = set(path.parts)
    if parts & SKIP_DIRS:
        return True
    return False


def collect_candidates(
    *,
    keep_days: int,
    include_llm_api_cache: bool,
    ignore_locks: bool,
) -> tuple[list[DeletionCandidate], list[Path]]:
    keep_days = int(keep_days)
    cutoff = _now() - timedelta(days=keep_days)
    logs_root = repo_paths.logs_root()

    candidates: list[DeletionCandidate] = []
    locks = [] if ignore_locks else default_active_locks_for_mutation()
    skipped_locked: list[Path] = []

    # Root-level L3 files
    for p in sorted(logs_root.iterdir() if logs_root.exists() else []):
        if not p.is_file():
            continue
        if _should_keep(p):
            continue
        if p.suffix not in {".log", ".out", ".txt", ".json", ".md", ".png"}:
            continue
        if _is_older_than(p, cutoff):
            if locks and find_blocking_lock(p, locks):
                skipped_locked.append(p)
                continue
            candidates.append(DeletionCandidate(p, f"logs_root_file_older_than_{keep_days}d"))

    # Known L3 subdirectories
    for rel in ("repair", "swap", "regression", "ui_hub", "ops", "ui"):
        base = logs_root / rel
        for p in sorted(_iter_files(base)):
            if _should_keep(p):
                continue
            allowed_suffixes = {".log", ".out", ".txt", ".json", ".md"}
            if rel == "swap":
                allowed_suffixes |= {".png"}
            if p.suffix not in allowed_suffixes:
                continue
            if _is_older_than(p, cutoff):
                if locks and find_blocking_lock(p, locks):
                    skipped_locked.append(p)
                    continue
                candidates.append(DeletionCandidate(p, f"{rel}_older_than_{keep_days}d"))

    # Optional: LLM API cache (safe to rebuild)
    if include_llm_api_cache:
        base = logs_root / "llm_api_cache"
        for p in sorted(_iter_files(base)):
            if _should_keep(p):
                continue
            if _is_older_than(p, cutoff):
                if locks and find_blocking_lock(p, locks):
                    skipped_locked.append(p)
                    continue
                candidates.append(DeletionCandidate(p, f"llm_api_cache_older_than_{keep_days}d"))

    return candidates, skipped_locked


def _delete_file(path: Path) -> None:
    path.unlink(missing_ok=True)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _rel(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except Exception:
        return str(path)


def _write_report(payload: dict[str, Any]) -> Path:
    out_dir = repo_paths.logs_root() / "regression" / "logs_cleanup"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"logs_cleanup_{_utc_now_compact()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup L3 logs under logs_root (safe dry-run by default)")
    parser.add_argument("--run", action="store_true", help="Actually delete files (default: dry-run)")
    parser.add_argument("--keep-days", type=int, default=30, help="Keep files newer than this many days (default: 30)")
    parser.add_argument("--max-print", type=int, default=40, help="Max candidates to print (default: 40).")
    parser.add_argument(
        "--include-llm-api-cache",
        action="store_true",
        help="Also prune workspaces/logs/llm_api_cache (default: keep)",
    )
    parser.add_argument(
        "--ignore-locks",
        action="store_true",
        help="Do not respect coordination locks (dangerous; default: respect locks).",
    )
    args = parser.parse_args()

    keep_days = int(args.keep_days)
    if keep_days < 1:
        raise SystemExit("--keep-days must be >= 1")

    candidates, skipped_locked = collect_candidates(
        keep_days=keep_days,
        include_llm_api_cache=bool(args.include_llm_api_cache),
        ignore_locks=bool(args.ignore_locks),
    )
    max_print = max(0, int(args.max_print))

    payload: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "created_at": _utc_now_iso(),
        "mode": "run" if args.run else "dry_run",
        "keep_days": keep_days,
        "include_llm_api_cache": bool(args.include_llm_api_cache),
        "ignore_locks": bool(args.ignore_locks),
        "counts": {
            "candidates": len(candidates),
            "skipped_locked": len(skipped_locked),
            "deleted": 0,
            "failed": 0,
        },
        "candidates": [{"path": _rel(c.path), "reason": c.reason} for c in candidates],
        "skipped_locked": [_rel(p) for p in skipped_locked],
        "deleted": [],
        "failed": [],
    }

    print(f"[cleanup_logs] candidates={len(candidates)} skipped_locked={len(skipped_locked)} dry_run={not args.run}")
    if max_print and candidates:
        for i, c in enumerate(candidates[:max_print], start=1):
            prefix = "[RUN]" if args.run else "[DRY]"
            print(f"{prefix} {i:>4}/{len(candidates)} {_rel(c.path)}  ({c.reason})")
        if len(candidates) > max_print:
            print(f"... ({len(candidates) - max_print} more)")

    if not args.run:
        report_path = _write_report(payload)
        print(f"[cleanup_logs] dry-run complete report={report_path}")
        return 0

    deleted: list[str] = []
    failed: list[dict[str, str]] = []
    for c in candidates:
        try:
            _delete_file(c.path)
            deleted.append(_rel(c.path))
        except Exception as exc:
            failed.append({"path": _rel(c.path), "error": str(exc)})

    payload["counts"]["deleted"] = len(deleted)
    payload["counts"]["failed"] = len(failed)
    payload["deleted"] = deleted
    payload["failed"] = failed

    report_path = _write_report(payload)
    msg = f"[cleanup_logs] deleted={len(deleted)} planned={len(candidates)} report={report_path}"
    if failed:
        msg += f" failed={len(failed)}"
    if skipped_locked:
        msg += f" skipped_locked={len(skipped_locked)}"
    print(msg)
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
