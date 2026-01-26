#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return None


def _default_root() -> Path:
    env = (os.getenv("YTM_SHARED_STORAGE_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    alias = Path.home() / "mounts" / "lenovo_share"
    if alias.exists():
        return alias
    return Path.home() / "mounts" / "lenovo_share_real"


def _is_smbfs_mounted(mountpoint: Path) -> bool:
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


def _ensure_mounted(*, root: Path, timeout_sec: int) -> tuple[bool, str | None]:
    if _is_smbfs_mounted(root):
        return True, None

    script = Path.home() / "bin" / "mount-lenovo-doraemon-share.sh"
    if not script.exists():
        return False, f"missing mount script: {script}"

    try:
        subprocess.run([str(script)], timeout=timeout_sec, check=False)
    except subprocess.TimeoutExpired:
        return False, f"mount timeout after {timeout_sec}s"
    except Exception as e:  # noqa: BLE001
        return False, f"mount error: {e}"

    if _is_smbfs_mounted(root):
        return True, None
    return False, "mount did not appear"


def _fallback_root() -> Path:
    base = Path.home() / "doraemon_hq" / "magic_files" / "_fallback_storage"
    base.mkdir(parents=True, exist_ok=True)
    return base / "lenovo_share_unavailable"


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolve Lenovo share root (and optionally mount it).")
    ap.add_argument("--root", default="", help="Override root path (default: YTM_SHARED_STORAGE_ROOT or ~/mounts/lenovo_share).")
    ap.add_argument("--ensure-mounted", action="store_true", help="Attempt to mount Lenovo share if missing.")
    ap.add_argument("--timeout-sec", type=int, default=25, help="Mount attempt timeout (sec).")
    ap.add_argument("--json", action="store_true", help="Print JSON payload instead of plain path.")
    ap.add_argument("--allow-fallback", action="store_true", help="When not mounted, return a local fallback directory.")
    args = ap.parse_args()

    root = Path(str(args.root).strip()).expanduser() if str(args.root).strip() else _default_root()
    mounted = _is_smbfs_mounted(root)
    note: str | None = None

    if bool(args.ensure_mounted) and not mounted:
        mounted, note = _ensure_mounted(root=root, timeout_sec=int(args.timeout_sec))

    resolved_root = root
    fallback = None
    if not mounted and bool(args.allow_fallback):
        fallback = _fallback_root()
        fallback.mkdir(parents=True, exist_ok=True)
        resolved_root = fallback

    if bool(args.json):
        payload = {
            "ts": _now_iso_utc(),
            "root": str(root),
            "mounted": mounted,
            "resolved_root": str(resolved_root),
            "fallback_root": str(fallback) if fallback else None,
            "note": note,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    print(str(resolved_root))
    if not mounted and not bool(args.allow_fallback):
        print(f"[WARN] Lenovo share not mounted at: {root}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

