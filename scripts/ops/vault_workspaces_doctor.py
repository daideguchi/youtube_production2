#!/usr/bin/env python3
"""
vault_workspaces_doctor.py — normalize Vault(=shared) workspaces layout for multi-host usage.

Why this exists:
- Vault `ytm_workspaces/` is mounted on **Mac** and **Acer** under different absolute paths.
- If Vault contains symlinks whose targets are absolute (e.g. `/Users/.../mounts/...`),
  those links break on the other host → `/files` and UI show missing assets.

What this tool does:
- Rewrite such symlinks into **share-internal relative symlinks** (portable).
- Ensure canonical paths exist so UI/agents don't get lost:
  - `ytm_workspaces/thumbnails/assets/`
  - `ytm_workspaces/video/runs/`
  - If `ytm_workspaces/scripts/` is empty, optionally point it to offload scripts (safe backup first).

Safety:
- Dry-run by default. Use `--run` to apply.

SSOT:
- ssot/ops/OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL.md
- ssot/plans/PLAN_CAPCUT_HOT_VAULT_ROLLOUT.md
- ssot/history/HISTORY_20260124_capcut_vault_mirror.md
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=True)

from factory_common import paths as repo_paths  # noqa: E402


REPORT_SCHEMA = "ytm.ops.vault_workspaces_doctor.v1"


def _now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_compact_utc() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _logs_dir() -> Path:
    return repo_paths.logs_root() / "ops" / "vault_workspaces_doctor"


def _report_path(stamp: str) -> Path:
    return _logs_dir() / f"vault_workspaces_doctor__{stamp}.json"


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _readlink(p: Path) -> str:
    return os.readlink(str(p))


def _resolve_link_target_abs(*, link_path: Path, target: str) -> Path:
    if os.path.isabs(target):
        return Path(target)
    # Relative link: resolve from its parent dir (without requiring realpath semantics).
    return (link_path.parent / target).resolve()


def _rel_target_within_share(*, shared_root: Path, link_path: Path, target: str) -> str | None:
    """
    If target is an absolute path under shared_root, return a relative target path for link_path.
    Otherwise return None (do not touch).
    """
    if not os.path.isabs(target):
        return None
    try:
        rel = Path(target).resolve().relative_to(shared_root.resolve())
    except Exception:
        return None
    abs_target = (shared_root / rel).resolve()
    return os.path.relpath(str(abs_target), start=str(link_path.parent))


def _rewrite_symlink(*, path: Path, new_target: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        if tmp.exists() or tmp.is_symlink():
            tmp.unlink()
    except Exception:
        pass
    os.symlink(new_target, str(tmp))
    os.replace(str(tmp), str(path))


def _normalize_one_symlink(*, shared_root: Path, link_path: Path, run: bool) -> dict[str, Any]:
    before = _readlink(link_path)
    after = _rel_target_within_share(shared_root=shared_root, link_path=link_path, target=before)
    if after is None or after == before:
        return {"path": str(link_path), "changed": False, "before": before, "after": before}
    if run:
        _rewrite_symlink(path=link_path, new_target=after)
    return {"path": str(link_path), "changed": True, "before": before, "after": after}


def _iter_candidate_symlinks(vault_root: Path) -> list[Path]:
    out: list[Path] = []

    # Known hot spots (do NOT walk the whole tree).
    for p in (
        vault_root / "audio" / "final",
    ):
        if p.is_symlink():
            out.append(p)

    thumbs = vault_root / "thumbnails"
    if thumbs.exists():
        for child in sorted(thumbs.iterdir()):
            if child.is_symlink():
                out.append(child)

    video_input = vault_root / "video" / "input"
    if video_input.exists():
        for child in sorted(video_input.iterdir()):
            if child.is_symlink():
                out.append(child)

    return out


def _ensure_thumbnails_assets(*, vault_root: Path, shared_root: Path, run: bool) -> dict[str, Any]:
    """
    Make sure `thumbnails/assets` exists. If only a timestamped backup symlink exists, create a stable alias.
    """
    assets = vault_root / "thumbnails" / "assets"
    if assets.exists() or assets.is_symlink():
        return {"action": "ensure_thumbnails_assets", "changed": False, "path": str(assets), "note": "already exists"}

    thumbs = vault_root / "thumbnails"
    if not thumbs.exists():
        return {"action": "ensure_thumbnails_assets", "changed": False, "path": str(assets), "note": "thumbnails/ missing"}

    backup = None
    for child in sorted(thumbs.iterdir()):
        if child.is_symlink() and child.name.startswith("assets.symlink_"):
            backup = child
            break

    if backup is None:
        if run:
            _ensure_dir(assets)
        return {"action": "ensure_thumbnails_assets", "changed": bool(run), "path": str(assets), "note": "created empty dir"}

    target = _readlink(backup)
    rel = _rel_target_within_share(shared_root=shared_root, link_path=assets, target=target) or target
    if run:
        _rewrite_symlink(path=assets, new_target=rel)
    return {
        "action": "ensure_thumbnails_assets",
        "changed": bool(run),
        "path": str(assets),
        "note": f"aliased from {backup.name}",
        "target": rel,
    }


def _ensure_video_runs(*, vault_root: Path, run: bool) -> dict[str, Any]:
    runs = vault_root / "video" / "runs"
    if runs.exists():
        return {"action": "ensure_video_runs", "changed": False, "path": str(runs), "note": "already exists"}
    if run:
        _ensure_dir(runs)
    return {"action": "ensure_video_runs", "changed": bool(run), "path": str(runs), "note": "created dir"}


def _derive_offload_scripts_target(*, vault_root: Path) -> Path | None:
    """
    Best-effort: use `audio/final` link target as an anchor and point to sibling `workspaces/scripts`.
    """
    audio_final = vault_root / "audio" / "final"
    if not audio_final.is_symlink():
        return None
    target = _resolve_link_target_abs(link_path=audio_final, target=_readlink(audio_final))
    parts = list(target.parts)
    if "workspaces" not in parts:
        return None
    idx = parts.index("workspaces")
    workspaces_root = Path(*parts[: idx + 1])
    scripts = workspaces_root / "scripts"
    return scripts if scripts.exists() and scripts.is_dir() else None


def _ensure_scripts_visible(*, vault_root: Path, shared_root: Path, run: bool, stamp: str) -> dict[str, Any]:
    """
    If `ytm_workspaces/scripts/` looks empty, point it to offloaded scripts (backup current dir first).
    This is reversible and avoids waiting for a huge mirror to finish before UI can see scripts.
    """
    scripts = vault_root / "scripts"
    if scripts.is_symlink():
        return {"action": "ensure_scripts_visible", "changed": False, "path": str(scripts), "note": "already symlink"}
    if not scripts.exists() or not scripts.is_dir():
        return {"action": "ensure_scripts_visible", "changed": False, "path": str(scripts), "note": "scripts/ missing or not dir"}

    entries = [p for p in scripts.iterdir() if p.name not in {"_cache"} and not p.name.startswith(".")]
    if entries:
        return {"action": "ensure_scripts_visible", "changed": False, "path": str(scripts), "note": "already has content"}

    target_abs = _derive_offload_scripts_target(vault_root=vault_root)
    if target_abs is None:
        return {"action": "ensure_scripts_visible", "changed": False, "path": str(scripts), "note": "no offload scripts target found"}

    target_rel = os.path.relpath(str(target_abs.resolve()), start=str(scripts.parent.resolve()))
    backup = scripts.with_name(f"scripts.__bak_{stamp}")
    if run:
        scripts.rename(backup)
        _rewrite_symlink(path=scripts, new_target=target_rel)
    return {
        "action": "ensure_scripts_visible",
        "changed": bool(run),
        "path": str(scripts),
        "note": "linked to offload scripts (backup kept)",
        "backup": str(backup),
        "target": target_rel,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Normalize Vault workspaces for multi-host usage (dry-run by default).")
    ap.add_argument("--run", action="store_true", help="Apply changes (default: dry-run).")
    ap.add_argument("--json", action="store_true", help="Emit JSON report to stdout (default: human-readable).")
    args = ap.parse_args()

    shared_root = repo_paths.shared_storage_root()
    vault_root = repo_paths.vault_workspaces_root()
    if shared_root is None:
        raise SystemExit("[POLICY] shared_storage_root is not configured (set YTM_SHARED_STORAGE_ROOT).")
    if vault_root is None:
        raise SystemExit("[POLICY] vault_workspaces_root is not configured (set YTM_VAULT_WORKSPACES_ROOT).")
    if not vault_root.exists():
        raise SystemExit(f"[MISSING] vault_workspaces_root: {vault_root}")

    stamp = _now_compact_utc()
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": _now_iso_utc(),
        "run": bool(args.run),
        "stamp": stamp,
        "paths": {"repo_root": str(REPO_ROOT), "shared_root": str(shared_root), "vault_root": str(vault_root)},
        "changes": {"symlinks": [], "ensure": []},
    }

    symlinks = _iter_candidate_symlinks(vault_root=vault_root)
    for p in symlinks:
        report["changes"]["symlinks"].append(_normalize_one_symlink(shared_root=shared_root, link_path=p, run=bool(args.run)))

    report["changes"]["ensure"].append(_ensure_thumbnails_assets(vault_root=vault_root, shared_root=shared_root, run=bool(args.run)))
    report["changes"]["ensure"].append(_ensure_video_runs(vault_root=vault_root, run=bool(args.run)))
    report["changes"]["ensure"].append(_ensure_scripts_visible(vault_root=vault_root, shared_root=shared_root, run=bool(args.run), stamp=stamp))

    _ensure_dir(_logs_dir())
    rp = _report_path(stamp)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if bool(args.json):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    changed = sum(1 for x in report["changes"]["symlinks"] if x.get("changed")) + sum(
        1 for x in report["changes"]["ensure"] if x.get("changed")
    )
    mode = "RUN" if bool(args.run) else "DRY"
    print(f"[vault_workspaces_doctor] {mode} report={rp} changes={changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

