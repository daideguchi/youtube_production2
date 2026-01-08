#!/usr/bin/env python3
"""
git_write_lock — prevent accidental git rollbacks by write-locking `.git/`.

Why:
  In multi-agent environments, a single `git restore/checkout/reset` can wipe hours of work.
  This guard locks the repository metadata (`.git/`) so operations that need to write git
  metadata (e.g. checkout/reset that update refs/index) fail immediately.

Notes:
  - This does NOT affect normal file edits (agents can still modify files directly).
  - This does NOT block worktree-only rollbacks such as `git restore <file>` (no `.git` writes).
    For those, rely on the Codex Git Guard / execpolicy (see `ssot/ops/OPS_GIT_SAFETY.md`).
  - To commit/push, unlock temporarily, then re-lock after push.
  - Some environments forbid modifying `.git` metadata (chmod/chflags). In that case, this
    command becomes a no-op and rollback prevention relies on the Codex execpolicy.

Usage:
  python3 scripts/ops/git_write_lock.py status
  python3 scripts/ops/git_write_lock.py lock
  python3 scripts/ops/git_write_lock.py unlock
  python3 scripts/ops/git_write_lock.py unlock-for-push
"""

from __future__ import annotations

import argparse
import os
import stat as stat_mod
import subprocess
import sys
from datetime import datetime, timezone
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


def _orchestrator_lease_is_held() -> tuple[bool, str]:
    """
    Best-effort: treat `.git` unlock as allowed only when the single-orchestrator
    lease is held.

    This reduces the chance that a random worker unlocks `.git` and performs a
    rollback in a parallel multi-agent run.
    """
    try:
        import fcntl  # unix-only
    except Exception:
        return False, "fcntl unavailable (cannot validate orchestrator lease)"

    try:
        from factory_common.paths import logs_root

        lock_path = logs_root() / "agent_tasks" / "coordination" / "orchestrator" / "lease.lock"
    except Exception as exc:
        return False, f"cannot resolve orchestrator lease lock path: {exc}"

    if not lock_path.exists():
        return False, f"orchestrator lease lock missing: {lock_path}"

    lock_f = lock_path.open("a")
    try:
        try:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True, "lease held"
        return False, "lease not held"
    finally:
        try:
            lock_f.close()
        except Exception:
            pass


def _orchestrator_state_summary() -> str:
    try:
        import json
        from factory_common.paths import logs_root

        state_path = logs_root() / "agent_tasks" / "coordination" / "orchestrator" / "state.json"
        if not state_path.exists():
            return "(no state.json)"

        st = json.loads(state_path.read_text(encoding="utf-8"))
        name = st.get("name")
        pid = st.get("pid")
        last = st.get("last_heartbeat_at")
        age = None
        try:
            if isinstance(last, str):
                dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                age = int((datetime.now(timezone.utc) - dt).total_seconds())
        except Exception:
            age = None
        return f"name={name} pid={pid} heartbeat_age_sec={age}"
    except Exception:
        return "(unreadable state.json)"


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

    # If creation is denied, explain the best-known reason.
    if _is_darwin() and _darwin_has_immutable_flag(git_dir):
        print("locked (immutable)")
        return 0
    if _chmod_is_locked(git_dir):
        print("locked (chmod)")
        return 0

    # If permissions look writable but creation is denied, it's typically an external restriction
    # (sandbox / OS provenance).
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
        try:
            imm = int(getattr(stat_mod, "UF_IMMUTABLE", 0) or 0)
            if imm:
                st = os.stat(git_dir)
                os.chflags(git_dir, int(getattr(st, "st_flags", 0)) | imm)
                print("locked")
                return 0
        except Exception:
            # Fall through to chmod lock.
            pass

    # chmod fallback: remove owner write bit on `.git/` directory.
    st = os.stat(git_dir)
    new_mode = st.st_mode & ~stat_mod.S_IWUSR
    if not _try_chmod(git_dir, new_mode):
        print("[FAIL] cannot lock .git (operation not permitted in this environment)", file=sys.stderr)
        return 2

    print("locked")
    return 0


def _unlock_git_dir(*, require_orchestrator_lease: bool) -> int:
    git_dir = _git_dir()
    if not git_dir.exists():
        print("[FAIL] .git/ not found (not a git repo?)", file=sys.stderr)
        return 2
    if not is_locked(git_dir):
        print("unlocked")
        return 0

    if require_orchestrator_lease:
        held, note = _orchestrator_lease_is_held()
        if not held:
            print(
                "[FAIL] refusing to unlock .git without an active orchestrator lease (rollback prevention).",
                file=sys.stderr,
            )
            print(
                "hint: start orchestrator -> python3 scripts/agent_org.py orchestrator start --name dd-orch",
                file=sys.stderr,
            )
            print(f"orchestrator_state: {_orchestrator_state_summary()}", file=sys.stderr)
            print(f"lease_check: {note}", file=sys.stderr)
            return 2

    if _is_darwin():
        try:
            imm = int(getattr(stat_mod, "UF_IMMUTABLE", 0) or 0)
            if imm:
                st = os.stat(git_dir)
                os.chflags(git_dir, int(getattr(st, "st_flags", 0)) & ~imm)
        except Exception:
            pass

    st = os.stat(git_dir)
    new_mode = st.st_mode | stat_mod.S_IWUSR
    if not _try_chmod(git_dir, new_mode):
        print("[FAIL] cannot unlock .git (run outside Codex / remove sandbox restrictions)", file=sys.stderr)
        return 2

    print("unlocked")
    return 0


def cmd_unlock(_args: argparse.Namespace) -> int:
    # Human-friendly: allow unlocking without orchestrator lease.
    # Rollback prevention for agents should rely on Codex Git Guard / execpolicy.
    return _unlock_git_dir(require_orchestrator_lease=False)


def cmd_unlock_for_push(_args: argparse.Namespace) -> int:
    # Safer unlock path (for orchestrator workflows).
    return _unlock_git_dir(require_orchestrator_lease=True)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Write-lock/unlock .git to prevent destructive rollbacks.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status", help="print locked/unlocked")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("lock", help="lock .git (destructive git operations will fail)")
    sp.set_defaults(func=cmd_lock)

    sp = sub.add_parser("unlock", help="unlock .git (required before commit/push)")
    sp.set_defaults(func=cmd_unlock)

    sp = sub.add_parser("unlock-for-push", help="unlock .git (requires orchestrator lease; safer)")
    sp.set_defaults(func=cmd_unlock_for_push)

    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
