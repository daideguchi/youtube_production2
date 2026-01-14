from __future__ import annotations

import contextlib
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.audio import pause_tags
from backend.audio.script_loader import iterate_sections


def _load_sections_from_text(text: str) -> List[Any]:
    """Helper that reuses iterate_sections by writing to a temp file."""

    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not normalized:
        return []
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile("w+", encoding="utf-8", delete=False) as tmp_file:
            tmp_file.write(normalized)
            tmp_file.flush()
            tmp_path = Path(tmp_file.name)
        sections = list(iterate_sections(tmp_path))
    finally:
        if tmp_path is not None:
            with contextlib.suppress(FileNotFoundError):
                tmp_path.unlink()
    return sections


def _compose_tagged_tts(
    plain_text: str,
    silence_plan: Optional[Sequence[float]],
    pause_map: Optional[Sequence[Dict[str, Any]]],
) -> str:
    sections = _load_sections_from_text(plain_text)
    if not sections:
        return plain_text

    total = len(sections)
    plan: List[float] = [0.0] * total
    if silence_plan:
        for idx in range(min(total, len(silence_plan))):
            try:
                plan[idx] = float(silence_plan[idx])
            except (TypeError, ValueError):
                plan[idx] = 0.0
    if pause_map:
        for entry in pause_map:
            try:
                section_idx = int(entry.get("section") or entry.get("section_index"))
            except (TypeError, ValueError):
                continue
            if not (1 <= section_idx <= total):
                continue
            try:
                plan[section_idx - 1] = float(entry.get("pause_sec") or 0.0)
            except (TypeError, ValueError):
                continue

    output_lines: List[str] = []
    for idx, section in enumerate(sections):
        output_lines.extend(section.lines)
        pause_value = plan[idx] if idx < len(plan) else 0.0
        if pause_value and pause_value > 0:
            output_lines.append(f"[{pause_value:.2f}s]")
        if idx < len(sections) - 1:
            output_lines.append("")
    return "\n".join(output_lines).strip()


def _parse_tagged_tts(tagged_text: str) -> Tuple[str, List[Dict[str, Any]], int]:
    sections = _load_sections_from_text(tagged_text)
    pause_entries: List[Dict[str, Any]] = []
    for section in sections:
        clean_lines, tags = pause_tags.strip_pause_tags_from_lines(section.lines)
        if tags:
            pause_sec = pause_tags.extract_last_pause_seconds(tags)
            if pause_sec is not None:
                pause_entries.append(
                    {
                        "section": section.index,
                        "pause_sec": round(float(pause_sec), 4),
                        "source": "user_tag",
                        "raw_tag": tags[-1].raw,
                    }
                )
    plain_text = pause_tags.PAUSE_TAG_PATTERN.sub("", tagged_text)
    plain_text = re.sub(r"\n{3,}", "\n\n", plain_text).strip()
    return plain_text, pause_entries, len(sections)

