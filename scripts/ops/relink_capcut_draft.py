#!/usr/bin/env python3
"""
relink_capcut_draft.py — CapCut draft 参照（run_dir/capcut_draft + capcut_draft_info.json）を安全に整合させる

背景:
- Hot（未投稿）は「Macローカルに実体」が絶対。
- しかし運用の中で、CapCut draft のフォルダ名が変わる/別名が増えると、
  run_dir 側の `capcut_draft` symlink と `capcut_draft_info.json:draft_path` が古くなり、
  UI/編集で参照切れ（壊れsymlink/ドラフト未検出）が起きる。

方針（安全）:
- default は dry-run（書き換えない）。`--run` 指定時のみ変更する。
- 変更対象は:
  - `<run_dir>/capcut_draft`（symlink）
  - `<run_dir>/capcut_draft_info.json`（draft_path / draft_path_ref を更新）
- draft 自体（CapCut の draft_content.json など）は一切変更しない。

SSOT:
- ssot/ops/OPS_HOTSET_POLICY.md
- ssot/plans/PLAN_CAPCUT_HOT_VAULT_ROLLOUT.md
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from _bootstrap import bootstrap

_REPO_ROOT = bootstrap(load_env=False)

from factory_common.path_ref import best_effort_path_ref  # noqa: E402
from factory_common.paths import capcut_draft_root, video_runs_root  # noqa: E402
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402


EP_RE = re.compile(r"^(CH\d{1,2})-(\d{1,3})$")


def _utc_now_tag() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _norm_channel(value: str) -> str:
    raw = str(value or "").strip().upper()
    if not raw.startswith("CH") or raw == "CH":
        raise SystemExit(f"Invalid channel: {value!r} (expected CHxx)")
    digits = "".join(ch for ch in raw[2:] if ch.isdigit())
    if digits:
        return f"CH{int(digits):02d}"
    return raw


def _norm_video(value: str) -> str:
    token = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not token:
        raise SystemExit(f"Invalid video: {value!r} (expected NNN)")
    return f"{int(token):03d}"


def _episode_id(channel: str, video: str) -> str:
    return f"{_norm_channel(channel)}-{_norm_video(video)}"


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".tmp__{path.name}__{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


@dataclass(frozen=True)
class RunPick:
    run_id: str
    run_dir: Path
    mtime_epoch: float
    has_draft_info: bool


def _pick_run_dir(episode_id: str) -> Optional[RunPick]:
    root = video_runs_root()
    if not root.exists():
        return None
    best: Optional[RunPick] = None
    for p in root.iterdir():
        if not p.is_dir():
            continue
        if p.name.startswith(("_", ".")):
            continue
        if episode_id not in p.name:
            continue
        info_path = p / "capcut_draft_info.json"
        has_info = info_path.exists()
        try:
            mtime = float(p.stat().st_mtime)
        except Exception:
            mtime = 0.0
        cand = RunPick(run_id=p.name, run_dir=p, mtime_epoch=mtime, has_draft_info=bool(has_info))
        if best is None:
            best = cand
            continue
        if (cand.has_draft_info, cand.mtime_epoch, cand.run_id) > (best.has_draft_info, best.mtime_epoch, best.run_id):
            best = cand
    return best


def _candidate_drafts(episode_id: str) -> list[Path]:
    root = capcut_draft_root()
    if not root.exists():
        return []
    out: list[Path] = []
    token = episode_id.upper()
    for p in root.iterdir():
        try:
            if not p.is_dir():
                continue
        except Exception:
            continue
        if token in p.name.upper():
            out.append(p)
    out.sort(key=lambda p: (p.stat().st_mtime if p.exists() else 0.0, p.name), reverse=True)
    return out


def _format_candidate(p: Path) -> str:
    try:
        mtime = datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        mtime = "unknown"
    flags = []
    for name in ("draft_content.json", "draft_info.json"):
        try:
            if (p / name).exists():
                flags.append(name)
        except Exception:
            pass
    flag_s = ",".join(flags) if flags else "-"
    return f"{p} (mtime={mtime} files={flag_s})"


def _backup_symlink(path: Path) -> Optional[Path]:
    if not path.exists() and not path.is_symlink():
        return None
    if not path.is_symlink():
        return None
    backup = path.with_name(f"{path.name}.symlink_backup__{_utc_now_tag()}")
    try:
        path.rename(backup)
        return backup
    except Exception:
        return None


def _write_symlink(link_path: Path, target_dir: Path) -> None:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    if link_path.exists() or link_path.is_symlink():
        # Caller must backup first.
        try:
            link_path.unlink()
        except Exception:
            pass
    os.symlink(str(target_dir), str(link_path))


def _validate_episode_id(ep: str) -> str:
    m = EP_RE.match(str(ep or "").strip().upper())
    if not m:
        raise SystemExit(f"Invalid episode: {ep!r} (expected CHxx-NNN)")
    return f"{_norm_channel(m.group(1))}-{_norm_video(m.group(2))}"


def main() -> int:
    ap = argparse.ArgumentParser(description="Relink CapCut draft wiring for a run_dir (safe dry-run by default).")
    ap.add_argument("--episode", help="Episode id (CHxx-NNN).")
    ap.add_argument("--channel", help="Channel (CHxx).")
    ap.add_argument("--video", help="Video (NNN).")
    ap.add_argument("--run-id", help="Explicit run_id under workspaces/video/runs (optional).")
    ap.add_argument("--draft-dir", help="Target CapCut draft directory (must exist).")
    ap.add_argument("--run", action="store_true", help="Apply changes (default: dry-run).")
    ap.add_argument(
        "--ignore-locks",
        action="store_true",
        help="Do not respect coordination locks (dangerous; default: respect locks).",
    )
    args = ap.parse_args()

    if args.episode:
        ep = _validate_episode_id(args.episode)
    else:
        if not (args.channel and args.video):
            raise SystemExit("Provide --episode or both --channel and --video.")
        ep = _episode_id(str(args.channel), str(args.video))

    # Pick run_dir.
    if args.run_id:
        run_dir = video_runs_root() / str(args.run_id).strip()
        run_pick = RunPick(run_id=run_dir.name, run_dir=run_dir, mtime_epoch=0.0, has_draft_info=(run_dir / "capcut_draft_info.json").exists())
    else:
        run_pick = _pick_run_dir(ep)
        if run_pick is None:
            raise SystemExit(f"No run_dir found for episode: {ep}")
        run_dir = run_pick.run_dir

    if not run_dir.exists() or not run_dir.is_dir():
        raise SystemExit(f"run_dir not found: {run_dir}")

    # Respect locks (mutations only). Dry-run should not pay the lock-scan cost.
    if bool(args.run) and not bool(args.ignore_locks):
        locks = default_active_locks_for_mutation()
        blocking = find_blocking_lock(run_dir, locks) if locks else None
        if blocking:
            raise SystemExit(f"run_dir is locked; abort. run_dir={run_dir} lock={blocking.lock_id}")

    info_path = run_dir / "capcut_draft_info.json"
    info = _safe_read_json(info_path) if info_path.exists() else {}
    current_path = str(info.get("draft_path") or "").strip() if isinstance(info, dict) else ""

    print(f"[relink_capcut_draft] episode={ep}")
    print(f"- run_id: {run_pick.run_id if run_pick else run_dir.name}")
    print(f"- run_dir: {run_dir}")
    print(f"- capcut_draft_info: {info_path} ({'exists' if info_path.exists() else 'missing'})")
    if current_path:
        print(f"- current draft_path: {current_path}")

    # Show candidate drafts (read-only).
    candidates = _candidate_drafts(ep)
    if candidates:
        print("[candidates] (newest first)")
        for p in candidates[:10]:
            print(f"- {_format_candidate(p)}")
    else:
        print("[candidates] none found under capcut_draft_root")

    # If no target draft is specified, exit after reporting.
    if not args.draft_dir:
        if bool(args.run):
            raise SystemExit("--run requires --draft-dir (explicit selection; no auto-pick).")
        return 0

    draft_dir = Path(str(args.draft_dir)).expanduser()
    if not draft_dir.exists() or not draft_dir.is_dir():
        raise SystemExit(f"draft_dir not found: {draft_dir}")

    # Guard: draft_dir should be under CapCut draft root (Hot local) or be explicitly allowed.
    capcut_root = capcut_draft_root().expanduser()
    try:
        draft_dir.relative_to(capcut_root)
    except Exception:
        # Allow, but warn (some environments use a local fallback root).
        print(f"[warn] draft_dir is outside capcut_draft_root: draft_dir={draft_dir} capcut_root={capcut_root}")

    capcut_link = run_dir / "capcut_draft"
    print(f"- will relink: {capcut_link} -> {draft_dir}")

    # Update draft_info payload (pathref is best-effort; only when resolvable).
    new_info = dict(info) if isinstance(info, dict) else {}
    new_info["draft_path"] = str(draft_dir)
    ref = best_effort_path_ref(draft_dir)
    if ref:
        new_info["draft_path_ref"] = ref

    if not bool(args.run):
        print("[dry-run] no changes applied")
        return 0

    # Apply: backup existing symlink then create a new one.
    backup = _backup_symlink(capcut_link)
    if backup:
        print(f"- backed up old symlink: {backup}")
    _write_symlink(capcut_link, draft_dir)
    print("- wrote symlink ok")

    # Write capcut_draft_info.json (if missing, create it).
    _atomic_write_json(info_path, new_info)
    print("- wrote capcut_draft_info.json ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
