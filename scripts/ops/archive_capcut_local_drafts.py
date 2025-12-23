#!/usr/bin/env python3
"""
archive_capcut_local_drafts — workspaces/video/_capcut_drafts の“ローカル退避”ドラフトを整理する。

背景:
- `commentary_02_srt2images_timeline/tools/auto_capcut_run.py` は、CapCut の実draft root
  (`~/Movies/CapCut/.../com.lveditor.draft`) が書き込み不可な環境で
  `workspaces/video/_capcut_drafts/` にフォールバックしてドラフトを生成する。
- 実draft root にコピー済みのローカルドラフトは、SoT ではなく探索ノイズになりやすい。

整理方針（安全）:
- default は dry-run（移動しない）。
- `--run` 指定時のみ移動する（削除ではない）。
- 直近で更新されたドラフトは `--keep-recent-minutes` で保護する。
- coordination locks を尊重し、lock 対象は skip。
- 退避先は `workspaces/video/_capcut_drafts/_archive/<timestamp>/`。
- JSON report を `workspaces/logs/regression/capcut_local_drafts_archive/` に出力する。

SSOT:
- ssot/plans/PLAN_OPS_ARTIFACT_LIFECYCLE.md
- ssot/ops/OPS_LOGGING_MAP.md
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


REPORT_SCHEMA = "ytm.capcut.local_drafts_archive.v1"
EP_RE = re.compile(r"(CH\d{2}-\d{3})")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _default_capcut_real_root() -> Path:
    return Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft"


def _capcut_real_episode_ids(real_root: Path) -> tuple[set[str], dict[str, list[str]]]:
    """
    Return episode-id set + a small lookup map for report/debug.
    CapCut folder names may be truncated; episode-id match is substring-based.
    """
    ids: set[str] = set()
    by_id: dict[str, list[str]] = {}
    for p in real_root.iterdir():
        try:
            if not p.is_dir():
                continue
        except Exception:
            continue
        m = EP_RE.search(p.name)
        if not m:
            continue
        ep = m.group(1)
        ids.add(ep)
        by_id.setdefault(ep, []).append(p.name)
    return ids, by_id


def _is_protected_dir_name(name: str) -> bool:
    n = (name or "").strip()
    if not n:
        return True
    if n.startswith("."):
        return True
    if n == "_archive":
        return True
    if "テンプレ" in n:
        return True
    if "template" in n.lower():
        return True
    # Common "template-ish" placeholder naming.
    if "XXX" in n.upper():
        return True
    return False


@dataclass(frozen=True)
class Candidate:
    src: Path
    name: str
    episode_id: str
    mtime_epoch: float


def _is_recent(*, mtime_epoch: float, keep_recent_minutes: int) -> bool:
    if keep_recent_minutes <= 0:
        return False
    cutoff = datetime.now(timezone.utc).timestamp() - (keep_recent_minutes * 60)
    return mtime_epoch >= cutoff


def collect_candidates(
    *,
    local_root: Path,
    real_episode_ids: set[str],
    keep_recent_minutes: int,
    include_unmatched: bool,
    ignore_locks: bool,
) -> tuple[list[Candidate], dict[str, list[dict[str, Any]]]]:
    locks = [] if ignore_locks else default_active_locks_for_mutation()

    candidates: list[Candidate] = []
    skipped: dict[str, list[dict[str, Any]]] = {
        "protected_name": [],
        "no_episode_id": [],
        "not_copied_to_real_root": [],
        "recent": [],
        "symlink": [],
        "locked": [],
    }

    if not local_root.exists():
        return [], skipped

    for p in sorted(local_root.iterdir(), key=lambda x: x.name):
        try:
            if not p.is_dir():
                continue
        except Exception:
            continue

        name = p.name
        if _is_protected_dir_name(name):
            skipped["protected_name"].append({"path": str(p), "name": name})
            continue

        if p.is_symlink():
            skipped["symlink"].append({"path": str(p), "name": name})
            continue

        if locks and find_blocking_lock(p, locks):
            skipped["locked"].append({"path": str(p), "name": name})
            continue

        m = EP_RE.search(name)
        if not m:
            skipped["no_episode_id"].append({"path": str(p), "name": name})
            continue
        episode_id = m.group(1)

        try:
            mtime_epoch = p.stat().st_mtime
        except Exception:
            mtime_epoch = 0.0

        if _is_recent(mtime_epoch=mtime_epoch, keep_recent_minutes=keep_recent_minutes):
            skipped["recent"].append({"path": str(p), "name": name, "episode_id": episode_id})
            continue

        if episode_id not in real_episode_ids and not include_unmatched:
            skipped["not_copied_to_real_root"].append({"path": str(p), "name": name, "episode_id": episode_id})
            continue

        candidates.append(Candidate(src=p, name=name, episode_id=episode_id, mtime_epoch=mtime_epoch))

    return candidates, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description="Archive local CapCut drafts under workspaces/video/_capcut_drafts (safe dry-run by default).")
    ap.add_argument("--run", action="store_true", help="Actually move directories (default: dry-run).")
    ap.add_argument(
        "--archive-dir",
        help="Optional override for archive destination root (default: <local_root>/_archive/<timestamp>).",
    )
    ap.add_argument(
        "--real-draft-root",
        default=str(_default_capcut_real_root()),
        help="Real CapCut draft root for 'copied already' detection (default: macOS CapCut location).",
    )
    ap.add_argument(
        "--keep-recent-minutes",
        type=int,
        default=24 * 60,
        help="Protect drafts modified within this many minutes (default: 1440 = 24h). 0 disables.",
    )
    ap.add_argument(
        "--include-unmatched",
        action="store_true",
        help="Also archive drafts without a matching episode-id in real draft root (dangerous; default: false).",
    )
    ap.add_argument("--max-print", type=int, default=40, help="Max candidates to print (default: 40).")
    ap.add_argument(
        "--ignore-locks",
        action="store_true",
        help="Do not respect coordination locks (dangerous; default: respect locks).",
    )
    args = ap.parse_args()

    local_root = repo_paths.video_capcut_local_drafts_root()
    ts = _utc_now_compact()
    archive_root = (
        Path(args.archive_dir).expanduser().resolve()
        if args.archive_dir
        else (local_root / "_archive" / ts)
    )
    report_path = (
        repo_paths.logs_root()
        / "regression"
        / "capcut_local_drafts_archive"
        / f"capcut_local_drafts_archive_{ts}.json"
    )

    real_root = Path(args.real_draft_root).expanduser().resolve()
    real_episode_ids: set[str] = set()
    real_by_id: dict[str, list[str]] = {}
    real_root_error: str | None = None
    try:
        if real_root.exists():
            real_episode_ids, real_by_id = _capcut_real_episode_ids(real_root)
        else:
            real_root_error = f"real_draft_root_not_found: {real_root}"
    except Exception as exc:
        real_root_error = f"{type(exc).__name__}: {exc}"

    candidates, skipped = collect_candidates(
        local_root=local_root,
        real_episode_ids=real_episode_ids,
        keep_recent_minutes=int(args.keep_recent_minutes),
        include_unmatched=bool(args.include_unmatched),
        ignore_locks=bool(args.ignore_locks),
    )

    payload: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "created_at": _utc_now_iso(),
        "generated_at": ts,
        "mode": "run" if args.run else "dry-run",
        "ignore_locks": bool(args.ignore_locks),
        "include_unmatched": bool(args.include_unmatched),
        "keep_recent_minutes": int(args.keep_recent_minutes),
        "local_root": str(local_root),
        "real_draft_root": str(real_root),
        "real_draft_root_error": real_root_error,
        "archive_root": str(archive_root),
        "counts": {
            "candidates": len(candidates),
            "skipped_protected_name": len(skipped["protected_name"]),
            "skipped_no_episode_id": len(skipped["no_episode_id"]),
            "skipped_recent": len(skipped["recent"]),
            "skipped_symlink": len(skipped["symlink"]),
            "skipped_locked": len(skipped["locked"]),
            "skipped_not_copied_to_real_root": len(skipped["not_copied_to_real_root"]),
        },
        "candidates": [{"path": str(c.src), "name": c.name, "episode_id": c.episode_id} for c in candidates],
        "skipped": skipped,
        "moves": [],
        "real_matches_preview": {
            c.episode_id: real_by_id.get(c.episode_id, [])[:3] for c in candidates
        },
    }

    print(
        f"[archive_capcut_local_drafts] candidates={len(candidates)} dry_run={not args.run} "
        f"local_root={local_root}"
    )
    if real_root_error:
        print(f"[archive_capcut_local_drafts] WARN real_root_error={real_root_error}")
    else:
        print(f"[archive_capcut_local_drafts] real_root={real_root} episode_ids={len(real_episode_ids)}")

    max_print = max(0, int(args.max_print))
    if max_print and candidates:
        for i, c in enumerate(candidates[:max_print], start=1):
            prefix = "[RUN]" if args.run else "[DRY]"
            print(f"{prefix} {i:>3}/{len(candidates)} {c.episode_id} {c.name}")
        if len(candidates) > max_print:
            print(f"... ({len(candidates) - max_print} more)")

    moved = 0
    if args.run:
        for c in candidates:
            dest = archive_root / c.name
            _move_dir(c.src, dest)
            moved += 1
            payload["moves"].append({"src": str(c.src), "dest": str(dest), "episode_id": c.episode_id})
        payload["counts"]["moved"] = moved
    else:
        payload["counts"]["moved"] = 0

    _save_json(report_path, payload)
    print(f"[archive_capcut_local_drafts] wrote report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
