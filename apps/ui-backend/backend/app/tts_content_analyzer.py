from __future__ import annotations

from typing import List, Tuple

from backend.audio import pause_tags
from backend.app.tts_models import TTSIssue
from backend.core.tools.content_processor import ContentProcessor


def analyze_tts_content(raw: str) -> Tuple[str, List[TTSIssue]]:
    normalized_input = raw.replace("\r\n", "\n").replace("\r", "\n")
    issues: List[TTSIssue] = []
    sanitized_lines: List[str] = []
    for idx, line in enumerate(normalized_input.splitlines(), 1):
        stripped = line.strip()
        cleaned_line, _ = pause_tags.remove_pause_tags(stripped)
        sanitized_lines.append(cleaned_line)
        if not cleaned_line:
            continue
        ascii_letters = sum(ch.isalpha() and ch.isascii() for ch in cleaned_line)
        ratio = ascii_letters / len(cleaned_line) if cleaned_line else 0.0
        if ratio >= 0.3:
            issues.append(
                TTSIssue(
                    type="non_japanese_ratio",
                    line=idx,
                    detail=cleaned_line[:80],
                )
            )
        if len(cleaned_line) > 120:
            issues.append(
                TTSIssue(
                    type="line_too_long",
                    line=idx,
                    detail=str(len(cleaned_line)),
                )
            )
        if any(token in cleaned_line for token in ("(", ")", "[", "]", "{", "}", "<", ">", "（", "）", "［", "］")):
            issues.append(
                TTSIssue(
                    type="bracket_detected",
                    line=idx,
                    detail=cleaned_line[:80],
                )
            )
    sans_tags_text = "\n".join(sanitized_lines)
    sanitized = ContentProcessor.sanitize_for_tts(sans_tags_text)
    return sanitized, issues
