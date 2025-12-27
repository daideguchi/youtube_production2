#!/usr/bin/env python3
"""
repo_sanity_audit — repo layout safety guard (SSOT compliance)

This script prevents accidental reintroduction of:
- tracked symlinks (git mode 120000)
- unexpected repo-root directories/symlinks (compat aliases / layout drift)

Usage:
  python3 scripts/ops/repo_sanity_audit.py
  python3 scripts/ops/repo_sanity_audit.py --verbose
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path
from typing import List, Tuple

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=False)

from factory_common.repo_layout import unexpected_repo_root_entries  # noqa: E402

_GRAVEYARD_MANIFEST_RE = re.compile(r"backups/graveyard/[A-Za-z0-9TZ_\\-]+/manifest\.tsv")


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
    failures = unexpected_repo_root_entries(REPO_ROOT)

    # Sensitive config: local file is allowed, symlink is not (it was previously absolute-path symlink).
    oauth = REPO_ROOT / "configs" / "drive_oauth_client.json"
    if oauth.is_symlink():
        failures.append(oauth)

    if not failures:
        if verbose:
            print("[ok] repo root layout drift: none")
        return 0

    print("[FAIL] unexpected repo-root directories/symlinks exist (SSOT drift):")
    for p in failures:
        label = p.relative_to(REPO_ROOT)
        if p.is_symlink():
            try:
                target = p.readlink()
            except Exception:
                target = "<unreadable>"
            print(f"  - {label} (symlink) -> {target}")
        elif p.is_dir():
            print(f"  - {label} (dir)")
        else:
            print(f"  - {label}")
    return 1


def check_graveyard_manifest_refs(*, verbose: bool) -> int:
    """
    Enforce SSOT reproducibility for archive-first deletions.

    SSOT (`ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md`) references manifest files under
    `backups/graveyard/**/manifest.tsv`. If those are missing, it becomes a
    multi-agent footgun (broken audit trail / restore path).
    """

    graveyard = REPO_ROOT / "backups" / "graveyard"
    if not graveyard.exists():
        print("[FAIL] backups/graveyard/ is missing (required for archive-first).")
        return 1

    cleanup_log = REPO_ROOT / "ssot" / "ops" / "OPS_CLEANUP_EXECUTION_LOG.md"
    if not cleanup_log.exists():
        print("[FAIL] SSOT cleanup log missing: ssot/ops/OPS_CLEANUP_EXECUTION_LOG.md")
        return 1

    text = cleanup_log.read_text(encoding="utf-8")
    refs = sorted(set(_GRAVEYARD_MANIFEST_RE.findall(text)))
    missing = [p for p in refs if not (REPO_ROOT / p).exists()]

    if missing:
        print("[FAIL] graveyard manifests referenced by SSOT are missing:")
        for p in missing:
            print(f"  - {p}")
        return 1

    if verbose:
        print(f"[ok] graveyard manifests: {len(refs)} referenced, all present")
    return 0


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Repo sanity audit (symlink + layout guard)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    rc = 0
    rc = max(rc, check_no_tracked_symlinks(verbose=bool(args.verbose)))
    rc = max(rc, check_no_legacy_alias_paths(verbose=bool(args.verbose)))
    rc = max(rc, check_graveyard_manifest_refs(verbose=bool(args.verbose)))
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
