from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def current_timestamp() -> str:
    """Return an ISO8601 UTC timestamp with ``Z`` suffix."""

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def current_timestamp_compact() -> str:
    """Return a compact UTC timestamp used by existing metadata fields."""

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def parse_iso_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None
