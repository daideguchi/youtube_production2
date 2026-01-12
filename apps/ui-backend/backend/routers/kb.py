from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from audio_tts.tts.reading_dict import is_banned_surface, is_safe_reading, normalize_reading_kana
from factory_common.paths import audio_pkg_root

router = APIRouter(prefix="/api/kb", tags=["kb"])

logger = logging.getLogger(__name__)

KB_PATH = audio_pkg_root() / "data" / "global_knowledge_base.json"


@router.get("")
def get_knowledge_base():
    """Retrieve Global Knowledge Base."""
    kb_path = KB_PATH
    if not kb_path.exists():
        logger.warning("KB not found at: %s", kb_path)
        return {"version": 2, "words": {}}

    try:
        with kb_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        # Migrate/Compatibility
        if "entries" in data and "words" not in data:
            return {"version": 1, "words": {}}  # Reset if old version
        return data
    except Exception as exc:
        logger.error("Failed to load KB at %s: %s", kb_path, exc)
        raise HTTPException(status_code=500, detail=f"Failed to load KB: {exc}") from exc


class KnowledgeBaseUpsertRequest(BaseModel):
    word: str = Field(..., description="登録する単語（漢字/表記）")
    reading: str = Field(..., description="読み（カナ推奨）")


@router.post("")
def upsert_knowledge_base_entry(payload: KnowledgeBaseUpsertRequest):
    """Add or update an entry in Global Knowledge Base (word dict)."""
    word = payload.word.strip()
    reading = payload.reading.strip()
    if is_banned_surface(word):
        raise HTTPException(status_code=400, detail="短すぎる/曖昧な単語は辞書登録できません。")
    if not reading:
        raise HTTPException(status_code=400, detail="読みを入力してください。")
    normalized = normalize_reading_kana(reading)
    if not is_safe_reading(normalized):
        raise HTTPException(status_code=400, detail="読みはカナで入力してください（漢字や説明文は不可）。")
    if normalized == word:
        raise HTTPException(status_code=400, detail="読みが表記と同じなので登録不要です。")
    reading = normalized

    kb_path = KB_PATH
    kb_path.parent.mkdir(parents=True, exist_ok=True)
    data: Dict[str, Any] = {"version": 2, "words": {}, "updated_at": datetime.now(timezone.utc).isoformat()}
    if kb_path.exists():
        try:
            data = json.loads(kb_path.read_text(encoding="utf-8"))
        except Exception:
            # fall back to empty structure
            data = {"version": 2, "words": {}, "updated_at": datetime.now(timezone.utc).isoformat()}

    container = data.get("words")
    if container is None:
        container = data.get("entries")
        if container is None:
            container = {}
        data["words"] = container
    if not isinstance(container, dict):
        container = {}
        data["words"] = container

    container[word] = reading
    data["version"] = data.get("version") or 2
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    kb_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


@router.delete("/{entry_key}")
def delete_knowledge_base_entry(entry_key: str):
    """Delete an entry from GKB."""
    kb_path = KB_PATH
    if not kb_path.exists():
        raise HTTPException(status_code=404, detail="KB not found")

    try:
        with kb_path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)

        # Support both structures or migrate
        container = data.get("words")
        if container is None:
            container = data.get("entries")  # Old format fallback

        if container and entry_key in container:
            del container[entry_key]

            # Atomic write
            temp_path = kb_path.with_suffix(".tmp")
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2)
            shutil.move(temp_path, kb_path)

            return {"success": True, "message": f"Deleted key {entry_key}"}
        raise HTTPException(status_code=404, detail="Entry key not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to update KB: {exc}") from exc

