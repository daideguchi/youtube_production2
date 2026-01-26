from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query

from factory_common import paths as repo_paths

router = APIRouter(prefix="/api/storage", tags=["storage"])

REPO_ROOT = repo_paths.repo_root()
HOT_ASSETS_REPORT_PATH = (
    REPO_ROOT / "workspaces" / "logs" / "ops" / "hot_assets_doctor" / "report__latest.json"
)

_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}

_YTM_SAFE_KEYS = {
    "YTM_WORKSPACE_ROOT",
    "YTM_PLANNING_ROOT",
    "YTM_SHARED_STORAGE_ROOT",
    "YTM_SHARED_STORAGE_NAMESPACE",
    "YTM_VAULT_WORKSPACES_ROOT",
    "YTM_ASSET_VAULT_ROOT",
    "YTM_CAPCUT_WORKSET_ROOT",
    "YTM_OFFLOAD_ROOT",
}


def _utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _strip_quotes(value: str) -> str:
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _read_dotenv_ytm_vars() -> Dict[str, str]:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return {}

    out: Dict[str, str] = {}
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#"):
                continue
            if raw.startswith("export "):
                raw = raw[len("export ") :].strip()
            if "=" not in raw:
                continue
            key, val = raw.split("=", 1)
            key = key.strip()
            if key not in _YTM_SAFE_KEYS:
                continue
            val = _strip_quotes(val)
            if val:
                out[key] = val
    except Exception:
        return {}

    return out


def _run_storage_doctor_json(*, env: Dict[str, str]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    script = REPO_ROOT / "scripts" / "ops" / "storage_doctor.py"
    if not script.exists():
        return None, f"not_found: {script}"

    proc_env = dict(env)
    repo_pythonpath = f"{REPO_ROOT}:{REPO_ROOT}/packages"
    if proc_env.get("PYTHONPATH"):
        proc_env["PYTHONPATH"] = f"{repo_pythonpath}:{proc_env['PYTHONPATH']}"
    else:
        proc_env["PYTHONPATH"] = repo_pythonpath

    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--json"],
            cwd=str(REPO_ROOT),
            env=proc_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=6,
        )
    except subprocess.TimeoutExpired:
        return None, "storage_doctor timeout"
    except Exception as exc:
        return None, f"storage_doctor failed: {exc}"

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return None, err or f"storage_doctor rc={proc.returncode}"

    try:
        return json.loads(proc.stdout), None
    except Exception as exc:
        return None, f"storage_doctor invalid json: {exc}"


def _timed_test_file(path: Path, *, timeout_sec: float = 1.0) -> Optional[bool]:
    try:
        proc = subprocess.run(
            ["/bin/bash", "-lc", f"test -f {shlex.quote(str(path))}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None
    return proc.returncode == 0


def _hot_assets_summary() -> tuple[Optional[dict[str, Any]], Optional[str]]:
    if not HOT_ASSETS_REPORT_PATH.exists():
        return None, f"not_found: {HOT_ASSETS_REPORT_PATH}"
    try:
        payload = json.loads(HOT_ASSETS_REPORT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"read_failed: {exc}"

    channels = payload.get("channels")
    if not isinstance(channels, list):
        return None, "invalid_schema: channels is not a list"

    violations_total = 0
    warnings_total = 0
    hot_checked_total = 0
    channels_with_violations: list[str] = []

    for ch in channels:
        if not isinstance(ch, dict):
            continue
        code = str(ch.get("channel") or "").strip()
        counts = ch.get("counts")
        if not isinstance(counts, dict):
            continue
        violations = int(counts.get("violations") or 0)
        warnings = int(counts.get("warnings") or 0)
        checked_hot = int(counts.get("checked_hot") or 0)
        violations_total += violations
        warnings_total += warnings
        hot_checked_total += checked_hot
        if violations > 0 and code:
            channels_with_violations.append(code)

    return (
        {
            "report_path": str(HOT_ASSETS_REPORT_PATH),
            "generated_at": payload.get("generated_at"),
            "schema": payload.get("schema"),
            "channels_total": len(channels),
            "hot_checked_total": hot_checked_total,
            "violations_total": violations_total,
            "warnings_total": warnings_total,
            "channels_with_violations": sorted(set(channels_with_violations)),
        },
        None,
    )


@router.get("/status")
def storage_status(
    fresh: bool = Query(False, description="Bypass cache (recompute)."),
    cache_sec: float = Query(5.0, ge=0.0, le=60.0, description="Cache TTL seconds."),
):
    now = time.time()
    cached_at = float(_CACHE.get("at") or 0.0)
    cached_value = _CACHE.get("value")
    if cached_value and not fresh and now - cached_at <= cache_sec:
        out = dict(cached_value)
        out["cached"] = True
        return out

    dotenv_ytm = _read_dotenv_ytm_vars()
    runtime_ytm = {k: (os.getenv(k) or None) for k in sorted(_YTM_SAFE_KEYS)}

    env_for_subproc = dict(os.environ)
    env_for_subproc.update(dotenv_ytm)

    doctor, doctor_err = _run_storage_doctor_json(env=env_for_subproc)
    hot, hot_err = _hot_assets_summary()

    shared_stub: Optional[bool] = None
    vault_sentinel: Optional[bool] = None
    if doctor and isinstance(doctor.get("paths"), dict):
        shared_root_raw = doctor["paths"].get("shared_storage_root")
        if isinstance(shared_root_raw, str) and shared_root_raw.strip():
            shared_stub = _timed_test_file(Path(shared_root_raw) / "README_MOUNTPOINT.txt")
        vault_root_raw = doctor["paths"].get("vault_workspaces_root")
        if isinstance(vault_root_raw, str) and vault_root_raw.strip():
            vault_sentinel = _timed_test_file(Path(vault_root_raw) / ".ytm_vault_workspaces_root.json")

    value = {
        "generated_at": _utc_now_iso(),
        "cached": False,
        "runtime_ytm": runtime_ytm,
        "dotenv_ytm": dotenv_ytm,
        "storage_doctor": doctor,
        "storage_doctor_error": doctor_err,
        "shared_storage_stub": shared_stub,
        "vault_sentinel_present": vault_sentinel,
        "hot_assets": hot,
        "hot_assets_error": hot_err,
    }
    _CACHE["at"] = now
    _CACHE["value"] = value
    return value

