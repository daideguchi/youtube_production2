#!/usr/bin/env python3
"""
cleanup_broken_symlinks — 壊れたsymlinkを安全に削除して探索ノイズを減らす。

対象（デフォルト）:
- workspaces/video/runs/**/capcut_draft
- workspaces/video/_archive/**/capcut_draft

安全のため:
- default は dry-run（削除しない）
- `--run` 指定時のみ unlink（symlinkのみ）
- coordination locks を尊重（lock対象は skip）

SSOT:
- `ssot/ops/OPS_IO_SCHEMAS.md`
- `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
- `ssot/ops/OPS_LOGGING_MAP.md`
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402


REPORT_SCHEMA = "ytm.broken_symlinks_cleanup_report.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_readlink(path: Path) -> Optional[str]:
    try:
        return os.readlink(path)
    except OSError:
        return None


def _resolve_link_target(link_path: Path, link_target: str) -> Path:
    raw = Path(link_target)
    if raw.is_absolute():
        return raw
    return (link_path.parent / raw).resolve()


@dataclass(frozen=True)
class BrokenSymlink:
    path: Path
    link_target: Optional[str]
    resolved_target: Optional[Path]
    root: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "link_target": self.link_target,
            "resolved_target": str(self.resolved_target) if self.resolved_target else None,
        }


def _iter_symlink_entries(root: Path, name: str) -> Iterable[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dp = Path(dirpath)
        if name in dirnames:
            out.append(dp / name)
        if name in filenames:
            out.append(dp / name)
    return out


def collect_candidates(
    *,
    symlink_name: str,
    include_runs: bool,
    include_archive: bool,
    include_episodes: bool,
    ignore_locks: bool,
    path_globs: list[str],
) -> tuple[list[BrokenSymlink], list[dict[str, Any]]]:
    candidates: list[BrokenSymlink] = []
    skipped_locked: list[dict[str, Any]] = []
    locks = [] if ignore_locks else default_active_locks_for_mutation()

    roots: list[Path] = []
    ws = repo_paths.workspace_root()
    if include_runs:
        roots.append(ws / "video" / "runs")
    if include_archive:
        roots.append(ws / "video" / "_archive")
    if include_episodes:
        roots.append(ws / "episodes")

    for root in roots:
        for p in _iter_symlink_entries(root, symlink_name):
            if not p.is_symlink():
                continue
            # Path.exists() follows the link; broken links return False.
            if p.exists():
                continue
            if path_globs:
                try:
                    rel = p.absolute().relative_to(repo_paths.repo_root()).as_posix()
                except Exception:
                    rel = str(p)
                if not any(fnmatch.fnmatchcase(rel, g) for g in path_globs):
                    continue
            blocking = find_blocking_lock(p, locks) if locks else None
            if blocking:
                skipped_locked.append(
                    {
                        "path": str(p),
                        "lock_id": blocking.lock_id,
                        "created_by": blocking.created_by,
                        "mode": blocking.mode,
                        "expires_at": blocking.expires_at.isoformat() if blocking.expires_at else None,
                    }
                )
                continue
            link_target = _safe_readlink(p)
            resolved_target = _resolve_link_target(p, link_target) if link_target else None
            candidates.append(BrokenSymlink(path=p, link_target=link_target, resolved_target=resolved_target, root=root))

    candidates.sort(key=lambda c: str(c.path))
    return candidates, skipped_locked


def _write_report(payload: dict[str, Any]) -> Path:
    out_dir = repo_paths.logs_root() / "regression" / "broken_symlinks"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"broken_symlinks_{_utc_now_compact()}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except TypeError:
        # Python <3.8 fallback (should not happen in this repo)
        if path.exists() or path.is_symlink():
            path.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(description="Cleanup broken symlinks (safe dry-run by default).")
    ap.add_argument("--run", action="store_true", help="Actually remove symlinks (default: dry-run).")
    ap.add_argument(
        "--name",
        default="capcut_draft",
        help="Symlink basename to target (default: capcut_draft).",
    )
    ap.add_argument("--include-runs", action="store_true", help="Scan workspaces/video/runs (default: enabled).")
    ap.add_argument("--include-archive", action="store_true", help="Scan workspaces/video/_archive (default: enabled).")
    ap.add_argument(
        "--include-episodes",
        action="store_true",
        help="Also scan workspaces/episodes (default: disabled; broken links may indicate missing artifacts).",
    )
    ap.add_argument(
        "--ignore-locks",
        action="store_true",
        help="Do not respect coordination locks (dangerous; default: respect locks).",
    )
    ap.add_argument(
        "--path-glob",
        action="append",
        default=[],
        help="Only target symlinks whose repo-relative path matches this glob (repeatable). "
        "Example: workspaces/video/runs/CH02-*",
    )
    ap.add_argument("--max-print", type=int, default=120, help="Max candidates to print (default: 120).")
    args = ap.parse_args()

    include_runs = True if (args.include_runs is False and args.include_archive is False) else bool(args.include_runs)
    include_archive = True if (args.include_runs is False and args.include_archive is False) else bool(args.include_archive)

    candidates, skipped_locked = collect_candidates(
        symlink_name=str(args.name),
        include_runs=include_runs,
        include_archive=include_archive,
        include_episodes=bool(args.include_episodes),
        ignore_locks=bool(args.ignore_locks),
        path_globs=[str(x).strip() for x in (args.path_glob or []) if str(x).strip()],
    )

    max_print = max(0, int(args.max_print))
    if max_print:
        for i, c in enumerate(candidates):
            if i >= max_print:
                remaining = len(candidates) - max_print
                print(f"... ({remaining} more)")
                break
            prefix = "[RUN]" if args.run else "[DRY]"
            target = c.link_target or "(unknown)"
            print(f"{prefix} {c.path} -> {target}")

    report = {
        "schema": REPORT_SCHEMA,
        "created_at": _utc_now_iso(),
        "mode": "run" if args.run else "dry_run",
        "symlink_name": str(args.name),
        "include": {
            "runs": include_runs,
            "archive": include_archive,
            "episodes": bool(args.include_episodes),
        },
        "filters": {"path_globs": [str(x).strip() for x in (args.path_glob or []) if str(x).strip()]},
        "counts": {
            "candidates": len(candidates),
            "skipped_locked": len(skipped_locked),
        },
        "candidates": [c.as_dict() for c in candidates],
        "skipped_locked": skipped_locked,
    }

    if not args.run:
        report_path = _write_report(report)
        msg = f"[cleanup_broken_symlinks] dry-run complete candidates={len(candidates)}"
        if skipped_locked:
            msg += f" skipped_locked={len(skipped_locked)}"
        msg += f" report={report_path}"
        print(msg)
        return 0

    deleted = 0
    for c in candidates:
        try:
            _unlink(c.path)
            deleted += 1
        except Exception:
            continue

    report["counts"]["deleted"] = int(deleted)
    report_path = _write_report(report)
    msg = f"[cleanup_broken_symlinks] deleted={deleted} (candidates={len(candidates)}) report={report_path}"
    if skipped_locked:
        msg += f" skipped_locked={len(skipped_locked)}"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
