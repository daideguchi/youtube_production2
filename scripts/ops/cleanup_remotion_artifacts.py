#!/usr/bin/env python3
"""
cleanup_remotion_artifacts — Remotion の生成物（L3/L2）をローテして探索ノイズと容量を減らす。

対象（既定）:
- apps/remotion/out/**                (mp4/chunks/tmp wav などの生成物)
- apps/remotion/public/_bgm/**        (生成BGM wav; 再生成可能)
- apps/remotion/public/_auto/**       (自動生成物; 再生成可能)

安全設計:
- default は dry-run（削除しない）
- `--run` 指定時のみ削除する
- `--keep-days` より新しいものは削除しない
- coordination locks を尊重し、lock 対象は skip

SSOT:
- ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md
- ssot/OPS_LOGGING_MAP.md
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402


REPORT_SCHEMA = "ytm.remotion_artifacts_cleanup_report.v1"

# Tracked or intentionally kept files (avoid deleting even if old).
KEEP_ALWAYS_OUT_FILENAMES = {
    "belt_config.generated.json",
    "belt_llm_raw.json",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except Exception:
        return str(path)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _mtime_utc(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, timezone.utc)


def _fmt_bytes(num: int) -> str:
    n = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}TB"


def _walk_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        for name in filenames:
            out.append(dp / name)
    return out


def _subtree_is_older_than(root: Path, cutoff: datetime) -> bool:
    if not root.exists():
        return False
    if _mtime_utc(root) >= cutoff:
        return False
    for p in _walk_files(root):
        if _mtime_utc(p) >= cutoff:
            return False
    return True


@dataclass(frozen=True)
class Candidate:
    path: Path
    reason: str
    size_bytes: int


def _remotion_app_root() -> Path:
    # Stage5 canonical location. (root `remotion/` is a compat symlink.)
    return repo_paths.repo_root() / "apps" / "remotion"


def collect_candidates(
    *,
    keep_days: int,
    include_out: bool,
    include_public_auto: bool,
    include_public_bgm: bool,
    ignore_locks: bool,
) -> tuple[list[Candidate], int]:
    cutoff = _now_utc() - timedelta(days=int(keep_days))
    locks = [] if ignore_locks else default_active_locks_for_mutation()
    skipped_locked = 0

    app = _remotion_app_root()
    out_dir = app / "out"
    auto_dir = app / "public" / "_auto"
    bgm_dir = app / "public" / "_bgm"

    candidates: list[Candidate] = []

    def add_file(p: Path, reason: str) -> None:
        nonlocal skipped_locked
        if locks and find_blocking_lock(p, locks):
            skipped_locked += 1
            return
        try:
            sz = p.stat().st_size
        except Exception:
            sz = 0
        candidates.append(Candidate(path=p, reason=reason, size_bytes=int(sz)))

    def add_dir(p: Path, reason: str) -> None:
        nonlocal skipped_locked
        if locks and find_blocking_lock(p, locks):
            skipped_locked += 1
            return
        # Directory size is best-effort; used only for reporting.
        sz = 0
        for fp in _walk_files(p):
            try:
                sz += fp.stat().st_size
            except Exception:
                pass
        candidates.append(Candidate(path=p, reason=reason, size_bytes=int(sz)))

    if include_out and out_dir.exists():
        # Prefer removing entire old subtrees (chunks dirs etc) to avoid leaving empty dirs.
        for child in sorted(out_dir.iterdir()):
            if not child.exists():
                continue
            if child.is_dir():
                if _subtree_is_older_than(child, cutoff):
                    add_dir(child, f"out_subtree_older_than_{keep_days}d")
                continue
            if child.name in KEEP_ALWAYS_OUT_FILENAMES:
                continue
            if _mtime_utc(child) < cutoff:
                add_file(child, f"out_file_older_than_{keep_days}d")

    def prune_files_under(base: Path, label: str) -> None:
        if not base.exists():
            return
        for fp in sorted(_walk_files(base)):
            if _mtime_utc(fp) < cutoff:
                add_file(fp, f"{label}_file_older_than_{keep_days}d")

    if include_public_auto:
        prune_files_under(auto_dir, "public_auto")
    if include_public_bgm:
        prune_files_under(bgm_dir, "public_bgm")

    # Deduplicate (file candidates may overlap with directory candidates; keep dir if present).
    dir_set = {c.path for c in candidates if c.path.is_dir()}
    uniq: list[Candidate] = []
    seen: set[Path] = set()
    for c in candidates:
        if c.path in seen:
            continue
        if any(parent in dir_set for parent in c.path.parents):
            # covered by a parent dir deletion candidate
            continue
        seen.add(c.path)
        uniq.append(c)

    uniq.sort(key=lambda c: _rel(c.path))
    return uniq, int(skipped_locked)


def _delete_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink(missing_ok=True)


def _cleanup_empty_dirs(root: Path) -> None:
    if not root.exists():
        return
    # bottom-up remove empty dirs; keep root
    for dp, dirnames, filenames in os.walk(root, topdown=False):
        if dirnames or filenames:
            continue
        p = Path(dp)
        if p == root:
            continue
        try:
            p.rmdir()
        except Exception:
            continue


def _write_report(payload: dict[str, Any]) -> Path:
    out_dir = repo_paths.logs_root() / "regression" / "remotion_cleanup"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"remotion_cleanup_{_utc_now_compact()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Cleanup Remotion artifacts (safe dry-run by default).")
    ap.add_argument("--run", action="store_true", help="Actually delete (default: dry-run).")
    ap.add_argument("--keep-days", type=int, default=14, help="Keep artifacts newer than this many days (default: 14).")
    ap.add_argument("--include-out", action="store_true", help="Include apps/remotion/out (default: enabled).")
    ap.add_argument("--include-public-auto", action="store_true", help="Include apps/remotion/public/_auto (default: enabled).")
    ap.add_argument("--include-public-bgm", action="store_true", help="Include apps/remotion/public/_bgm (default: enabled).")
    ap.add_argument("--max-print", type=int, default=60, help="Max candidates to print (default: 60; set 0 to suppress).")
    ap.add_argument(
        "--ignore-locks",
        action="store_true",
        help="Do not respect coordination locks (dangerous; default: respect locks).",
    )
    args = ap.parse_args()

    keep_days = int(args.keep_days)
    if keep_days < 1:
        raise SystemExit("--keep-days must be >= 1")

    include_out = True if not (args.include_out or args.include_public_auto or args.include_public_bgm) else bool(args.include_out)
    include_auto = True if not (args.include_out or args.include_public_auto or args.include_public_bgm) else bool(args.include_public_auto)
    include_bgm = True if not (args.include_out or args.include_public_auto or args.include_public_bgm) else bool(args.include_public_bgm)

    do_run = bool(args.run)
    dry_run = not do_run

    candidates, skipped_locked = collect_candidates(
        keep_days=keep_days,
        include_out=include_out,
        include_public_auto=include_auto,
        include_public_bgm=include_bgm,
        ignore_locks=bool(args.ignore_locks),
    )
    total = sum(c.size_bytes for c in candidates)
    print(
        f"[cleanup_remotion_artifacts] candidates={len(candidates)} total={_fmt_bytes(total)} "
        f"dry_run={dry_run} keep_days={keep_days} skipped_locked={skipped_locked}",
        flush=True,
    )

    max_print = max(0, int(args.max_print))
    if max_print:
        for i, c in enumerate(candidates):
            if i >= max_print:
                remaining = len(candidates) - max_print
                if remaining > 0:
                    print(f"... ({remaining} more)")
                break
            prefix = "[RUN]" if do_run else "[DRY]"
            kind = "dir" if c.path.is_dir() else "file"
            print(f"{prefix} {kind} {_rel(c.path)} size={_fmt_bytes(c.size_bytes)} ({c.reason})")

    if dry_run:
        report_path = _write_report(
            {
                "schema": REPORT_SCHEMA,
                "created_at": _utc_now_iso(),
                "mode": "dry_run",
                "policy": {
                    "keep_days": keep_days,
                    "include_out": include_out,
                    "include_public_auto": include_auto,
                    "include_public_bgm": include_bgm,
                    "respect_locks": not bool(args.ignore_locks),
                },
                "counts": {
                    "candidates": len(candidates),
                    "skipped_locked": skipped_locked,
                },
                "bytes": {
                    "candidates_total": int(total),
                },
                "candidates": [
                    {"path": _rel(c.path), "reason": c.reason, "kind": ("dir" if c.path.is_dir() else "file"), "size_bytes": c.size_bytes}
                    for c in candidates
                ],
            }
        )
        print(f"[cleanup_remotion_artifacts] wrote report={report_path}", flush=True)
        return 0

    deleted = 0
    failed: list[dict[str, str]] = []
    for c in candidates:
        try:
            _delete_path(c.path)
            deleted += 1
        except Exception as exc:
            failed.append({"path": _rel(c.path), "error": str(exc)})

    # Post cleanup: remove empty directories created by partial file deletions.
    app = _remotion_app_root()
    if include_out:
        _cleanup_empty_dirs(app / "out")
    if include_auto:
        _cleanup_empty_dirs(app / "public" / "_auto")
    if include_bgm:
        _cleanup_empty_dirs(app / "public" / "_bgm")

    report_path = _write_report(
        {
            "schema": REPORT_SCHEMA,
            "created_at": _utc_now_iso(),
            "mode": "run",
            "policy": {
                "keep_days": keep_days,
                "include_out": include_out,
                "include_public_auto": include_auto,
                "include_public_bgm": include_bgm,
                "respect_locks": not bool(args.ignore_locks),
            },
            "counts": {
                "planned": len(candidates),
                "deleted": deleted,
                "failed": len(failed),
                "skipped_locked": skipped_locked,
            },
            "bytes": {
                "planned_total": int(total),
            },
            "failed": failed,
        }
    )

    msg = f"[cleanup_remotion_artifacts] deleted={deleted} planned={len(candidates)} report={report_path}"
    if failed:
        msg += f" failed={len(failed)}"
    print(msg, flush=True)
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
