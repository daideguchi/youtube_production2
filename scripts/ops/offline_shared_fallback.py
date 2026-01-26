#!/usr/bin/env python3
"""
offline_shared_fallback.py — make offloaded/symlinked SoT paths usable while Lenovo SMB is down.

Problem
-------
Some `workspaces/**` directories may be symlinked into the Lenovo shared storage tree
(e.g. `$YTM_SHARED_STORAGE_ROOT/archive/mac_assets/...`).
When the SMB share is temporarily unavailable, those symlinks become broken and upstream
commands fail early.

This tool materializes *local* fallbacks under `workspaces/video/input/`
**only when the share is NOT mounted**, so upstream commands keep working offline
without depending on the external share.

Policy
------
- No-op when the share is mounted (never writes into the real SMB share).
- Dry-run by default; pass `--run` to apply.
- Never deletes user data. Offloaded symlinks are renamed to a backup name before materializing.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=True)

from factory_common import paths as repo_paths  # noqa: E402


REPORT_SCHEMA = "ytm.ops.offline_shared_fallback.v1"

def _norm_key(s: str) -> str:
    # macOS HFS/APFS may surface NFD-ish forms depending on origin; normalize for stable matching.
    return unicodedata.normalize("NFC", str(s))

def _now_compact_utc() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _is_mounted_smbfs(mountpoint: Path) -> bool:
    """
    Detect macOS smbfs mounts via `/sbin/mount`.
    Best-effort: returns False on any error or non-mac platforms.
    """
    if sys.platform != "darwin":
        return False

    try:
        proc = subprocess.run(
            ["/sbin/mount"],
            cwd=str(REPO_ROOT),
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


def _default_shared_root() -> Path:
    env = (os.getenv("YTM_SHARED_STORAGE_ROOT") or "").strip()
    if env:
        return Path(env).expanduser()
    # Common aliases on this repo/host.
    alias = Path.home() / "mounts" / "lenovo_share"
    if alias.exists():
        return alias
    return Path.home() / "mounts" / "lenovo_share_real"


def _repo_rel_under_home(repo_root: Path) -> Path:
    """
    Matches the existing offload convention:
      <share>/archive/mac_assets/<HOME_REL_REPO>/workspaces/...

    If the repo isn't under $HOME, fall back to repo name (best-effort).
    """
    home = Path.home().expanduser().resolve()
    try:
        return repo_root.resolve().relative_to(home)
    except Exception:
        return Path(repo_root.name)


def _readlink_abs(p: Path) -> Path | None:
    if not p.is_symlink():
        return None
    try:
        raw = os.readlink(p)
    except OSError:
        return None
    t = Path(raw)
    return t if t.is_absolute() else (p.parent / t)


def _iter_video_archive_roots() -> list[Path]:
    """
    Archive roots to search for restore sources.

    Priority: newest stamp wins across all roots.
    Sources:
    - local workspace archive: <workspace_root>/video/_archive
    - vault mirror archive (if available): <vault_workspaces_root>/video/_archive
    """
    roots: list[Path] = []

    local = repo_paths.workspace_root() / "video" / "_archive"
    try:
        if local.exists() and local.is_dir():
            roots.append(local)
    except Exception:
        pass

    vault_root = repo_paths.vault_workspaces_root()
    if vault_root is not None:
        vault = vault_root / "video" / "_archive"
        try:
            if vault.exists() and vault.is_dir():
                roots.append(vault)
        except Exception:
            pass

    return roots


def _collect_video_input_archives(archive_roots: list[Path]) -> dict[str, Path]:
    """
    Build a map: <video_input_dir_name> -> <best local archive path>.

    Local archive layout:
      workspaces/video/_archive/<STAMP>/<CH>/video_input/<CH>_<PresetName>/
    """
    out: dict[str, Path] = {}

    stamp_dirs: list[Path] = []
    for root in archive_roots:
        try:
            if not root.exists() or not root.is_dir():
                continue
            for p in root.iterdir():
                if p.is_dir():
                    stamp_dirs.append(p)
        except Exception:
            continue

    # Newest stamp wins (folder name is a sortable timestamp).
    stamps = sorted(stamp_dirs, key=lambda p: p.name, reverse=True)
    for stamp_dir in stamps:
        try:
            ch_dirs = [p for p in stamp_dir.iterdir() if p.is_dir()]
        except Exception:
            continue
        for ch_dir in ch_dirs:
            video_input_root = ch_dir / "video_input"
            try:
                if not video_input_root.exists() or not video_input_root.is_dir():
                    continue
            except Exception:
                continue
            try:
                input_dirs = [p for p in video_input_root.iterdir() if p.is_dir()]
            except Exception:
                continue
            for input_dir in input_dirs:
                name = _norm_key(input_dir.name)
                # Keep first seen (newest stamp).
                out.setdefault(name, input_dir)
    return out


def _unique_backup_path(p: Path, *, stamp: str) -> Path:
    """
    Choose a non-existing backup path in the same directory.
    """
    base = p.with_name(f"{p.name}.symlink_shared_backup_{stamp}")
    if not base.exists() and not base.is_symlink():
        return base
    for i in range(1, 1000):
        cand = p.with_name(f"{p.name}.symlink_shared_backup_{stamp}__{i}")
        if not cand.exists() and not cand.is_symlink():
            return cand
    # Last resort: include pid
    return p.with_name(f"{p.name}.symlink_shared_backup_{stamp}__pid{os.getpid()}")


def _write_offline_placeholder(dest_dir: Path, *, stamp: str, link: Path, target: Path) -> None:
    """
    Write a small marker file explaining why this directory exists.

    This helps humans distinguish "real input dir" vs "offline materialized placeholder".
    """
    try:
        marker = dest_dir / "README_OFFLINE_FALLBACK.txt"
        marker.write_text(
            "\n".join(
                [
                    "offline_shared_fallback: placeholder directory",
                    f"- created_at_utc: {stamp}",
                    f"- link: {link}",
                    f"- original_target: {target}",
                    "",
                    "Why:",
                    "- The Lenovo SMB share is offline, and this path was a broken symlink into the shared archive.",
                    "- workspaces/video/input is a mirror (NOT SoT). We materialize it locally so Mac hot work doesn't stop.",
                    "",
                    "Next:",
                    "- If you need inputs here, re-sync from SoT (audio final) via:",
                    "    PYTHONPATH='.:packages' python3 -m video_pipeline.tools.sync_audio_inputs",
                    "",
                    "Safety:",
                    "- The original symlink was renamed to *.symlink_shared_backup_* before creating this directory.",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
    except Exception:
        # best-effort marker
        pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Materialize local fallbacks for offloaded symlinked SoT paths (safe by default)."
    )
    ap.add_argument("--run", action="store_true", help="Apply changes (default: dry-run).")
    ap.add_argument("--json", action="store_true", help="Emit JSON report.")
    ap.add_argument(
        "--materialize",
        choices=["copy", "symlink"],
        default="copy",
        help="How to materialize fallbacks into workspaces/video/input (default: copy).",
    )
    ap.add_argument(
        "--shared-root",
        default="",
        help="Override shared root (default: YTM_SHARED_STORAGE_ROOT or ~/mounts/lenovo_share_real).",
    )
    args = ap.parse_args()

    shared_root = Path(str(args.shared_root).strip()).expanduser() if str(args.shared_root).strip() else _default_shared_root()
    mounted = _is_mounted_smbfs(shared_root) or os.path.ismount(str(shared_root))

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "run": bool(args.run),
        "repo_root": str(REPO_ROOT),
        "shared_root": str(shared_root),
        "shared_root_mounted": bool(mounted),
        "materialize": str(args.materialize),
        "actions": [],
        "warnings": [],
    }

    if mounted:
        report["warnings"].append("shared root appears mounted; skipping (will not write into SMB share).")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print("[offline_shared_fallback] share is mounted; skip.")
        return 0

    stamp = _now_compact_utc()

    repo_rel = _repo_rel_under_home(REPO_ROOT)
    mac_assets_root = shared_root / "archive" / "mac_assets" / repo_rel / "workspaces"
    report["paths"] = {
        "repo_rel_under_home": str(repo_rel),
        "mac_assets_workspaces_root": str(mac_assets_root),
    }

    archive_roots = _iter_video_archive_roots()
    report["paths"]["video_input_archive_roots"] = [str(p) for p in archive_roots]
    local_archives = _collect_video_input_archives(archive_roots)
    report.setdefault("stats", {})["video_input_archives_found"] = len(local_archives)

    # 1) Fix workspaces/video/input symlinks that point into the shared archive but are currently broken.
    video_input_root = repo_paths.video_input_root()
    expected_prefix = mac_assets_root / "video" / "input"

    if not video_input_root.exists():
        report["warnings"].append(f"video_input_root missing: {video_input_root}")
    else:
        ignored_backup_symlinks = 0
        materialized_from_archive = 0
        materialized_empty = 0
        for child in sorted(video_input_root.iterdir()):
            if not child.is_symlink():
                continue
            if ".symlink_shared_backup_" in child.name:
                ignored_backup_symlinks += 1
                continue
            target = _readlink_abs(child)
            if target is None:
                continue
            # Only operate on the specific offload convention under <share>/archive/mac_assets/<repo_rel>/workspaces/video/input/
            try:
                _ = target.relative_to(expected_prefix)
            except Exception:
                continue

            if target.exists():
                report["actions"].append(
                    {"action": "video_input_link_ok", "link": str(child), "target": str(target)}
                )
                continue

            src = local_archives.get(_norm_key(child.name))
            backup = _unique_backup_path(child, stamp=stamp)
            if src is None:
                report["warnings"].append(f"no local archive for video/input dir: {child.name} (will create empty dir)")
                report["actions"].append(
                    {
                        "action": "video_input_materialize_empty_dir_plan",
                        "link": str(child),
                        "target": str(target),
                        "backup": str(backup),
                    }
                )
            else:
                report["actions"].append(
                    {
                        "action": "video_input_materialize_plan",
                        "link": str(child),
                        "target": str(target),
                        "src": str(src),
                        "backup": str(backup),
                        "mode": str(args.materialize),
                    }
                )

            if not bool(args.run):
                continue

            # Move the offloaded symlink aside (non-destructive).
            try:
                child.rename(backup)
                report["actions"].append({"action": "video_input_symlink_backed_up", "from": str(child), "to": str(backup)})
            except Exception as e:  # noqa: BLE001
                report["warnings"].append(f"failed to backup symlink: {child} ({e})")
                continue

            # Materialize local fallback.
            try:
                if src is None:
                    child.mkdir(parents=False, exist_ok=False)
                    _write_offline_placeholder(child, stamp=stamp, link=backup, target=target)
                    materialized_empty += 1
                    report["actions"].append({"action": "video_input_materialized_empty_dir", "dest": str(child)})
                else:
                    if str(args.materialize) == "symlink":
                        os.symlink(str(src), str(child))
                    else:
                        shutil.copytree(src, child)
                    materialized_from_archive += 1
                    report["actions"].append({"action": "video_input_materialized", "dest": str(child), "src": str(src)})
            except Exception as e:  # noqa: BLE001
                report["warnings"].append(f"failed to materialize fallback: {child} <- {src} ({e})")
                # Best-effort rollback: restore original symlink name.
                try:
                    if child.exists() or child.is_symlink():
                        if child.is_dir() and not child.is_symlink():
                            shutil.rmtree(child)
                        else:
                            child.unlink()
                    backup.rename(child)
                except Exception:
                    pass
        report.setdefault("stats", {})["video_input_backup_symlinks_ignored"] = int(ignored_backup_symlinks)
        report.setdefault("stats", {})["video_input_materialized_from_archive"] = int(materialized_from_archive)
        report.setdefault("stats", {})["video_input_materialized_empty_dir"] = int(materialized_empty)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("[offline_shared_fallback]")
        print(f"- shared_root: {shared_root} (mounted={mounted})")
        print(f"- video_input_root: {video_input_root}")
        planned = [a for a in report["actions"] if a.get("action") == "video_input_materialize_plan"]
        missing = [a for a in report["actions"] if a.get("action") == "video_input_broken_no_archive"]
        print(f"- materialize_planned: {len(planned)}")
        print(f"- missing_no_archive: {len(missing)}")
        if report["warnings"]:
            print("[warnings]")
            for w in report["warnings"]:
                print(f"- {w}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
