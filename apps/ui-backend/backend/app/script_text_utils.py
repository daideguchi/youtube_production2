from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from backend.app.episode_store import video_base_dir
from backend.app.path_utils import PROJECT_ROOT


def _resolve_a_text_display_path(channel: str, video: str) -> Path:
    """
    Aテキスト（表示用）用に解決するパス。
    優先: content/assembled_human.md -> content/assembled.md
    """
    base = video_base_dir(channel, video)
    candidates = [
        base / "content" / "assembled_human.md",
        base / "content" / "assembled.md",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    raise HTTPException(status_code=404, detail=f"A-text not found: {channel}-{video}")


def _fallback_character_count_from_files(
    metadata: Dict[str, Any], channel_code: str, video_number: str
) -> Optional[int]:
    """
    Fallback: count characters from assembled files when metadata is missing or zero.
    """
    candidates: List[Path] = []
    assembled_path = metadata.get("assembled_path")
    script_meta = metadata.get("script")
    if not assembled_path and isinstance(script_meta, dict):
        assembled_path = script_meta.get("assembled_path")
    if assembled_path:
        path = Path(assembled_path)
        if not path.is_absolute():
            path = (PROJECT_ROOT / assembled_path).resolve()
        candidates.append(path)
    base_dir = video_base_dir(channel_code, video_number)
    candidates.append(base_dir / "content" / "assembled.md")
    candidates.append(base_dir / "content" / "assembled_human.md")

    for path in candidates:
        try:
            if path.exists() and path.is_file():
                text = path.read_text(encoding="utf-8")
                if text:
                    return len(text)
        except Exception:
            continue
    return None


def _character_count_from_a_text(channel_code: str, video_number: str) -> Optional[int]:
    """
    Prefer accurate count by reading the current Aテキスト (assembled_human/assembled → audio_prep/script_sanitized).
    """
    try:
        path = _resolve_a_text_display_path(channel_code, video_number)
    except HTTPException:
        return None
    try:
        text = path.read_text(encoding="utf-8")
        # Match UI display semantics: count without line breaks.
        return len(text.replace("\r", "").replace("\n", ""))
    except Exception:
        return None

