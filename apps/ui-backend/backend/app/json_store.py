from __future__ import annotations

import json
from pathlib import Path

from fastapi import HTTPException

from backend.app.lock_store import write_text_with_lock


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"Invalid JSON: {path}") from exc


def write_json(path: Path, payload: dict) -> None:
    write_text_with_lock(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

