from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


def summarize_log(log_path: Path) -> Optional[dict]:
    """
    Summarize an audio log JSON for lightweight UI display.

    Returns a small dict with stable keys, or None if unavailable.
    """
    if not log_path or not log_path.exists():
        return None
    try:
        data = json.loads(log_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    meta = data.get("audio") or {}
    engine = data.get("engine") or meta.get("engine")
    duration = meta.get("duration_sec")
    chunk_meta = data.get("engine_metadata", {}).get("chunk_meta")
    chunk_count = len(chunk_meta) if isinstance(chunk_meta, list) else None
    return {
        "engine": engine,
        "duration_sec": duration,
        "chunk_count": chunk_count,
    }

