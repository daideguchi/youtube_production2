from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class PauseTag:
    raw: str
    seconds: float


# Matches pause tags like: [0.50s]
# NOTE: We intentionally require the trailing "s" so we don't clobber citations like [13].
PAUSE_TAG_PATTERN = re.compile(r"\[\s*(\d+(?:\.\d+)?)\s*s\s*\]", flags=re.IGNORECASE)


def iter_pause_tags(text: str) -> Iterable[PauseTag]:
    for match in PAUSE_TAG_PATTERN.finditer(text or ""):
        raw = match.group(0)
        sec_raw = match.group(1)
        try:
            seconds = float(sec_raw)
        except (TypeError, ValueError):
            continue
        yield PauseTag(raw=raw, seconds=seconds)


def remove_pause_tags(text: str) -> Tuple[str, List[PauseTag]]:
    tags = list(iter_pause_tags(text))
    cleaned = PAUSE_TAG_PATTERN.sub("", text or "")
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned).strip()
    return cleaned, tags


def strip_pause_tags_from_lines(lines: Sequence[str]) -> Tuple[List[str], List[PauseTag]]:
    cleaned_lines: List[str] = []
    tags: List[PauseTag] = []
    for line in lines:
        cleaned, found = remove_pause_tags(line)
        tags.extend(found)
        if cleaned:
            cleaned_lines.append(cleaned)
    return cleaned_lines, tags


def extract_last_pause_seconds(tags: Sequence[PauseTag]) -> Optional[float]:
    if not tags:
        return None
    return float(tags[-1].seconds)


def apply_pause_tags(text: str) -> str:
    """Back-compat noop (pause tags are inserted elsewhere)."""
    return text
