from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from backend.app.lock_store import write_text_with_lock
from backend.app.path_utils import safe_relative_path
from backend.app.tts_tagged_text import _parse_tagged_tts

logger = logging.getLogger(__name__)


def replace_text(content: str, original: str, replacement: str, scope: str) -> Tuple[str, int]:
    if scope == "all":
        count = content.count(original)
        if count == 0:
            return content, 0
        return content.replace(original, replacement), count
    index = content.find(original)
    if index == -1:
        return content, 0
    return content.replace(original, replacement, 1), 1


def update_tts_metadata(status: dict, plain_path: Path, tagged_path: Optional[Path], timestamp: str) -> None:
    metadata = status.setdefault("metadata", {})
    audio_meta = metadata.setdefault("audio", {})
    prepare_meta = audio_meta.setdefault("prepare", {})
    prepare_meta["script_sanitized_path"] = safe_relative_path(plain_path) or str(plain_path)
    if tagged_path is not None:
        prepare_meta["script_tagged_path"] = safe_relative_path(tagged_path) or str(tagged_path)
    else:
        prepare_meta.pop("script_tagged_path", None)
    prepare_meta["updated_at"] = timestamp


def _persist_tts_variants(
    base_dir: Path,
    status: dict,
    tagged_content: str,
    *,
    timestamp: str,
    update_assembled: bool = False,
) -> Tuple[str, List[Dict[str, Any]]]:
    plain_content, pause_map, section_count = _parse_tagged_tts(tagged_content)

    audio_prep_dir = base_dir / "audio_prep"
    audio_prep_dir.mkdir(parents=True, exist_ok=True)

    plain_path = audio_prep_dir / "script_sanitized.txt"
    tagged_path = audio_prep_dir / "script_sanitized_with_pauses.txt"

    # 正規パスガード（フォールバック禁止）
    if plain_path.parent.name != "audio_prep":
        raise HTTPException(status_code=400, detail="invalid tts path")
    if tagged_path.parent.name != "audio_prep":
        raise HTTPException(status_code=400, detail="invalid tts_tagged path")

    write_text_with_lock(tagged_path, tagged_content)
    write_text_with_lock(plain_path, plain_content)

    if update_assembled:
        content_dir = base_dir / "content"
        assembled_path = content_dir / "assembled.md"
        assembled_human_path = content_dir / "assembled_human.md"
        if assembled_path.parent.name != "content":
            raise HTTPException(status_code=400, detail="invalid assembled path")
        if assembled_human_path.parent.name != "content":
            raise HTTPException(status_code=400, detail="invalid assembled_human path")
        target = assembled_human_path if assembled_human_path.exists() else assembled_path
        try:
            write_text_with_lock(target, plain_content)
            if target != assembled_path:
                write_text_with_lock(assembled_path, plain_content)
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover - unexpected file errors
            logger.exception("Failed to update assembled.md for %s", base_dir)
            raise HTTPException(status_code=500, detail=f"assembled.md の更新に失敗しました: {exc}") from exc

    metadata = status.setdefault("metadata", {})
    audio_meta = metadata.setdefault("audio", {})
    if pause_map:
        audio_meta["pause_map"] = pause_map
    else:
        audio_meta.pop("pause_map", None)

    synthesis_meta = audio_meta.setdefault("synthesis", {})
    existing_plan = synthesis_meta.get("silence_plan") if isinstance(synthesis_meta.get("silence_plan"), list) else []
    plan: List[float] = list(existing_plan) if isinstance(existing_plan, list) else []
    if section_count and len(plan) < section_count:
        plan.extend([0.0] * (section_count - len(plan)))
    if not plan and section_count:
        plan = [0.0] * section_count
    for entry in pause_map:
        section_idx = entry.get("section")
        pause_value = entry.get("pause_sec")
        if isinstance(section_idx, int) and isinstance(pause_value, (int, float)) and 1 <= section_idx <= len(plan):
            plan[section_idx - 1] = float(pause_value)
    if plan:
        synthesis_meta["silence_plan"] = plan

    update_tts_metadata(status, plain_path, tagged_path, timestamp)

    return plain_content, pause_map

