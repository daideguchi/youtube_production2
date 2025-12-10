"""Validate required outputs for stages in the new script_pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Any

from .sot import load_status

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / "script_pipeline" / "data"


def validate_stage(channel: str, video: str, stage_defs: List[Dict[str, Any]]) -> List[str]:
    """Return a list of validation errors (empty = OK)."""
    errors: List[str] = []
    st = load_status(channel, video)
    base = DATA_ROOT / channel / video

    def _replace_tokens(p: str) -> Path:
        return base / p.replace("CHxx", channel).replace("NNN", video)

    for sd in stage_defs:
        name = sd.get("name")
        outputs = sd.get("outputs") or []
        # custom validation for script_review: assembled.md 内容の禁止記号チェック
        if name == "script_review":
            assembled = base / "content" / "assembled.md"
            if assembled.exists():
                try:
                    txt = assembled.read_text(encoding="utf-8")
                    if "・" in txt:
                        errors.append(f"[{name}] forbidden bullet '・' found in {assembled}")
                except Exception as exc:  # pragma: no cover
                    errors.append(f"[{name}] cannot read {assembled}: {exc}")
        # dynamic chapters: require all chapters present
        if name == "script_draft":
            outline = base / "content" / "outline.md"
            chapters: List[Path] = []
            if outline.exists():
                import re
                pat = re.compile(r"^##\\s*第(\\d+)章")
                nums = []
                for line in outline.read_text(encoding="utf-8").splitlines():
                    m = pat.match(line.strip())
                    if m:
                        try:
                            nums.append(int(m.group(1)))
                        except Exception:
                            pass
                if nums:
                    for n in nums:
                        chapters.append(base / f"content/chapters/chapter_{n}.md")
            if not chapters:
                chapters.append(base / "content/chapters/chapter_1.md")
            for p in chapters:
                if not p.exists():
                    errors.append(f"[{name}] missing: {p}")
                    continue
                if p.stat().st_size == 0:
                    errors.append(f"[{name}] empty file: {p}")
            continue
        for out in outputs:
            if not out.get("required"):
                continue
            p = _replace_tokens(out["path"])
            if not p.exists():
                errors.append(f"[{name}] missing: {p}")
                continue
            try:
                if p.is_file() and p.stat().st_size == 0:
                    errors.append(f"[{name}] empty file: {p}")
            except Exception as exc:  # pragma: no cover
                errors.append(f"[{name}] cannot stat {p}: {exc}")
    return errors
