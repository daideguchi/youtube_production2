#!/usr/bin/env python3
"""
repo_sanity_audit — repo layout safety guard (SSOT compliance)

This script prevents accidental reintroduction of:
- tracked symlinks (git mode 120000)
- legacy root-level alias paths (audio_tts_v2/, script_pipeline/, remotion/, ui/*, etc.)

Usage:
  python3 scripts/ops/repo_sanity_audit.py
  python3 scripts/ops/repo_sanity_audit.py --verbose
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import List, Tuple

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

# Legacy alias names that must not exist as symlinks at repo root.
LEGACY_ROOT_ALIASES = [
    "00_research",
    "progress",
    "thumbnails",
    "remotion",
    "script_pipeline",
    "audio_tts",
    "audio_tts_v2",
    "video_pipeline",
    "commentary_02_srt2images_timeline",
    "factory_common",
    "logs",
    "ui",
]

# Legacy alias paths under repo root.
LEGACY_SUBPATHS = [
    "ui/backend",
    "ui/frontend",
    "ui/tools",
]


def _git(args: List[str]) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def list_tracked_symlinks() -> List[Tuple[str, str]]:
    out = _git(["ls-files", "-s"])
    rows: List[Tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) != 4:
            continue
        mode, blob, _stage, path = parts
        if mode != "120000":
            continue
        target = _git(["cat-file", "-p", blob]).rstrip("\n")
        rows.append((path, target))
    rows.sort()
    return rows


def check_no_tracked_symlinks(*, verbose: bool) -> int:
    links = list_tracked_symlinks()
    if not links:
        if verbose:
            print("[ok] tracked symlinks: none")
        return 0

    print("[FAIL] tracked symlinks still present in git index:")
    for path, target in links:
        print(f"  - {path} -> {target}")
    return 1


def check_no_legacy_alias_paths(*, verbose: bool) -> int:
    failures: List[Tuple[str, Path]] = []

    for name in [*LEGACY_ROOT_ALIASES, *LEGACY_SUBPATHS]:
        p = REPO_ROOT / name
        # `Path.exists()` is False for broken symlinks; treat those as existing too.
        if p.exists() or p.is_symlink():
            failures.append((name, p))

    # Sensitive config: local file is allowed, symlink is not (it was previously absolute-path symlink).
    oauth = REPO_ROOT / "configs" / "drive_oauth_client.json"
    if oauth.is_symlink():
        failures.append(("configs/drive_oauth_client.json", oauth))

    if not failures:
        if verbose:
            print("[ok] legacy alias paths: none")
        return 0

    print("[FAIL] legacy alias paths exist at repo root (should not):")
    for label, p in failures:
        if p.is_symlink():
            try:
                target = p.readlink()
            except Exception:
                target = "<unreadable>"
            print(f"  - {label} (symlink) -> {target}")
        elif p.is_dir():
            print(f"  - {label} (dir)")
        else:
            print(f"  - {label} (file)")
    return 1


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Repo sanity audit (symlink + layout guard)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    rc = 0
    rc = max(rc, check_no_tracked_symlinks(verbose=bool(args.verbose)))
    rc = max(rc, check_no_legacy_alias_paths(verbose=bool(args.verbose)))
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
