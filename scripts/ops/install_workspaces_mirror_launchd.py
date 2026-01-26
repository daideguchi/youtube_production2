#!/usr/bin/env python3
"""
install_workspaces_mirror_launchd.py — install a Mac launchd agent to run workspaces_mirror periodically.

Goal:
- Keep Mac-local workspaces mirrored to the vault in the background
  (create/update + delete sync), without manual commands.

Policy:
- Writes only under ~/Library/LaunchAgents and workspaces/logs/...
- Does not require sudo.

SSOT:
- ssot/ops/OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL.md
"""

from __future__ import annotations

import argparse
import plistlib
import subprocess
import sys
from pathlib import Path

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=True)

from factory_common import paths as repo_paths  # noqa: E402


LABEL = "ytm.factory_commentary.workspaces_mirror"
VAULT_SENTINEL_NAME = ".ytm_vault_workspaces_root.json"


def _launchagents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _plist_path() -> Path:
    return _launchagents_dir() / f"{LABEL}.plist"


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _log_dir() -> Path:
    return repo_paths.logs_root() / "ops" / "workspaces_mirror"


def _write_plist(*, interval_sec: int, run: bool) -> None:
    _ensure_dir(_launchagents_dir())
    _ensure_dir(_log_dir())

    stdout_path = _log_dir() / "launchd_stdout.log"
    stderr_path = _log_dir() / "launchd_stderr.log"

    if run:
        dest_root = repo_paths.vault_workspaces_root()
        if dest_root is None:
            raise SystemExit(
                "[POLICY] vault workspaces root is not configured.\n"
                "- set YTM_VAULT_WORKSPACES_ROOT (or YTM_SHARED_STORAGE_ROOT so it can be derived)\n"
                "- then run: ./ops mirror workspaces -- --bootstrap-dest"
            )
        sentinel = dest_root / VAULT_SENTINEL_NAME
        if not sentinel.exists():
            raise SystemExit(
                "[POLICY] vault sentinel is missing (refusing to install run-mode daemon).\n"
                f"- dest_root: {dest_root}\n"
                f"- expected: {sentinel}\n"
                "- action: run: ./ops mirror workspaces -- --bootstrap-dest"
            )

    mirror_py = REPO_ROOT / "scripts" / "ops" / "workspaces_mirror.py"
    cmd = f"python3 {mirror_py}" + (" --run" if run else "")
    program = ["/bin/bash", "-lc", cmd]

    payload = {
        "Label": LABEL,
        "ProcessType": "Interactive",
        "ProgramArguments": program,
        "WorkingDirectory": str(REPO_ROOT),
        "RunAtLoad": True,
        "StartInterval": int(interval_sec),
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }
    _plist_path().write_bytes(plistlib.dumps(payload))


def _launchctl(cmd: list[str]) -> int:
    p = subprocess.run(cmd, check=False)
    return int(p.returncode)


def main() -> int:
    ap = argparse.ArgumentParser(description="Install/uninstall launchd agent for workspaces mirror (macOS only).")
    ap.add_argument("--interval-sec", type=int, default=600, help="Run interval seconds (default: 600).")
    ap.add_argument("--dry-run", action="store_true", help="Install in dry-run mode (no rsync).")
    ap.add_argument("--uninstall", action="store_true", help="Unload and remove the agent.")
    ap.add_argument("--load", action="store_true", help="Load the agent after writing plist (default: true).")
    ap.add_argument("--no-load", dest="load", action="store_false")
    ap.set_defaults(load=True)
    args = ap.parse_args()

    if bool(args.uninstall):
        # Unload if present (ignore failure).
        _launchctl(["launchctl", "unload", str(_plist_path())])
        if _plist_path().exists():
            _plist_path().unlink()
            print(f"[OK] removed: {_plist_path()}")
        else:
            print(f"[OK] not present: {_plist_path()}")
        return 0

    run = not bool(args.dry_run)
    _write_plist(interval_sec=int(args.interval_sec), run=run)
    print(f"[OK] wrote: {_plist_path()}")
    print(f"- interval_sec: {int(args.interval_sec)}")
    print(f"- mode: {'run' if run else 'dry-run'}")

    if bool(args.load):
        # `launchctl load` fails if already loaded; unload first (ignore failure).
        _launchctl(["launchctl", "unload", str(_plist_path())])
        rc = _launchctl(["launchctl", "load", str(_plist_path())])
        if rc != 0:
            print("[WARN] launchctl load failed. You can run it manually:", file=sys.stderr)
            print(f"  launchctl load {_plist_path()}", file=sys.stderr)
        else:
            print("[OK] launchctl load")
    else:
        print("[NOTE] skipped launchctl load (--no-load).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
