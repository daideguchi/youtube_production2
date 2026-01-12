from __future__ import annotations

import os
from datetime import datetime
from typing import Dict

from fastapi import APIRouter

from backend.app.ui_settings_store import _get_effective_openai_key, _get_effective_openrouter_key
from factory_common.paths import (
    logs_root as ssot_logs_root,
    planning_root as ssot_planning_root,
    repo_root as ssot_repo_root,
    script_data_root as ssot_script_data_root,
    script_pkg_root,
    video_pkg_root,
)

router = APIRouter(prefix="/api")

REPO_ROOT = ssot_repo_root()
# NOTE: PROJECT_ROOT is treated as repo-root throughout the UI backend (legacy alias).
PROJECT_ROOT = REPO_ROOT
SCRIPT_PIPELINE_ROOT = script_pkg_root()
VIDEO_PIPELINE_ROOT = video_pkg_root()
DATA_ROOT = ssot_script_data_root()
LOGS_ROOT = ssot_logs_root()
UI_LOG_DIR = LOGS_ROOT / "ui"
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"


def _collect_health_components() -> Dict[str, bool]:
    components: Dict[str, bool] = {
        "project_root": PROJECT_ROOT.exists(),
        "data_dir": DATA_ROOT.exists(),
        "script_pipeline": SCRIPT_PIPELINE_ROOT.exists(),
        "video_pipeline": VIDEO_PIPELINE_ROOT.exists(),
        "logs_dir": LOGS_ROOT.exists(),
        "ui_log_dir": UI_LOG_DIR.exists(),
        "channel_planning_dir": CHANNEL_PLANNING_DIR.exists(),
        "gemini_api_key": bool(os.getenv("GEMINI_API_KEY")),
        "openrouter_api_key": bool(_get_effective_openrouter_key()),
        "openai_api_key": bool(_get_effective_openai_key()),
    }
    return components


@router.get("/healthz")
def healthcheck():
    components = _collect_health_components()
    issues = [name for name, ok in components.items() if not ok]
    status = "ok" if not issues else "degraded"
    return {
        "status": status,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "issues": issues,
        "components": components,
    }

