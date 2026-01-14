from __future__ import annotations

from typing import Any

from backend.app.status_models import VALID_STAGE_STATUSES


def _normalize_status_token(value: Any) -> str:
    """
    Normalize various runner/UI status tokens into the small set the UI can reason about.

    The UI (frontend) expects stage statuses to collapse into:
      pending | in_progress | review | blocked | completed
    while older/legacy status.json may contain tokens like:
      processing | running | failed | skipped | done | ok ...
    """

    token = str(value or "").strip().lower()
    if not token or token == "pending":
        return "pending"
    if token in {"completed", "done", "ok", "success", "succeeded", "skipped"}:
        return "completed"
    if token in {"blocked", "failed", "error"}:
        return "blocked"
    if token in {"review"}:
        return "review"
    if token in {"in_progress", "processing", "running", "rerun_in_progress", "rerun_requested"}:
        return "in_progress"
    return "unknown"


def _stage_status_value(stage_entry: Any) -> str:
    if stage_entry is None:
        return "pending"
    raw = stage_entry.get("status") if isinstance(stage_entry, dict) else stage_entry
    normalized = _normalize_status_token(raw)
    if normalized in VALID_STAGE_STATUSES:
        return normalized
    return "unknown"

