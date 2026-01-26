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
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402
from factory_common.publish_lock import is_episode_published_locked  # noqa: E402


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


def _shared_base_for(root: Path) -> Path:
    ns = repo_paths.shared_storage_namespace()
    if root.name == "uploads":
        return root / ns
    return root / "uploads" / ns


def _is_lenovo_share_stub(root: Path) -> bool:
    try:
        return (root / "README_MOUNTPOINT.txt").exists()
    except Exception:
        return False


def _fallback_shared_root(*, configured_root: Path) -> Path:
    override = str(os.getenv("YTM_SHARED_STORAGE_FALLBACK_ROOT") or "").strip()
    if override:
        return Path(override).expanduser()

    try:
        link = configured_root / "ytm_workspaces"
        if link.is_symlink():
            target = Path(os.readlink(link))
            if not target.is_absolute():
                target = (link.parent / target)
            share_root = target.parent
            if share_root.exists() and share_root.is_dir():
                return share_root
    except Exception:
        pass

    return Path.home() / "doraemon_hq" / "magic_files" / "_fallback_storage" / "lenovo_share_unavailable"


@dataclass(frozen=True)
class SharedCtx:
    configured_root: Path
    effective_root: Path
    base: Path
    offline_fallback: bool
    offline_reason: str | None


def _resolve_shared_ctx(*, run: bool) -> SharedCtx:
    configured = _shared_root_or_die()
    offline = False
    reason: str | None = None
    if _is_lenovo_share_stub(configured):
        offline = True
        reason = "mountpoint stub detected (README_MOUNTPOINT.txt)"
    elif sys.platform == "darwin":
        if "lenovo_share" in str(configured):
            # mirror logic from shared_storage_sync.py (best-effort)
            try:
                proc = subprocess.run(
                    ["/sbin/mount"],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    check=False,
                )
                mp = os.path.realpath(str(configured))
                needle = f" on {mp} "
                mounted = any(needle in line and "(smbfs," in line for line in (proc.stdout or "").splitlines())
            except Exception:
                mounted = False
            if not mounted:
                offline = True
                reason = "Lenovo SMB share not mounted (smbfs not detected)"

    effective = configured if not offline else _fallback_shared_root(configured_root=configured)
    base = _shared_base_for(effective)
    if bool(run) and bool(offline):
        (base / "manifests").mkdir(parents=True, exist_ok=True)
    return SharedCtx(
        configured_root=configured,
        effective_root=effective,
        base=base,
        offline_fallback=bool(offline),
        offline_reason=reason,
    )


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
    if bool(getattr(args, "allow_unposted", False)):
        cmd.append("--allow-unposted")
    if bool(args.run):
        cmd.append("--run")
    if bool(args.symlink_back) and bool(item.post_symlink_back):
        cmd.append("--symlink-back")
    p = subprocess.run(cmd, cwd=str(repo_paths.repo_root()))
    return int(p.returncode)


def cmd_run(args: argparse.Namespace) -> int:
    ch = _norm_channel(args.channel)
    vv = _norm_video(args.video)

    if bool(args.run) and bool(args.symlink_back):
        emergency = str(os.getenv("YTM_EMERGENCY_OVERRIDE") or "").strip() in {"1", "true", "TRUE", "yes", "YES"}
        if not bool(getattr(args, "allow_unposted", False)) and not emergency:
            if not is_episode_published_locked(ch, vv):
                raise SystemExit(
                    "[POLICY] Refusing --symlink-back for an unposted episode.\n"
                    f"- episode: {ch}-{vv}\n"
                    "- reason: Hot/Freeze assets must keep a Mac-local real copy (no external dependency).\n"
                    "- action: rerun without --symlink-back, or mark as published (publish_lock),\n"
                    "          or (break-glass) use --allow-unposted / YTM_EMERGENCY_OVERRIDE=1."
                )

    items = _plan_items(args)
    if not items:
        raise SystemExit("[POLICY] No L1 artifacts selected/found for this episode.")

    shared = _resolve_shared_ctx(run=bool(args.run))
    if bool(shared.offline_fallback) and bool(args.run) and bool(args.symlink_back):
        raise SystemExit(
            "[POLICY] Refusing --symlink-back while shared storage is offline.\n"
            f"- configured_root: {shared.configured_root}\n"
            f"- fallback_root:   {shared.effective_root}\n"
            "- action: rerun after share is mounted (or run without --symlink-back)."
        )

    if not bool(args.run):
        if bool(shared.offline_fallback):
            print("[OFFLINE] shared episode offload (fallback)")
            print(f"- configured_root: {shared.configured_root}")
            print(f"- effective_root:  {shared.effective_root}")
            if shared.offline_reason:
                print(f"- reason: {shared.offline_reason}")
        print("[DRY-RUN] shared episode offload")
        print(f"- episode: {ch}-{vv}")
        print(f"- shared_base: {shared.base}")
        print(f"- symlink_back: {bool(args.symlink_back)} (applies to heavy artifacts only)")
        for it in items:
            # Mirror shared_storage_sync.py destination logic.
            if it.dest_rel:
                dest = shared.base / Path(str(it.dest_rel).lstrip("/"))
            else:
                dest = shared.base / it.kind / ch / vv / it.src.name
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
    p.add_argument(
        "--allow-unposted",
        action="store_true",
        help="Allow --symlink-back even if episode is unposted (NOT recommended; requires explicit intent).",
    )
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
