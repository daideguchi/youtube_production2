#!/usr/bin/env python3
"""
shared_storage_sync.py — 共有ストレージ（Tailscale常駐）へL1成果物を保存/退避する

SSOT:
  - ssot/ops/OPS_SHARED_ASSET_STORE.md

Policy:
  - workspaces/** はパス契約（正本）。L1の実体（bytes）は共有へ保存し、必要ならローカルを軽量化する。
  - default は dry-run（何も書かない）
  - `--run` 指定時のみ書き込みを実行
  - coordination locks を尊重し、ロック対象は停止する（--ignore-locks で例外）
  - サイレントfallback禁止（共有root未設定/未マウントなら停止）
  - atomic copy（tmp -> rename）+ sha256 manifest
  - `--move` / `--symlink-back` は明示時のみ（hash一致を確認してから実行）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock  # noqa: E402


def _now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _z3(video: str) -> str:
    return str(video).zfill(3)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _configured_shared_root() -> Path:
    root = repo_paths.shared_storage_root()
    if root is None:
        raise SystemExit("[POLICY] Missing YTM_SHARED_STORAGE_ROOT (shared storage root is required).")
    if not root.exists() or not root.is_dir():
        raise SystemExit(f"[POLICY] YTM_SHARED_STORAGE_ROOT is not a directory: {root}")
    return root


def _shared_namespace() -> str:
    return repo_paths.shared_storage_namespace()


def _shared_base_for(root: Path) -> Path:
    if root.name == "uploads":
        return root / _shared_namespace()
    return root / "uploads" / _shared_namespace()


def _is_lenovo_share_stub(root: Path) -> bool:
    """
    Convention: when Lenovo share is down, the mountpoint is replaced with a local stub
    containing README_MOUNTPOINT.txt to prevent accidental writes.
    """
    try:
        return (root / "README_MOUNTPOINT.txt").exists()
    except Exception:
        return False


def _is_smbfs_mounted(mountpoint: Path) -> bool:
    """
    Detect macOS smbfs mounts via `/sbin/mount`.
    Best-effort: returns False on any error or non-mac platforms.
    """
    if sys.platform != "darwin":
        return False
    try:
        proc = subprocess.run(
            ["/sbin/mount"],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
    except Exception:
        return False
    mp = os.path.realpath(str(mountpoint))
    needle = f" on {mp} "
    for line in (proc.stdout or "").splitlines():
        if needle in line and "(smbfs," in line:
            return True
    return False


def _fallback_shared_root(*, configured_root: Path) -> Path:
    override = str(os.getenv("YTM_SHARED_STORAGE_FALLBACK_ROOT") or "").strip()
    if override:
        return Path(override).expanduser()

    # Prefer the local backup share tree (when using the mountpoint stub convention).
    # Example:
    #   <stub>/ytm_workspaces -> <local_backup_share>/ytm_workspaces
    # so the effective share root is <local_backup_share>.
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
    # Default: stable local outbox (shared is optional; Mac must not stop).
    return Path.home() / "doraemon_hq" / "magic_files" / "_fallback_storage" / "lenovo_share_unavailable"


@dataclass(frozen=True)
class SharedCtx:
    configured_root: Path
    effective_root: Path
    base: Path
    namespace: str
    offline_fallback: bool
    offline_reason: str | None


def _resolve_shared_ctx(*, run: bool) -> SharedCtx:
    configured = _configured_shared_root()
    namespace = _shared_namespace()

    offline = False
    reason: str | None = None
    if _is_lenovo_share_stub(configured):
        offline = True
        reason = "mountpoint stub detected (README_MOUNTPOINT.txt)"
    elif sys.platform == "darwin":
        # When using the Lenovo share alias paths, require smbfs mount unless stub explicitly exists.
        if "lenovo_share" in str(configured) and not _is_smbfs_mounted(configured):
            offline = True
            reason = "Lenovo SMB share not mounted (smbfs not detected)"

    effective = configured if not offline else _fallback_shared_root(configured_root=configured)
    base = _shared_base_for(effective)
    if bool(run) and bool(offline):
        # Ensure outbox exists (local).
        _ensure_dir(base / "manifests")
    return SharedCtx(
        configured_root=configured,
        effective_root=effective,
        base=base,
        namespace=namespace,
        offline_fallback=bool(offline),
        offline_reason=reason,
    )


def _validate_channel(channel: Optional[str]) -> Optional[str]:
    if not channel:
        return None
    ch = str(channel).strip().upper()
    if not re.fullmatch(r"CH\d{2}", ch):
        raise SystemExit(f"Invalid --channel: {channel!r} (expected CHxx)")
    return ch


def _validate_video(video: Optional[str]) -> Optional[str]:
    if not video:
        return None
    v = _z3(str(video).strip())
    if not re.fullmatch(r"\d{3}", v):
        raise SystemExit(f"Invalid --video: {video!r} (expected NNN)")
    return v


@dataclass(frozen=True)
class SyncPlan:
    kind: str
    src: Path
    dest: Path
    sha256: str
    size_bytes: int


def _derive_dest_rel(kind: str, *, src: Path, channel: Optional[str], video: Optional[str]) -> Path:
    base = Path(str(kind or "misc").strip() or "misc")
    if channel and video:
        return base / channel / video / src.name
    if channel:
        return base / channel / src.name
    return base / src.name


def _plan_sync(
    *,
    kind: str,
    src: Path,
    dest_rel: Optional[str],
    channel: Optional[str],
    video: Optional[str],
    shared_base: Path,
) -> SyncPlan:
    if not src.exists():
        raise SystemExit(f"Source not found: {src}")
    if not src.is_file():
        raise SystemExit(f"Source must be a file: {src}")

    kind = str(kind or "misc").strip() or "misc"
    ch = _validate_channel(channel)
    vv = _validate_video(video)

    rel = Path(dest_rel) if (dest_rel and str(dest_rel).strip()) else _derive_dest_rel(kind, src=src, channel=ch, video=vv)
    rel = Path(str(rel).replace("\\\\", "/")).as_posix().lstrip("/")

    dest = shared_base / rel
    sha = _sha256_file(src)
    size = int(src.stat().st_size)
    return SyncPlan(kind=kind, src=src, dest=dest, sha256=sha, size_bytes=size)


def _atomic_copy(src: Path, dest: Path, *, overwrite: bool) -> None:
    _ensure_dir(dest.parent)
    if dest.exists() and not overwrite:
        raise SystemExit(f"Destination exists (use --overwrite): {dest}")
    tmp = dest.with_name(f".tmp__{dest.name}__{os.getpid()}")
    if tmp.exists():
        tmp.unlink()
    try:
        try:
            with src.open("rb") as fsrc, tmp.open("wb") as fdst:
                shutil.copyfileobj(fsrc, fdst, length=1024 * 1024)
                fdst.flush()
                os.fsync(fdst.fileno())
        except OSError as e:
            # Some network filesystems (SMB) can intermittently throw EIO on large writes.
            # Fall back to rsync (more resilient; supports partial/inplace).
            if getattr(e, "errno", None) not in (5, 22):
                raise
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            cmd = ["rsync", "--partial", "--inplace", str(src), str(tmp)]
            print(f"[shared_storage_sync] copyfileobj failed (errno={getattr(e,'errno',None)}); fallback to rsync", file=sys.stderr)
            try:
                proc = subprocess.run(cmd, check=False)
            except FileNotFoundError:
                raise
            if int(proc.returncode) != 0:
                raise SystemExit(f"[POLICY] rsync copy failed (rc={proc.returncode}): {dest}")
        tmp.replace(dest)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def _post_copy_verify_or_die(plan: SyncPlan) -> None:
    """
    Defensive check before any destructive local operation (move/symlink-back).
    """
    if not plan.dest.exists():
        raise SystemExit(f"[POLICY] post-copy verify failed: dest missing: {plan.dest}")
    if not plan.dest.is_file():
        raise SystemExit(f"[POLICY] post-copy verify failed: dest is not file: {plan.dest}")
    got = _sha256_file(plan.dest)
    if got != plan.sha256:
        raise SystemExit(f"[POLICY] post-copy verify failed: sha256 mismatch dest={got} expected={plan.sha256}")


def _replace_with_symlink(src: Path, dest: Path) -> None:
    if not src.exists():
        raise SystemExit(f"[POLICY] symlink-back failed: src missing: {src}")
    if src.is_dir():
        raise SystemExit(f"[POLICY] symlink-back failed: src is a directory: {src}")
    src.unlink()
    src.symlink_to(dest)


def _write_manifest(plan: SyncPlan, *, dest: Path, shared: SharedCtx) -> None:
    _ensure_dir(dest.parent)
    payload = {
        "schema_version": 1,
        "created_at": _now_iso_utc(),
        "kind": "shared_storage_sync",
        "sync_kind": plan.kind,
        "repo": {"root": str(repo_paths.repo_root()), "name": repo_paths.repo_root().name},
        "shared": {
            "configured_root": str(shared.configured_root),
            "effective_root": str(shared.effective_root),
            "namespace": shared.namespace,
            "base": str(shared.base),
            "offline_fallback": bool(shared.offline_fallback),
            "offline_reason": shared.offline_reason,
        },
        "host": {"hostname": socket.gethostname()},
        "artifact": {
            "src": str(plan.src),
            "dest": str(plan.dest),
            "sha256": plan.sha256,
            "size_bytes": plan.size_bytes,
        },
    }
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_sync(args: argparse.Namespace) -> int:
    src = Path(str(args.src)).expanduser()
    if not src.is_absolute():
        # Do not Path.resolve() here (network mounts/symlinks may block when offline).
        # We only need a stable absolute path independent of CWD.
        src = (repo_paths.repo_root() / src)

    shared = _resolve_shared_ctx(run=bool(args.run))
    if bool(shared.offline_fallback) and bool(args.run) and (bool(getattr(args, "symlink_back", False)) or bool(getattr(args, "move", False))):
        raise SystemExit(
            "[POLICY] Refusing --symlink-back/--move while shared storage is offline.\n"
            f"- configured_root: {shared.configured_root}\n"
            f"- fallback_root:   {shared.effective_root}\n"
            "- action: rerun after share is mounted (or store without symlink/move)."
        )

    plan = _plan_sync(
        kind=str(args.kind or "misc"),
        src=src,
        dest_rel=str(args.dest_rel or "").strip() or None,
        channel=str(args.channel or "").strip() or None,
        video=str(args.video or "").strip() or None,
        shared_base=shared.base,
    )

    if not bool(args.ignore_locks):
        lock = find_blocking_lock(plan.src, default_active_locks_for_mutation())
        if lock is not None:
            print("[LOCKED] Refusing to read/copy locked source.", file=sys.stderr)
            print(f"- src: {plan.src}", file=sys.stderr)
            print(f"- lock_id: {lock.lock_id}", file=sys.stderr)
            print(f"- created_by: {lock.created_by}", file=sys.stderr)
            note = getattr(lock, "note", None) or getattr(lock, "reason", None) or ""
            if note:
                print(f"- note: {note}", file=sys.stderr)
            return 2

    manifests_dir = shared.base / "manifests" / "shared_sync"
    manifest_name = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}__{plan.kind}__{plan.sha256[:12]}.json"
    manifest_path = manifests_dir / manifest_name

    if not bool(args.run):
        if bool(shared.offline_fallback):
            print("[OFFLINE] shared store (fallback)")
            print(f"- configured_root: {shared.configured_root}")
            print(f"- effective_root:  {shared.effective_root}")
            if shared.offline_reason:
                print(f"- reason: {shared.offline_reason}")
        print("[DRY-RUN] shared store")
        print(f"- src: {plan.src}")
        print(f"- dest: {plan.dest}")
        print(f"- sha256: {plan.sha256}")
        print(f"- size_bytes: {plan.size_bytes}")
        print(f"- manifest: {manifest_path}")
        if bool(getattr(args, "symlink_back", False)):
            print("- post: symlink-back (shared bytes + local path preserved)")
        elif bool(getattr(args, "move", False)):
            print("- post: move (delete local after verified copy)")
        return 0

    # Idempotency: if dest already exists and matches, treat as OK (no overwrite required).
    if plan.dest.exists() and not bool(args.overwrite):
        try:
            if plan.dest.is_file():
                got = _sha256_file(plan.dest)
                if got == plan.sha256:
                    _write_manifest(plan, dest=manifest_path, shared=shared)
                    if bool(getattr(args, "symlink_back", False)):
                        _replace_with_symlink(plan.src, plan.dest)
                    elif bool(getattr(args, "move", False)):
                        plan.src.unlink()
                    print("[OK] shared store (up-to-date)")
                    print(f"- dest: {plan.dest}")
                    return 0
        except Exception:
            pass

    _atomic_copy(plan.src, plan.dest, overwrite=bool(args.overwrite))
    _write_manifest(plan, dest=manifest_path, shared=shared)
    if bool(getattr(args, "symlink_back", False)) or bool(getattr(args, "move", False)):
        _post_copy_verify_or_die(plan)
        if bool(getattr(args, "symlink_back", False)):
            _replace_with_symlink(plan.src, plan.dest)
        else:
            plan.src.unlink()
    print("[OK] shared store")
    print(f"- dest: {plan.dest}")
    print(f"- manifest: {manifest_path}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Store/offload L1 artifacts to shared storage (keeps Mac disk free)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sync", help="sync one file to shared storage (atomic copy + manifest)")
    sp.add_argument("--kind", default="misc", help="artifact kind (folder under shared base)")
    sp.add_argument("--src", required=True, help="source file path (absolute or repo-relative)")
    sp.add_argument("--dest-rel", default=None, help="override destination path relative to shared base")
    sp.add_argument("--channel", default=None, help="CHxx (optional; used to derive dest)")
    sp.add_argument("--video", default=None, help="NNN (optional; used to derive dest)")
    sp.add_argument("--overwrite", action="store_true", help="overwrite destination if exists")
    sp.add_argument("--ignore-locks", action="store_true", help="ignore coordination locks (debug only)")
    sp.add_argument("--move", action="store_true", help="delete local source after verified copy (dangerous)")
    sp.add_argument("--symlink-back", action="store_true", help="replace local source with symlink to shared (dangerous)")
    sp.add_argument("--run", action="store_true", help="execute (default: dry-run)")
    sp.set_defaults(func=cmd_sync)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
