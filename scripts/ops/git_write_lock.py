#!/usr/bin/env python3
"""
git_write_lock — prevent accidental git rollbacks by write-locking `.git/`.

Why:
  In multi-agent environments, a single `git restore/checkout/reset` can wipe hours of work.
  This guard locks the repository metadata so destructive git operations fail immediately.

Notes:
  - This does NOT affect normal file edits (agents can still modify files directly).
  - To commit/push, unlock temporarily, then re-lock after push.
  - Some environments forbid modifying `.git` metadata (chmod/chflags). In that case, this
    command becomes a no-op and rollback prevention relies on the Codex execpolicy.

Usage:
  python3 scripts/ops/git_write_lock.py status
  python3 scripts/ops/git_write_lock.py lock
  python3 scripts/ops/git_write_lock.py unlock
"""

from __future__ import annotations

import argparse
import os
import stat as stat_mod
import subprocess
import sys
from pathlib import Path

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)


def _git_dir() -> Path:
    return REPO_ROOT / ".git"


def _is_darwin() -> bool:
    return sys.platform == "darwin"


def _darwin_has_immutable_flag(path: Path) -> bool:
    st = os.stat(path)
    flag = getattr(stat_mod, "UF_IMMUTABLE", None)
    if flag is None:
        return False
    return bool(getattr(st, "st_flags", 0) & int(flag))


def _chmod_is_locked(path: Path) -> bool:
    # best-effort: considers locked if owner write is missing
    st = os.stat(path)
    return not bool(st.st_mode & stat_mod.S_IWUSR)


def _probe_can_create(git_dir: Path) -> bool:
    """
    Try creating a temporary file under `.git/` (then remove it).
    If creation is denied, treat `.git/` as effectively locked.
    """
    probe = git_dir / f"__codex_git_write_probe__{os.getpid()}"
    try:
        fd = os.open(str(probe), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        probe.unlink(missing_ok=True)
        return True
    except FileExistsError:
        # Extremely unlikely; treat as locked until manually cleaned.
        return False
    except PermissionError:
        return False
    except OSError:
        return False


def is_locked(git_dir: Path) -> bool:
    # Primary signal: can we create a lock/probe file in `.git/`?
    if not _probe_can_create(git_dir):
        return True
    # Secondary signal: immutable flag / chmod based lock.
    if _is_darwin():
        return _darwin_has_immutable_flag(git_dir) or _chmod_is_locked(git_dir)
    return _chmod_is_locked(git_dir)


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=str(REPO_ROOT), check=True, capture_output=True, text=True)


def _try_run(cmd: list[str]) -> bool:
    p = subprocess.run(cmd, cwd=str(REPO_ROOT), check=False, capture_output=True, text=True)
    return p.returncode == 0


def _try_chmod(path: Path, mode: int) -> bool:
    try:
        os.chmod(path, mode)
        return True
    except PermissionError:
        return False
    except OSError:
        return False


def cmd_status(_args: argparse.Namespace) -> int:
    git_dir = _git_dir()
    if not git_dir.exists():
        print("[FAIL] .git/ not found (not a git repo?)", file=sys.stderr)
        return 2
    can_create = _probe_can_create(git_dir)
    if can_create:
        print("unlocked")
        return 0

    # If permissions look writable but creation is denied, it's typically an external restriction
    # (sandbox / OS provenance) rather than our own chmod/chflags lock.
    st = os.stat(git_dir)
    writable_bit = bool(st.st_mode & stat_mod.S_IWUSR)
    if writable_bit:
        print("locked (external)")
    else:
        print("locked")
    return 0


def cmd_lock(_args: argparse.Namespace) -> int:
    git_dir = _git_dir()
    if not git_dir.exists():
        print("[FAIL] .git/ not found (not a git repo?)", file=sys.stderr)
        return 2
    if is_locked(git_dir):
        print("locked")
        return 0

    # Prefer locking the directory itself (avoid noisy recursive operations).
    if _is_darwin():
        if _try_run(["chflags", "uchg", str(git_dir)]):
            print("locked")
            return 0

    # chmod fallback: remove owner write bit on `.git/` directory.
    st = os.stat(git_dir)
    new_mode = st.st_mode & ~stat_mod.S_IWUSR
    if not _try_chmod(git_dir, new_mode):
        print("[FAIL] cannot lock .git (operation not permitted in this environment)", file=sys.stderr)
        return 2

    print("locked")
    return 0


def cmd_unlock(_args: argparse.Namespace) -> int:
    git_dir = _git_dir()
    if not git_dir.exists():
        print("[FAIL] .git/ not found (not a git repo?)", file=sys.stderr)
        return 2
    if not is_locked(git_dir):
        print("unlocked")
        return 0

    if _is_darwin():
        _try_run(["chflags", "nouchg", str(git_dir)])

    st = os.stat(git_dir)
    new_mode = st.st_mode | stat_mod.S_IWUSR
    if not _try_chmod(git_dir, new_mode):
        print("[FAIL] cannot unlock .git (run outside Codex / remove sandbox restrictions)", file=sys.stderr)
        return 2

    print("unlocked")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Write-lock/unlock .git to prevent destructive rollbacks.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status", help="print locked/unlocked")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("lock", help="lock .git (destructive git operations will fail)")
    sp.set_defaults(func=cmd_lock)

    sp = sub.add_parser("unlock", help="unlock .git (required before commit/push)")
    sp.set_defaults(func=cmd_unlock)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
