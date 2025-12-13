"""Validate required outputs for stages in the new script_pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Any

from factory_common.paths import audio_final_dir, script_data_root

from .sot import load_status

DATA_ROOT = script_data_root()


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


def validate_completed_outputs(channel: str, video: str, stage_defs: List[Dict[str, Any]]) -> List[str]:
    """Validate required outputs only for stages marked completed in status.json.

    This is intended for guard/health checks where in-progress videos should not
    fail just because future-stage outputs do not exist yet.

    It also applies artifact lifecycle rules:
    - Once `script_review` is completed, upstream intermediates (research/outline/chapters)
      are allowed to be missing (they may have been purged/archived).
    - Once `script_validation` is completed, `quality_check` intermediates are allowed missing.
    - For `audio_synthesis`, only durable outputs under `workspaces/audio/final/**` are required.
    """
    errors: List[str] = []
    st = load_status(channel, video)
    base = DATA_ROOT / channel / video

    def _file_ok(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            return (not path.is_file()) or path.stat().st_size > 0
        except Exception:
            return False

    def _replace_tokens(p: str) -> Path:
        return base / p.replace("CHxx", channel).replace("NNN", video)

    stage_status = {name: info.status for name, info in st.stages.items()}
    script_review_done = stage_status.get("script_review") == "completed"
    script_validation_done = stage_status.get("script_validation") == "completed"
    audio_done = stage_status.get("audio_synthesis") == "completed"

    for sd in stage_defs:
        name = sd.get("name")
        if not name:
            continue
        state = st.stages.get(name)
        if state is None or state.status != "completed":
            continue

        # Allow purged intermediates once assembled script exists.
        if script_review_done and name in {"topic_research", "script_outline", "chapter_brief", "script_draft"}:
            continue
        # Allow purged quality review after final validation.
        if script_validation_done and name == "quality_check":
            continue

        if name == "script_review":
            assembled_candidates = [
                base / "content" / "final" / "assembled.md",
                base / "content" / "assembled.md",
            ]
            assembled = next((p for p in assembled_candidates if p.exists()), assembled_candidates[-1])
            if not _file_ok(assembled):
                errors.append(f"[{name}] missing: {assembled}")
            continue

        if name == "audio_synthesis":
            if not audio_done:
                # Only validate completed stage.
                continue
            final_dir = audio_final_dir(channel, video)
            wav_path = final_dir / f"{channel}-{str(video).zfill(3)}.wav"
            srt_path = final_dir / f"{channel}-{str(video).zfill(3)}.srt"
            if not _file_ok(wav_path):
                errors.append(f"[{name}] missing: {wav_path}")
            if not _file_ok(srt_path):
                errors.append(f"[{name}] missing: {srt_path}")
            continue

        # dynamic chapters: require all chapters present (only when assembled is not complete yet)
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
                if not _file_ok(p):
                    errors.append(f"[{name}] missing: {p}")
            continue

        outputs = sd.get("outputs") or []
        for out in outputs:
            if not out.get("required"):
                continue
            p = _replace_tokens(out["path"])
            if not _file_ok(p):
                errors.append(f"[{name}] missing: {p}")
    return errors
