from __future__ import annotations

import json
import logging
import sqlite3
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException

from backend.app.datetime_utils import current_timestamp
from backend.core.portalocker_compat import portalocker
from factory_common.paths import logs_root as ssot_logs_root
from factory_common.paths import repo_root as ssot_repo_root

logger = logging.getLogger(__name__)

REPO_ROOT = ssot_repo_root()
# NOTE: PROJECT_ROOT is treated as repo-root throughout the UI backend (legacy alias).
PROJECT_ROOT = REPO_ROOT
LOGS_ROOT = ssot_logs_root()

LOCK_TIMEOUT_SECONDS = 5.0
LOCK_METRICS = {"timeout": 0, "unexpected": 0}
LOCK_HISTORY: deque[dict] = deque(maxlen=50)
LOCK_DB_PATH = LOGS_ROOT / "lock_metrics.db"
LOCK_ALERT_CONFIG_PATH = PROJECT_ROOT / "configs" / "ui_lock_alerts.json"
LOCK_ALERT_CONFIG: Dict[str, Any] = {
    "enabled": False,
    "timeout_threshold": None,
    "unexpected_threshold": None,
    "cooldown_minutes": 30,
    "slack_webhook": None,
}
LOCK_ALERT_STATE: Dict[str, Any] = {
    "timeout": 0,
    "unexpected": 0,
    "last_alert_at": None,
}


def init_lock_storage() -> None:
    LOCK_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(LOCK_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lock_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timeout_total INTEGER NOT NULL,
                unexpected_total INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_lock_metrics_time
            ON lock_metrics(occurred_at)
            """
        )
    load_lock_history()
    load_lock_alert_config()


def load_lock_history() -> None:
    LOCK_HISTORY.clear()
    if not LOCK_DB_PATH.exists():
        return
    with sqlite3.connect(LOCK_DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT occurred_at, event_type, timeout_total, unexpected_total
            FROM lock_metrics
            ORDER BY occurred_at DESC
            LIMIT 50
            """
        ).fetchall()
    if rows:
        last = rows[0]
        LOCK_METRICS["timeout"] = last[2]
        LOCK_METRICS["unexpected"] = last[3]
    else:
        LOCK_METRICS["timeout"] = 0
        LOCK_METRICS["unexpected"] = 0
    for occurred_at, event_type, timeout_total, unexpected_total in reversed(rows):
        LOCK_HISTORY.append(
            {
                "timestamp": occurred_at,
                "type": event_type,
                "timeout": timeout_total,
                "unexpected": unexpected_total,
            }
        )


def load_lock_alert_config() -> None:
    LOCK_ALERT_CONFIG.update(
        {
            "enabled": False,
            "timeout_threshold": None,
            "unexpected_threshold": None,
            "cooldown_minutes": 30,
            "slack_webhook": None,
        }
    )
    if not LOCK_ALERT_CONFIG_PATH.exists():
        return
    try:
        data = json.loads(LOCK_ALERT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.warning("Invalid lock alert configuration: %s", LOCK_ALERT_CONFIG_PATH)
        return
    LOCK_ALERT_CONFIG["enabled"] = bool(data.get("enabled", True))
    LOCK_ALERT_CONFIG["timeout_threshold"] = data.get("timeout_threshold")
    LOCK_ALERT_CONFIG["unexpected_threshold"] = data.get("unexpected_threshold")
    LOCK_ALERT_CONFIG["cooldown_minutes"] = data.get("cooldown_minutes", 30)
    LOCK_ALERT_CONFIG["slack_webhook"] = data.get("slack_webhook")
    reset_lock_alert_state()


def reset_lock_alert_state() -> None:
    LOCK_ALERT_STATE["timeout"] = 0
    LOCK_ALERT_STATE["unexpected"] = 0
    LOCK_ALERT_STATE["last_alert_at"] = None


def emit_lock_alert(message: str) -> None:
    logger.warning("LOCK ALERT: %s", message)
    webhook = LOCK_ALERT_CONFIG.get("slack_webhook")
    if not webhook:
        return
    try:
        payload = json.dumps({"text": message}).encode("utf-8")
        request = urllib.request.Request(
            webhook,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(request, timeout=5)
    except Exception:  # pragma: no cover - network errors not deterministic
        logger.exception("Failed to send Slack notification")


def maybe_emit_lock_alert(event_type: str) -> None:
    if not LOCK_ALERT_CONFIG.get("enabled", False):
        return
    LOCK_ALERT_STATE[event_type] += 1
    threshold_hit = False
    timeout_threshold = LOCK_ALERT_CONFIG.get("timeout_threshold")
    unexpected_threshold = LOCK_ALERT_CONFIG.get("unexpected_threshold")
    if timeout_threshold:
        if LOCK_ALERT_STATE["timeout"] >= timeout_threshold:
            threshold_hit = True
    if unexpected_threshold:
        if LOCK_ALERT_STATE["unexpected"] >= unexpected_threshold:
            threshold_hit = True
    if not threshold_hit:
        return
    last_alert = LOCK_ALERT_STATE.get("last_alert_at")
    cooldown_minutes = LOCK_ALERT_CONFIG.get("cooldown_minutes") or 0
    if last_alert:
        elapsed = datetime.now(timezone.utc) - last_alert
        if elapsed.total_seconds() < cooldown_minutes * 60:
            return
    message = (
        "ロック競合アラート: "
        f"timeout={LOCK_ALERT_STATE['timeout']}件, unexpected={LOCK_ALERT_STATE['unexpected']}件"
        " (閾値到達)"
    )
    emit_lock_alert(message)
    LOCK_ALERT_STATE["last_alert_at"] = datetime.now(timezone.utc)
    reset_lock_alert_state()


def record_lock_event(event_type: str) -> None:
    entry = {
        "timestamp": current_timestamp(),
        "type": event_type,
        "timeout": LOCK_METRICS["timeout"],
        "unexpected": LOCK_METRICS["unexpected"],
    }
    LOCK_HISTORY.append(entry)
    try:
        with sqlite3.connect(LOCK_DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO lock_metrics (occurred_at, event_type, timeout_total, unexpected_total)
                VALUES (?, ?, ?, ?)
                """,
                (entry["timestamp"], entry["type"], entry["timeout"], entry["unexpected"]),
            )
    except sqlite3.Error:
        logger.exception("Failed to persist lock metric event")
    maybe_emit_lock_alert(event_type)


def write_text_with_lock(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with portalocker.Lock(
            str(path),
            mode="w",
            encoding="utf-8",
            timeout=LOCK_TIMEOUT_SECONDS,
        ) as handle:
            handle.write(content)
            handle.flush()
    except portalocker.exceptions.Timeout as exc:
        LOCK_METRICS["timeout"] += 1
        record_lock_event("timeout")
        logger.warning("Lock timeout while writing %s", path)
        raise HTTPException(
            status_code=423,
            detail="ファイルが使用中です。数秒後に再試行してください。",
        ) from exc
    except portalocker.exceptions.LockException as exc:
        LOCK_METRICS["unexpected"] += 1
        record_lock_event("unexpected")
        logger.exception("Unexpected lock error for %s", path)
        raise HTTPException(
            status_code=500,
            detail="ファイルの更新中に予期しないロックエラーが発生しました。",
        ) from exc
