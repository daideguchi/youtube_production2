from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter

from factory_common.paths import repo_root

router = APIRouter(prefix="/api/meta", tags=["meta"])

REPO_ROOT = repo_root()

_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}


def _run_git(*args: str) -> Tuple[Optional[str], Optional[str]]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as exc:
        return None, str(exc)

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        return None, err or f"git_failed({proc.returncode})"

    return (proc.stdout or "").strip() or None, None


def _collect_meta() -> Dict[str, Any]:
    sha, sha_err = _run_git("rev-parse", "--short", "HEAD")
    branch, branch_err = _run_git("rev-parse", "--abbrev-ref", "HEAD")
    dirty_out, dirty_err = _run_git("status", "--porcelain=v1")

    return {
        "repo_root": str(REPO_ROOT),
        "git": {
            "sha": sha,
            "branch": branch,
            "dirty": bool(dirty_out),
            "errors": {k: v for k, v in {"sha": sha_err, "branch": branch_err, "dirty": dirty_err}.items() if v},
        },
        "process": {
            "pid": os.getpid(),
        },
        "time": {
            "server_now": time.time(),
        },
    }


@router.get("")
def get_meta():
    # Cache briefly to avoid running git on every UI navigation.
    now = time.time()
    cached_at = float(_CACHE.get("at") or 0.0)
    cached_value = _CACHE.get("value")
    if cached_value and now - cached_at < 3.0:
        return cached_value

    value = _collect_meta()
    _CACHE["at"] = now
    _CACHE["value"] = value
    return value

