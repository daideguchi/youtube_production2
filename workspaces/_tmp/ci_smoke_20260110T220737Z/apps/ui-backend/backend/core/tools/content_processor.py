from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from factory_common.text_sanitizer import strip_meta_from_script


class ContentProcessor:
    """Utility helpers for UI-side content cleanup."""

    def __init__(self, project_root: Optional[Path] = None, **_: Any):
        self.project_root = Path(project_root).resolve() if project_root else None

    @staticmethod
    def sanitize_for_tts(text: str) -> str:
        """Normalize user-provided text into a safer TTS input."""
        src = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = []
        for line in src.splitlines():
            stripped = line.strip()
            if not stripped:
                lines.append("")
                continue
            if stripped.startswith("#"):
                stripped = stripped.lstrip("#").strip()
            stripped = re.sub(r"^[-*•]\s+", "", stripped)
            lines.append(stripped)
        normalized = "\n".join(lines)
        sanitized = strip_meta_from_script(normalized).text
        return sanitized.strip()

    # Back-compat placeholder for older callers
    def process(self, *args: Any, **kwargs: Any) -> dict:
        return {}
