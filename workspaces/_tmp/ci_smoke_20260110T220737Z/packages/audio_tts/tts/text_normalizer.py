from __future__ import annotations

import re

_MIDDLE_DOT_JOIN_RE = re.compile(r"(?<=[A-Za-z0-9ァ-ヺー])・(?=[A-Za-z0-9ァ-ヺー])")


def normalize_text_for_tts(text: str) -> str:
    """
    Normalize text for TTS engines.

    Currently:
    - Joins middle dots (・) used within Katakana/ASCII names to avoid unnatural pauses.
    """
    if not text:
        return text

    normalized = text.replace("･", "・").replace("·", "・")
    if "・" not in normalized:
        return normalized

    return _MIDDLE_DOT_JOIN_RE.sub("", normalized)

