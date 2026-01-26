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
import socket
from pathlib import Path
from typing import Any

from _bootstrap import bootstrap

_REPO_ROOT = bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402


VAULT_SENTINEL_NAME = ".ytm_vault_workspaces_root.json"


def _p(v: Path | None) -> str | None:
    return str(v) if v is not None else None


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _collect() -> dict[str, Any]:
    workspace_root = repo_paths.workspace_root()
    planning_root = repo_paths.planning_root()
    shared_storage_root = repo_paths.shared_storage_root()
    shared_storage_base = repo_paths.shared_storage_base()
    vault_workspaces_root = repo_paths.vault_workspaces_root()
    asset_vault_root = repo_paths.asset_vault_root()
    capcut_worksets_root = repo_paths.capcut_worksets_root()

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
        "warnings": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Print/validate storage path wiring (safe by default).")
    ap.add_argument("--json", action="store_true", help="Emit JSON (default: human-readable).")
    ap.add_argument("--ensure-dirs", action="store_true", help="Create missing directories for configured shared roots.")
    args = ap.parse_args()

    payload = _collect()
    warnings: list[str] = payload["warnings"]
    paths: dict[str, str | None] = payload["paths"]
    env: dict[str, str | None] = payload["env"]

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

    if not workspace_root.exists():
        warnings.append(f"workspace_root does not exist: {workspace_root}")
    if not planning_root.exists():
        warnings.append(f"planning_root does not exist: {planning_root}")
    if shared_storage_root_s is None:
        warnings.append("YTM_SHARED_STORAGE_ROOT is not set (shared storage helpers will refuse to run).")
    else:
        shared_root = Path(shared_storage_root_s)
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

    if bool(args.ensure_dirs):
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
        if warnings:
            print("[warnings]")
            for w in warnings:
                print(f"- {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
