from __future__ import annotations

import logging
import os
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException

from factory_common.paths import repo_root as ssot_repo_root
from factory_common.paths import video_input_root as ssot_video_input_root

logger = logging.getLogger("ui_backend")

REPO_ROOT = ssot_repo_root()

router = APIRouter(prefix="/api/remotion", tags=["remotion"])


def _safe_relative_path(path: Path) -> Optional[str]:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path) if path.exists() else None


@router.post("/restart_preview")
def restart_remotion_preview(port: int = 3100):
    remotion_dir = REPO_ROOT / "apps" / "remotion"
    if not remotion_dir.exists():
        raise HTTPException(status_code=404, detail=f"remotion dir not found: {remotion_dir}")
    if not (remotion_dir / "node_modules").is_dir():
        raise HTTPException(
            status_code=400,
            detail="Remotion deps are not installed. Run: (cd apps/remotion && npm ci)",
        )

    # Kill only listeners on the target port (avoid broad pkill).
    try:
        lsof = subprocess.run(
            ["lsof", "-ti", f"tcp:{port}"],
            capture_output=True,
            text=True,
            check=False,
        )
        pids = [pid.strip() for pid in (lsof.stdout or "").splitlines() if pid.strip().isdigit()]
        if pids:
            subprocess.run(["kill", "-TERM", *pids], check=False)
            time.sleep(0.6)
            subprocess.run(["kill", "-KILL", *pids], check=False)
    except FileNotFoundError:
        # Fallback: keep legacy behavior if lsof is unavailable.
        try:
            subprocess.run(["pkill", "-f", "remotion preview"], check=False)
        except Exception:
            pass
    except Exception as e:
        logger.warning("Failed to stop remotion preview on port %s: %s", port, e)

    # Ensure workspace exists (preview reads runs from here).
    try:
        ssot_video_input_root().mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    log_dir = REPO_ROOT / "workspaces" / "logs" / "ui_hub"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "remotion_studio.log"
    pid_path = log_dir / "remotion_studio.pid"

    try:
        with log_path.open("a", encoding="utf-8") as fp:
            proc = subprocess.Popen(
                [
                    "npx",
                    "remotion",
                    "preview",
                    "--entry",
                    "src/index.ts",
                    "--root",
                    ".",
                    "--public-dir",
                    "public",
                    "--port",
                    str(port),
                ],
                cwd=str(remotion_dir),
                env={**os.environ, "BROWSER": "none"},
                stdin=subprocess.DEVNULL,
                stdout=fp,
                stderr=fp,
                start_new_session=True,
            )
        pid_path.write_text(f"{proc.pid}\n", encoding="utf-8")
    except Exception as e:
        logger.error("Failed to start remotion preview: %s", e)
        raise HTTPException(status_code=500, detail=f"start failed: {e}")

    url = f"http://localhost:{port}"
    deadline = time.time() + 10.0
    last_error: Optional[str] = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=0.6):
                return {
                    "status": "ok",
                    "port": port,
                    "url": url,
                    "pid": proc.pid,
                    "log_path": _safe_relative_path(log_path) or str(log_path),
                }
        except Exception as e:
            last_error = str(e)
            time.sleep(0.5)

    raise HTTPException(
        status_code=500,
        detail=f"Remotion preview did not start on {url}. Check {log_path} (last_error={last_error})",
    )

