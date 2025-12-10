from __future__ import annotations

import subprocess
from typing import Any, Dict, List
import json

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _run_jobrunner(args: List[str]) -> str:
    cmd = ["python", "-m", "script_pipeline.job_runner"] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=e.stderr or e.stdout or "job_runner failed")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="job_runner timeout")
    return proc.stdout


@router.get("")
def list_jobs() -> Dict[str, Any]:
    out = _run_jobrunner(["list", "--json"])
    try:
        data = json.loads(out)
    except Exception:
        data = {"raw": out}
    return {"raw": out, "data": data}


@router.post("")
def add_job(channel: str, video: str, title: str | None = None, max_retries: int = 0) -> Dict[str, Any]:
    args = ["add", "--channel", channel, "--video", video, "--max-retries", str(max_retries)]
    if title:
        args += ["--title", title]
    out = _run_jobrunner(args)
    return {"raw": out}


@router.post("/run-next")
def run_next(max_iter: int = 60) -> Dict[str, Any]:
    out = _run_jobrunner(["run-next", "--max-iter", str(max_iter)])
    return {"raw": out}


@router.post("/run-loop")
def run_loop(max_iter: int = 60, limit: int = 20, max_parallel: int = 1, sleep: int = 0) -> Dict[str, Any]:
    out = _run_jobrunner(
        [
            "run-loop",
            "--max-iter",
            str(max_iter),
            "--limit",
            str(limit),
            "--max-parallel",
            str(max_parallel),
            "--sleep",
            str(sleep),
        ]
    )
    return {"raw": out}


@router.post("/cancel")
def cancel(job_id: str) -> Dict[str, Any]:
    out = _run_jobrunner(["cancel", "--id", job_id])
    return {"raw": out}


@router.post("/retry")
def retry(job_id: str) -> Dict[str, Any]:
    out = _run_jobrunner(["retry", "--id", job_id])
    return {"raw": out}


@router.post("/purge")
def purge() -> Dict[str, Any]:
    out = _run_jobrunner(["purge"])
    return {"raw": out}


@router.post("/gc")
def gc(max_minutes: int = 120) -> Dict[str, Any]:
    out = _run_jobrunner(["gc", "--max-minutes", str(max_minutes)])
    return {"raw": out}


@router.post("/force-set")
def force_set(job_id: str, status: str) -> Dict[str, Any]:
    if status not in {"pending", "failed"}:
        raise HTTPException(status_code=400, detail="status must be pending or failed")
    out = _run_jobrunner(["force-set", "--id", job_id, "--status", status])
    return {"raw": out}
