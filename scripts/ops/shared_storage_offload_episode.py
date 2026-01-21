#!/usr/bin/env python3
"""
shared_storage_offload_episode.py — エピソード単位で L1 成果物を共有ストレージへ保存/退避する

SSOT:
  - ssot/ops/OPS_SHARED_ASSET_STORE.md

Policy:
  - workspaces/** はパス契約（SoT）。共有側は bytes store。
  - default は dry-run（何も書かない / 破壊操作もしない）
  - `--run` 指定時のみコピー+manifest作成を実行する
  - `--symlink-back` は明示時のみ（重いL1だけローカルをsymlink化してMac容量を空ける）
  - サイレントfallback禁止（共有root未設定/未マウントなら停止）
  - coordination locks は shared_storage_sync.py が尊重する（lock対象は停止）
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402


def _z3(video: str) -> str:
    return str(video).zfill(3)


def _norm_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    m = re.fullmatch(r"CH(\d{1,3})", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    if re.fullmatch(r"CH\d{2}", s):
        return s
    raise SystemExit(f"Invalid --channel: {raw!r} (expected CHxx)")


def _norm_video(raw: str) -> str:
    s = str(raw or "").strip()
    if not s:
        raise SystemExit(f"Invalid --video: {raw!r} (expected NNN)")
    v = _z3(s)
    if not re.fullmatch(r"\d{3}", v):
        raise SystemExit(f"Invalid --video: {raw!r} (expected NNN)")
    return v


def _shared_root_or_die() -> Path:
    root = repo_paths.shared_storage_root()
    if root is None:
        raise SystemExit("[POLICY] Missing YTM_SHARED_STORAGE_ROOT (shared storage root is required).")
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"[POLICY] YTM_SHARED_STORAGE_ROOT is not a directory: {root}")
    return root


def _shared_base_or_die() -> Path:
    _shared_root_or_die()
    base = repo_paths.shared_storage_base()
    if base is None:
        raise SystemExit("[POLICY] Missing YTM_SHARED_STORAGE_ROOT (shared storage root is required).")
    return base


@dataclass(frozen=True)
class Item:
    kind: str
    src: Path
    post_symlink_back: bool
    dest_rel: Optional[str] = None


def _expected_audio_paths(channel: str, video: str) -> list[Path]:
    d = repo_paths.audio_final_dir(channel, video)
    base = f"{channel}-{video}"
    return [
        d / f"{base}.wav",
        d / f"{base}.srt",
    ]


def _script_path(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "content" / "assembled_human.md"


def _remotion_mp4_path(run_id: str) -> Path:
    return repo_paths.video_run_dir(run_id) / "remotion" / "output" / "final.mp4"


def _require_file(path: Path, *, label: str) -> None:
    if not path.exists():
        raise SystemExit(f"[MISSING] {label}: {path}")
    if not path.is_file():
        raise SystemExit(f"[MISSING] {label} (not a file): {path}")


def _plan_items(args: argparse.Namespace) -> list[Item]:
    ch = _norm_channel(args.channel)
    vv = _norm_video(args.video)

    items: list[Item] = []

    if not bool(getattr(args, "skip_script", False)):
        p = _script_path(ch, vv)
        if p.exists() and p.is_file():
            items.append(Item(kind="scripts", src=p, post_symlink_back=False))
        elif not bool(args.allow_missing):
            _require_file(p, label="script (assembled_human.md)")

    if not bool(getattr(args, "skip_audio", False)):
        for p in _expected_audio_paths(ch, vv):
            if p.exists() and p.is_file():
                is_heavy = p.suffix.lower() == ".wav"
                items.append(Item(kind="audio_final", src=p, post_symlink_back=is_heavy))
            elif not bool(args.allow_missing):
                _require_file(p, label=f"audio_final ({p.name})")

    if bool(args.include_remotion):
        run_id = str(args.run_id or "").strip()
        if not run_id:
            raise SystemExit("[POLICY] --include-remotion requires --run-id <run_id>.")
        p = _remotion_mp4_path(run_id)
        if p.exists() and p.is_file():
            dest_rel = f"remotion_mp4/{ch}/{vv}/{run_id}.mp4"
            items.append(Item(kind="remotion_mp4", src=p, post_symlink_back=True, dest_rel=dest_rel))
        elif not bool(args.allow_missing):
            _require_file(p, label="remotion final.mp4")

    return items


def _dest_for(item: Item, *, channel: str, video: str) -> Path:
    base = _shared_base_or_die()
    if item.dest_rel:
        rel = Path(str(item.dest_rel).lstrip("/"))
        return base / rel
    return base / item.kind / channel / video / item.src.name


def _run_one(item: Item, *, channel: str, video: str, args: argparse.Namespace) -> int:
    cmd = [
        sys.executable,
        "scripts/ops/shared_storage_sync.py",
        "sync",
        "--kind",
        item.kind,
        "--src",
        str(item.src),
        "--channel",
        channel,
        "--video",
        video,
    ]
    if item.dest_rel:
        cmd += ["--dest-rel", str(item.dest_rel)]
    if bool(args.overwrite):
        cmd.append("--overwrite")
    if bool(args.ignore_locks):
        cmd.append("--ignore-locks")
    if bool(args.run):
        cmd.append("--run")
    if bool(args.symlink_back) and bool(item.post_symlink_back):
        cmd.append("--symlink-back")
    p = subprocess.run(cmd, cwd=str(repo_paths.repo_root()))
    return int(p.returncode)


def cmd_run(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    vv = _norm_video(args.video)

    items = _plan_items(args)
    if not items:
        raise SystemExit("[POLICY] No L1 artifacts selected/found for this episode.")

    # Validate shared storage early (no silent fallback).
    _shared_base_or_die()

    if not bool(args.run):
        print("[DRY-RUN] shared episode offload")
        print(f"- episode: {ch}-{vv}")
        print(f"- shared_base: {_shared_base_or_die()}")
        print(f"- symlink_back: {bool(args.symlink_back)} (applies to heavy artifacts only)")
        for it in items:
            dest = _dest_for(it, channel=ch, video=vv)
            post = "symlink-back" if (bool(args.symlink_back) and bool(it.post_symlink_back)) else "keep-local"
            print(f"- {it.kind}: {it.src} -> {dest} (post: {post})")
        print("tip: add --run to execute")
        return 0

    for it in items:
        rc = _run_one(it, channel=ch, video=vv, args=args)
        if rc != 0:
            print(f"[ERROR] shared episode offload failed (rc={rc}): {it.src}", file=sys.stderr)
            return rc
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Offload episode L1 artifacts to shared storage (dry-run by default)")
    p.add_argument("--channel", required=True, help="CHxx")
    p.add_argument("--video", required=True, help="NNN")
    p.add_argument("--run", action="store_true", help="Execute (default: dry-run)")
    p.add_argument("--symlink-back", action="store_true", help="After verified copy, symlink-back heavy L1 (wav/mp4)")
    p.add_argument("--overwrite", action="store_true", help="Overwrite destination if exists")
    p.add_argument("--ignore-locks", action="store_true", help="Ignore coordination locks (debug only)")
    p.add_argument("--allow-missing", action="store_true", help="Skip missing artifacts instead of stopping (not recommended)")

    p.add_argument("--skip-script", action="store_true", help="Skip script assembled_human.md (debug only)")
    p.add_argument("--skip-audio", action="store_true", help="Skip audio_final wav+srt (debug only)")
    p.add_argument("--include-remotion", action="store_true", help="Include Remotion final.mp4 (requires --run-id)")
    p.add_argument("--run-id", default="", help="Remotion run_id (required when --include-remotion)")

    p.set_defaults(func=cmd_run)
    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
