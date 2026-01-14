from __future__ import annotations

import json
import logging

from fastapi import HTTPException

from backend.app.datetime_utils import current_timestamp_compact
from backend.app.episode_store import load_status
from backend.app.normalize import normalize_channel_code, normalize_video_number
from backend.app.path_utils import safe_relative_path
from factory_common.paths import (
    logs_root as ssot_logs_root,
    planning_root as ssot_planning_root,
    script_data_root as ssot_script_data_root,
)

logger = logging.getLogger(__name__)

DATA_ROOT = ssot_script_data_root()
CHANNEL_PLANNING_DIR = ssot_planning_root() / "channels"
SSOT_SYNC_LOG_DIR = ssot_logs_root() / "regression" / "ssot_sync"


def run_ssot_sync_for_channel(channel_code: str, video_number: str) -> None:
    """
    Guard SoT after UI mutations.

    NOTE:
    - 外部スクリプト依存はしない（SoT は直接読み取る）。深い整合検査は `scripts/ops/planning_lint.py` を使用する。
    - ここでは「正本ファイルが存在し、最低限読める」ことだけを同期ガードとして検証する。
    """

    channel_code = normalize_channel_code(channel_code)
    video_number = normalize_video_number(video_number)

    csv_path = CHANNEL_PLANNING_DIR / f"{channel_code}.csv"
    status_json_path = DATA_ROOT / channel_code / video_number / "status.json"

    issues: list[str] = []
    if not csv_path.exists():
        issues.append("missing_planning_csv")
    if not status_json_path.exists():
        issues.append("missing_status_json")

    row_exists = False
    if csv_path.exists():
        try:
            import csv as _csv

            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                reader = _csv.DictReader(handle)
                for row in reader:
                    raw = row.get("動画番号") or row.get("video") or row.get("Video") or ""
                    if not raw:
                        continue
                    try:
                        token = normalize_video_number(raw)
                    except Exception:
                        continue
                    if token == video_number:
                        row_exists = True
                        break
        except Exception:
            issues.append("planning_csv_unreadable")

    if csv_path.exists() and not row_exists:
        issues.append("missing_planning_row")

    if status_json_path.exists():
        try:
            st = load_status(channel_code, video_number)
            if not isinstance(st, dict):
                issues.append("status_json_invalid_type")
        except Exception:
            issues.append("status_json_unreadable")

    if not issues:
        logger.info("SSOT guard ok for %s-%s", channel_code, video_number)
        return

    SSOT_SYNC_LOG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = current_timestamp_compact()
    log_path = SSOT_SYNC_LOG_DIR / f"ssot_guard_failure_{channel_code}_{video_number}_{timestamp}.json"
    log_payload = {
        "channel_code": channel_code,
        "video_number": video_number,
        "issues": issues,
        "planning_csv": str(safe_relative_path(csv_path) or csv_path),
        "status_json": str(safe_relative_path(status_json_path) or status_json_path),
    }
    log_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.error("SSOT guard failed for %s-%s: %s (log: %s)", channel_code, video_number, issues, log_path)
    raise HTTPException(
        status_code=502,
        detail="SSOTガードに失敗しました。ログを確認してから再試行してください。",
    )

