#!/usr/bin/env python3
"""
prune_video_run_legacy_files — workspaces/video/runs 内の `*.legacy.*` 残骸を整理する。

背景:
- run_dir 直下に `image_cues.legacy.<ts>.json` や `<CH>-<NNN>.legacy.<ts>.srt` が溜まると、
  低知能エージェントが「どれが正本？」を誤認しやすい。
- `*.legacy.*` は“過去のバックアップ”であり、現行フローの入力としては使わない（探索ノイズ）。

安全設計:
- default は dry-run（削除しない）。
- `--run` 指定時のみ削除する。
- coordination locks を尊重し、lock 対象は skip。
- `--run` 時は任意で archive-first（tar.gz）を作成してから削除する。

SSOT:
- ssot/PLAN_OPS_ARTIFACT_LIFECYCLE.md
- ssot/OPS_LOGGING_MAP.md
"""

from __future__ import annotations

import argparse
import json
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402


REPORT_SCHEMA = "ytm.video_run_legacy_prune_report.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _rel(path: Path) -> str:
    try:
        return path.relative_to(REPO_ROOT).as_posix()
    except Exception:
        return str(path)


@dataclass(frozen=True)
class Candidate:
    path: Path


def _is_legacy_file(path: Path) -> bool:
    name = path.name
    return ".legacy." in name and path.is_file()


def collect_candidates(*, ignore_locks: bool) -> tuple[list[Candidate], list[Path]]:
    runs_root = repo_paths.video_runs_root()
    locks = [] if ignore_locks else default_active_locks_for_mutation()

    deletable: list[Candidate] = []
    skipped_locked: list[Path] = []

    if not runs_root.exists():
        return [], []

    for p in sorted(runs_root.rglob("*")):
        if not _is_legacy_file(p):
            continue
        if locks and find_blocking_lock(p, locks):
            skipped_locked.append(p)
            continue
        deletable.append(Candidate(path=p))

    return deletable, skipped_locked


def _default_archive_path(ts: str) -> Path:
    return REPO_ROOT / "backups" / "graveyard" / f"{ts}_video_runs_legacy_files.tar.gz"


def _write_report(payload: dict[str, Any]) -> Path:
    out_dir = repo_paths.logs_root() / "regression" / "video_runs_legacy_prune"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"legacy_prune_{_utc_now_compact()}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _archive_files(paths: list[Path], archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "w:gz") as tf:
        for p in paths:
            try:
                tf.add(p, arcname=_rel(p))
            except Exception:
                continue


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune `*.legacy.*` files under workspaces/video/runs (safe dry-run by default).")
    ap.add_argument("--run", action="store_true", help="Actually delete (default: dry-run).")
    ap.add_argument("--max-print", type=int, default=40, help="Max deletable paths to print (default: 40).")
    ap.add_argument("--no-archive", action="store_true", help="Skip archive-first tar.gz creation (default: archive when --run).")
    ap.add_argument("--archive-path", help="Override archive tar.gz path (default: backups/graveyard/<ts>_video_runs_legacy_files.tar.gz)")
    ap.add_argument(
        "--ignore-locks",
        action="store_true",
        help="Do not respect coordination locks (dangerous; default: respect locks).",
    )
    args = ap.parse_args()

    do_run = bool(args.run)
    dry_run = not do_run

    deletable, skipped_locked = collect_candidates(ignore_locks=bool(args.ignore_locks))
    max_print = max(0, int(args.max_print))

    print(
        f"[prune_video_run_legacy_files] deletable={len(deletable)} skipped_locked={len(skipped_locked)} "
        f"dry_run={dry_run}",
        flush=True,
    )
    if max_print:
        for i, c in enumerate(deletable):
            if i >= max_print:
                remaining = len(deletable) - max_print
                if remaining > 0:
                    print(f"... ({remaining} more)")
                break
            prefix = "[RUN]" if do_run else "[DRY]"
            print(f"{prefix} {_rel(c.path)}")

    if dry_run:
        report_path = _write_report(
            {
                "schema": REPORT_SCHEMA,
                "created_at": _utc_now_iso(),
                "mode": "dry_run",
                "targets_root": str(repo_paths.video_runs_root()),
                "counts": {
                    "deletable": len(deletable),
                    "skipped_locked": len(skipped_locked),
                },
                "deletable": [_rel(c.path) for c in deletable],
                "skipped_locked": [_rel(p) for p in skipped_locked],
            }
        )
        print(f"[prune_video_run_legacy_files] wrote report={report_path}", flush=True)
        return 0

    archive_path: Optional[Path] = None
    if not bool(args.no_archive) and deletable:
        ts = _utc_now_compact()
        archive_path = Path(args.archive_path).expanduser().resolve() if args.archive_path else _default_archive_path(ts)
        _archive_files([c.path for c in deletable], archive_path)

    deleted = 0
    deleted_paths: list[str] = []
    failed: list[dict[str, str]] = []
    for c in deletable:
        try:
            c.path.unlink(missing_ok=True)
            deleted += 1
            deleted_paths.append(_rel(c.path))
        except Exception as exc:
            failed.append({"path": _rel(c.path), "error": str(exc)})

    report_path = _write_report(
        {
            "schema": REPORT_SCHEMA,
            "created_at": _utc_now_iso(),
            "mode": "run",
            "targets_root": str(repo_paths.video_runs_root()),
            "archive_path": str(archive_path) if archive_path else None,
            "counts": {
                "planned": len(deletable),
                "deleted": deleted,
                "failed": len(failed),
                "skipped_locked": len(skipped_locked),
            },
            "deleted": deleted_paths,
            "failed": failed,
            "skipped_locked": [_rel(p) for p in skipped_locked],
        }
    )

    msg = f"[prune_video_run_legacy_files] deleted={deleted} planned={len(deletable)} report={report_path}"
    if archive_path:
        msg += f" archive={archive_path}"
    if failed:
        msg += f" failed={len(failed)}"
    print(msg, flush=True)
    return 0 if not failed else 2


if __name__ == "__main__":
    raise SystemExit(main())
