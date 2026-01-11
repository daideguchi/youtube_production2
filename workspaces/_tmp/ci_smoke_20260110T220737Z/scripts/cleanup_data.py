"""
workspaces/scripts 配下の中間生成物と古いログをクリーンアップする。

安全のため:
- default は dry-run（削除しない）
- `--run` 指定時のみ削除する
- `--keep-days` より新しいものは削除しない
- `audio_prep/` は final 音声が揃っている動画のみ削除対象にする（誤って未完了の入力を消さない）

SSOT:
- `ssot/ops/OPS_LOGGING_MAP.md`
- `ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md`
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap()

from factory_common import paths as repo_paths
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock


@dataclass(frozen=True)
class DeletionCandidate:
    path: Path
    reason: str


def _now() -> datetime:
    return datetime.now()


def _is_older_than(path: Path, cutoff: datetime) -> bool:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime) < cutoff
    except Exception:
        return False


def _walk_paths(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root]

    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        for name in dirnames:
            out.append(dp / name)
        for name in filenames:
            out.append(dp / name)
    return out


def _subtree_is_older_than(root: Path, cutoff: datetime) -> bool:
    if not root.exists():
        return False
    if not _is_older_than(root, cutoff):
        return False
    for p in _walk_paths(root):
        if not _is_older_than(p, cutoff):
            return False
    return True


def _script_data_root() -> Path:
    # Use Path SSOT so Stage2 physical moves don't break this cleanup.
    return repo_paths.script_data_root()


def _script_state_logs_dir(data_root: Path) -> Path:
    return data_root / "_state" / "logs"


def _final_audio_pair_exists(channel: str, video: str) -> bool:
    ch = (channel or "").upper()
    v = str(video or "").zfill(3)
    if not ch.startswith("CH") or not v.isdigit():
        return False
    final_dir = repo_paths.audio_final_dir(ch, v)
    wav = final_dir / f"{ch}-{v}.wav"
    srt = final_dir / f"{ch}-{v}.srt"
    return wav.exists() and srt.exists()


def collect_candidates(*, keep_days: int, ignore_locks: bool) -> tuple[list[DeletionCandidate], int]:
    cutoff = _now() - timedelta(days=int(keep_days))
    data_root = _script_data_root()
    state_logs_dir = _script_state_logs_dir(data_root)

    candidates: list[DeletionCandidate] = []
    locks = [] if ignore_locks else default_active_locks_for_mutation()
    skipped_locked = 0

    # 1) script_pipeline state logs (L3, keep-days rotation)
    if state_logs_dir.exists():
        for p in sorted(state_logs_dir.glob("*.log")):
            if _is_older_than(p, cutoff):
                if locks and find_blocking_lock(p, locks):
                    skipped_locked += 1
                    continue
                candidates.append(DeletionCandidate(p, f"script_state_log_older_than_{keep_days}d"))

    # 2) per-video intermediates (audio_prep, logs) older than keep-days
    if data_root.exists():
        for channel_dir in sorted(data_root.iterdir()):
            if not channel_dir.is_dir() or channel_dir.name.startswith("_"):
                continue
            for video_dir in sorted(channel_dir.iterdir()):
                if not video_dir.is_dir() or not video_dir.name.isdigit():
                    continue
                for sub in ("audio_prep", "logs"):
                    target = video_dir / sub
                    if not target.exists():
                        continue
                    if sub == "audio_prep" and not _final_audio_pair_exists(channel_dir.name, video_dir.name):
                        continue
                    if _subtree_is_older_than(target, cutoff):
                        if locks and find_blocking_lock(target, locks):
                            skipped_locked += 1
                            continue
                        candidates.append(DeletionCandidate(target, f"{sub}_older_than_{keep_days}d"))

    return candidates, skipped_locked


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _is_safe_candidate(path: Path, data_root: Path) -> bool:
    """
    Extra safety: refuse to delete anything outside workspaces/scripts, and only allow
    the explicitly intended targets (L3 logs + audio_prep/logs dirs).
    """
    if not _is_under(path, data_root):
        return False
    if path == data_root:
        return False

    state_logs_dir = _script_state_logs_dir(data_root)
    if path.is_file():
        return path.suffix == ".log" and _is_under(path, state_logs_dir)

    # Directory candidates are strictly limited to these rebuildable subtrees.
    return path.name in {"audio_prep", "logs"}


def _delete_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cleanup script_pipeline intermediates/logs (safe dry-run by default)")
    parser.add_argument("--run", action="store_true", help="Actually delete files (default: dry-run)")
    parser.add_argument("--keep-days", type=int, default=14, help="Keep files newer than this many days (default: 14)")
    parser.add_argument(
        "--ignore-locks",
        action="store_true",
        help="Do not respect coordination locks (dangerous; default: respect locks).",
    )
    parser.add_argument(
        "--max-print",
        type=int,
        default=200,
        help="Max candidates to print (default: 200; set 0 to suppress)",
    )
    args = parser.parse_args()

    keep_days = int(args.keep_days)
    if keep_days < 1:
        raise SystemExit("--keep-days must be >= 1")

    candidates, skipped_locked = collect_candidates(keep_days=keep_days, ignore_locks=bool(args.ignore_locks))
    if not candidates:
        msg = "[cleanup_data] nothing to do"
        if skipped_locked:
            msg += f" (skipped_locked={skipped_locked})"
        print(msg)
        return 0

    max_print = max(0, int(args.max_print))
    if max_print:
        for i, c in enumerate(candidates):
            if i >= max_print:
                remaining = len(candidates) - max_print
                print(f"... ({remaining} more)")
                break
            prefix = "[RUN]" if args.run else "[DRY]"
            print(f"{prefix} {c.path}  ({c.reason})")

    if not args.run:
        msg = "[cleanup_data] dry-run complete (pass --run to delete)"
        if skipped_locked:
            msg += f" (skipped_locked={skipped_locked})"
        print(msg)
        return 0

    data_root = _script_data_root()
    deleted = 0
    skipped_unsafe = 0
    for c in candidates:
        try:
            if not _is_safe_candidate(c.path, data_root):
                skipped_unsafe += 1
                continue
            _delete_path(c.path)
            deleted += 1
        except Exception:
            continue

    msg = f"[cleanup_data] deleted {deleted} paths"
    if skipped_unsafe:
        msg += f" (skipped_unsafe={skipped_unsafe})"
    if skipped_locked:
        msg += f" (skipped_locked={skipped_locked})"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
