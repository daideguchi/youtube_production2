#!/usr/bin/env python3
"""
storage_doctor.py — print/validate storage path wiring (SoT, shared, CapCut worksets).

Why:
- Multi-machine ops (Mac + Lenovo external + Acer UI gateway) becomes chaotic unless
  every agent can answer: "which path is SoT? which path is shared? where is CapCut hot work?"
- This tool is *read-only by default* and is safe to run anywhere.

SSOT:
- ssot/ops/OPS_ENV_VARS.md
- ssot/ops/OPS_SHARED_WORKSPACES_REMOTE_UI.md
- ssot/ops/OPS_CAPCUT_DRAFT_STORAGE_STRATEGY.md
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

_REPO_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402


VAULT_SENTINEL_NAME = ".ytm_vault_workspaces_root.json"
MOUNTPOINT_STUB_NAME = "README_MOUNTPOINT.txt"


def _p(v: Path | None) -> str | None:
    return str(v) if v is not None else None


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _is_mountpoint_stub(root: Path) -> bool:
    try:
        return (root / MOUNTPOINT_STUB_NAME).exists()
    except Exception:
        return False


def _symlink_target(path: Path) -> str | None:
    if not path.is_symlink():
        return None
    try:
        return str(os.readlink(path))
    except Exception:
        return None


def _collect() -> dict[str, Any]:
    workspace_root = repo_paths.workspace_root()
    planning_root = repo_paths.planning_root()
    shared_storage_root = repo_paths.shared_storage_root()
    shared_storage_base = repo_paths.shared_storage_base()
    vault_workspaces_root = repo_paths.vault_workspaces_root()
    asset_vault_root = repo_paths.asset_vault_root()
    capcut_worksets_root = repo_paths.capcut_worksets_root()

    disk_usage: dict[str, Any] = {"path": str(workspace_root)}
    try:
        u = shutil.disk_usage(str(workspace_root))
        disk_usage.update(
            {
                "total_bytes": int(u.total),
                "used_bytes": int(u.used),
                "free_bytes": int(u.free),
            }
        )
    except Exception:
        pass

    return {
        "host": {"hostname": socket.gethostname()},
        "repo": {"root": str(_REPO_ROOT)},
        "env": {
            "YTM_WORKSPACE_ROOT": os.getenv("YTM_WORKSPACE_ROOT") or None,
            "YTM_PLANNING_ROOT": os.getenv("YTM_PLANNING_ROOT") or None,
            "YTM_SHARED_STORAGE_ROOT": os.getenv("YTM_SHARED_STORAGE_ROOT") or None,
            "YTM_SHARED_STORAGE_NAMESPACE": os.getenv("YTM_SHARED_STORAGE_NAMESPACE") or None,
            "YTM_VAULT_WORKSPACES_ROOT": os.getenv("YTM_VAULT_WORKSPACES_ROOT") or None,
            "YTM_ASSET_VAULT_ROOT": os.getenv("YTM_ASSET_VAULT_ROOT") or None,
            "YTM_CAPCUT_WORKSET_ROOT": os.getenv("YTM_CAPCUT_WORKSET_ROOT") or None,
            "YTM_OFFLOAD_ROOT": os.getenv("YTM_OFFLOAD_ROOT") or os.getenv("FACTORY_OFFLOAD_ROOT") or None,
        },
        "paths": {
            "workspace_root": _p(workspace_root),
            "planning_root": _p(planning_root),
            "shared_storage_root": _p(shared_storage_root),
            "shared_storage_base": _p(shared_storage_base),
            "vault_workspaces_root": _p(vault_workspaces_root),
            "asset_vault_root": _p(asset_vault_root),
            "capcut_worksets_root": _p(capcut_worksets_root),
        },
        "disk": disk_usage,
        "warnings": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Print/validate storage path wiring (safe by default).")
    ap.add_argument("--json", action="store_true", help="Emit JSON (default: human-readable).")
    ap.add_argument("--ensure-dirs", action="store_true", help="Create missing directories for configured shared roots.")
    ap.add_argument(
        "--disk-warn-gib",
        type=float,
        default=60.0,
        help="Warn when free disk GiB is below this threshold (default: 60).",
    )
    ap.add_argument(
        "--disk-stop-gib",
        type=float,
        default=30.0,
        help="Stop-level warning when free disk GiB is below this threshold (default: 30).",
    )
    args = ap.parse_args()

    payload = _collect()
    warnings: list[str] = payload["warnings"]
    paths: dict[str, str | None] = payload["paths"]
    env: dict[str, str | None] = payload["env"]
    disk: dict[str, Any] = payload.get("disk") or {}

    workspace_root = Path(paths["workspace_root"] or ".")
    planning_root = Path(paths["planning_root"] or ".")
    configured_planning = str(env.get("YTM_PLANNING_ROOT") or "").strip()
    if configured_planning:
        try:
            configured_planning_path = Path(configured_planning).expanduser()
            if configured_planning_path != planning_root:
                warnings.append(
                    "planning_root override is set but not effective (using local fallback). "
                    f"configured: {configured_planning_path} effective: {planning_root}"
                )
        except Exception:
            pass
    shared_storage_root_s = paths["shared_storage_root"]
    shared_storage_base_s = paths["shared_storage_base"]
    vault_workspaces_root_s = paths.get("vault_workspaces_root")
    asset_vault_root_s = paths["asset_vault_root"]
    capcut_worksets_root = Path(paths["capcut_worksets_root"] or ".")
    shared_is_stub = False

    if not workspace_root.exists():
        warnings.append(f"workspace_root does not exist: {workspace_root}")
    if not planning_root.exists():
        warnings.append(f"planning_root does not exist: {planning_root}")
    if shared_storage_root_s is None:
        warnings.append("YTM_SHARED_STORAGE_ROOT is not set (shared storage helpers will refuse to run).")
    else:
        shared_root = Path(shared_storage_root_s)
        shared_is_stub = _is_mountpoint_stub(shared_root)
        if shared_is_stub:
            marker = shared_root / MOUNTPOINT_STUB_NAME
            warnings.append(
                f"shared_storage_root looks OFFLINE/STUB (marker present): {marker} "
                "(Lenovo/NAS may be down; shared writes may be slow/unreliable)."
            )
        if not shared_root.exists():
            warnings.append(f"shared_storage_root does not exist: {shared_root}")
        elif not shared_root.is_dir():
            warnings.append(f"shared_storage_root is not a directory: {shared_root}")

    if shared_storage_base_s is None and shared_storage_root_s is not None:
        warnings.append("shared_storage_base could not be resolved (check YTM_SHARED_STORAGE_ROOT).")
    if vault_workspaces_root_s is None:
        warnings.append("vault_workspaces_root is not configured (set YTM_VAULT_WORKSPACES_ROOT for Mac->vault mirroring).")
    else:
        vwr = Path(str(vault_workspaces_root_s))
        if shared_is_stub and vwr.is_symlink():
            target = _symlink_target(vwr)
            if target:
                warnings.append(f"vault_workspaces_root is symlinked while share is offline: {vwr} -> {target}")
        if not vwr.exists():
            warnings.append(f"vault_workspaces_root does not exist: {vwr}")
        elif not vwr.is_dir():
            warnings.append(f"vault_workspaces_root is not a directory: {vwr}")
        else:
            sentinel = vwr / VAULT_SENTINEL_NAME
            if not sentinel.exists():
                warnings.append(
                    f"vault sentinel missing (run once: ./ops mirror workspaces -- --bootstrap-dest): {sentinel}"
                )
    if asset_vault_root_s is None and shared_storage_root_s is not None:
        warnings.append("asset_vault_root is not configured (set YTM_ASSET_VAULT_ROOT or use shared root default).")

    # Strong hint: planning split is intentional but easy to forget when debugging.
    try:
        planning_root.relative_to(workspace_root)
    except Exception:
        warnings.append(
            "planning_root is outside workspace_root (this is OK if Planning SSOT is shared; "
            "make sure every host uses the same Planning root)."
        )

    if not capcut_worksets_root.exists():
        warnings.append(f"capcut_worksets_root does not exist yet: {capcut_worksets_root} (will be created on first use).")

    try:
        free = int(disk.get("free_bytes") or 0)
        total = int(disk.get("total_bytes") or 0)
        free_gib = free / (1024**3)
        used_pct = (1.0 - (free / total)) * 100 if total > 0 else None
        disk["free_gib"] = round(free_gib, 2)
        if used_pct is not None:
            disk["used_pct"] = round(used_pct, 1)

        if free_gib <= float(args.disk_stop_gib):
            warnings.append(
                f"disk free is CRITICAL: {free_gib:.2f}GiB (<= {float(args.disk_stop_gib):.2f}GiB). "
                "Avoid large renders; run cleanup dry-run before generating new assets."
            )
        elif free_gib <= float(args.disk_warn_gib):
            warnings.append(
                f"disk free is low: {free_gib:.2f}GiB (<= {float(args.disk_warn_gib):.2f}GiB). "
                "Plan cleanup to keep Mac work responsive."
            )
    except Exception:
        pass

    if bool(args.ensure_dirs):
        if shared_is_stub:
            warnings.append("Refusing --ensure-dirs because shared_storage_root looks OFFLINE/STUB.")
            shared_storage_root_s = None
        # Only create dirs that are explicitly configured.
        if shared_storage_root_s is not None:
            if asset_vault_root_s is not None:
                _ensure_dir(Path(asset_vault_root_s))
            if shared_storage_base_s is not None:
                _ensure_dir(Path(shared_storage_base_s) / "manifests")

    if bool(args.json):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print("[storage_doctor]")
        for k, v in payload["paths"].items():
            print(f"- {k}: {v}")
        if disk:
            print("[disk]")
            for k in ("path", "total_bytes", "used_bytes", "free_bytes", "free_gib", "used_pct"):
                if k in disk:
                    print(f"- {k}: {disk.get(k)}")
        if warnings:
            print("[warnings]")
            for w in warnings:
                print(f"- {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
