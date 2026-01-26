from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.app.episode_store import _detect_artifact_path, video_base_dir
from backend.app.path_utils import PROJECT_ROOT, safe_exists, safe_is_file
from backend.app.stage_status_utils import _stage_status_value
from backend.app.status_models import STAGE_ORDER

SCRIPT_DUMMY_MARKERS = (
    "この動画の台本本文は外部管理です",
    "ダミー本文を配置しています",
)

SCRIPT_ASSEMBLED_MILESTONE_STAGES = (
    # Script pipeline (current / new)
    "topic_research",
    "script_outline",
    "script_master_plan",
    "chapter_brief",
    "script_draft",
    "script_review",
    # Legacy / compatibility
    "script_enhancement",
    "quality_check",
)

SCRIPT_POST_VALIDATION_AUTOCOMPLETE_STAGES = (
    # Some pipelines don't emit these stages in status.json (UI treats missing as pending),
    # but they are effectively "skipped" once validation is completed.
    "script_polish_ai",
    "script_audio_ai",
    "script_tts_prepare",
)


def _ensure_stage_slot(stages: Dict[str, Any], key: str) -> Dict[str, Any]:
    slot = stages.get(key)
    if not isinstance(slot, dict):
        slot = {}
        stages[key] = slot
    slot.setdefault("status", "pending")
    return slot


def _inject_audio_completion_from_artifacts(
    channel_code: str, video_number: str, stages: Dict[str, Any], metadata: Dict[str, Any]
) -> Tuple[Dict[str, Any], bool, bool]:
    """
    既存の status/stages に音声・字幕の完成を反映する（ファイルが存在する場合）。
    永続化はせずレスポンス上で補正する。
    """
    stages_copy = copy.deepcopy(stages) if isinstance(stages, dict) else {}
    audio_stage = _ensure_stage_slot(stages_copy, "audio_synthesis")
    srt_stage = _ensure_stage_slot(stages_copy, "srt_generation")

    audio_meta = metadata.get("audio", {}) if isinstance(metadata, dict) else {}
    synth_meta = audio_meta.get("synthesis", {}) if isinstance(audio_meta, dict) else {}
    final_wav = synth_meta.get("final_wav") if isinstance(synth_meta, dict) else None
    if final_wav:
        audio_path = Path(final_wav)
        if not audio_path.is_absolute():
            audio_path = (PROJECT_ROOT / final_wav).resolve()
    else:
        audio_path = _detect_artifact_path(channel_code, video_number, ".wav")

    srt_meta = metadata.get("subtitles", {}) if isinstance(metadata, dict) else {}
    final_srt = srt_meta.get("final_srt") if isinstance(srt_meta, dict) else None
    if final_srt:
        srt_path = Path(final_srt)
        if not srt_path.is_absolute():
            srt_path = (PROJECT_ROOT / final_srt).resolve()
    else:
        srt_path = _detect_artifact_path(channel_code, video_number, ".srt")

    audio_exists = safe_exists(audio_path) if audio_path else False
    srt_exists = safe_exists(srt_path) if srt_path else False

    if audio_exists:
        audio_stage["status"] = "completed"
    if srt_exists:
        srt_stage["status"] = "completed"

    return stages_copy, audio_exists, srt_exists


def _resolve_script_artifact_candidates(
    *,
    base_dir: Path,
    metadata: Dict[str, Any],
) -> List[Path]:
    candidates: List[Path] = []

    # status.json may carry an explicit assembled_path (best-effort).
    assembled_path = None
    if isinstance(metadata, dict):
        assembled_path = metadata.get("assembled_path")
        script_meta = metadata.get("script") if assembled_path is None else None
        if assembled_path is None and isinstance(script_meta, dict):
            assembled_path = script_meta.get("assembled_path")
    if isinstance(assembled_path, str) and assembled_path.strip():
        try:
            p = Path(assembled_path)
            if not p.is_absolute():
                p = (PROJECT_ROOT / assembled_path).resolve()
            candidates.append(p)
        except Exception:
            pass

    candidates.extend(
        [
            base_dir / "content" / "assembled_human.md",
            base_dir / "content" / "assembled.md",
            # Legacy (backward-compat only; should not be reintroduced as canonical).
            base_dir / "content" / "final" / "assembled.md",
        ]
    )
    return candidates


def _a_text_file_ok(path: Path) -> bool:
    if not safe_exists(path) or not safe_is_file(path):
        return False
    try:
        if path.stat().st_size <= 0:
            return False
    except Exception:
        return False
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            head = handle.read(4096)
    except Exception:
        return False
    return not any(marker in head for marker in SCRIPT_DUMMY_MARKERS)


def _detect_script_a_text(
    *,
    base_dir: Path,
    metadata: Dict[str, Any],
) -> Optional[Path]:
    for candidate in _resolve_script_artifact_candidates(base_dir=base_dir, metadata=metadata):
        if _a_text_file_ok(candidate):
            return candidate
    return None


def _inject_script_completion_from_artifacts(
    channel_code: str,
    video_number: str,
    stages: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool]:
    """
    Derive script-stage progress from durable artifacts (A-text).

    Rationale:
    - The UI treats missing stage keys as "pending".
    - Stale/mixed status.json can claim early stages are pending even when assembled.md exists.
    - For progress display, "assembled present" is the most reliable milestone.

    This is a read-time override; it does NOT persist status.json.
    """
    stages_copy = copy.deepcopy(stages) if isinstance(stages, dict) else {}
    base_dir = video_base_dir(channel_code, video_number)
    a_text_path = _detect_script_a_text(base_dir=base_dir, metadata=metadata)
    assembled_ok = a_text_path is not None
    if not assembled_ok:
        return stages_copy, False

    for key in SCRIPT_ASSEMBLED_MILESTONE_STAGES:
        slot = _ensure_stage_slot(stages_copy, key)
        if _stage_status_value(slot) != "completed":
            slot["status"] = "completed"

    script_validation_done = _stage_status_value(stages_copy.get("script_validation")) == "completed"
    if script_validation_done:
        for key in SCRIPT_POST_VALIDATION_AUTOCOMPLETE_STAGES:
            slot = _ensure_stage_slot(stages_copy, key)
            if _stage_status_value(slot) != "completed":
                slot["status"] = "completed"

    return stages_copy, True


def _derive_effective_stages(
    *,
    channel_code: str,
    video_number: str,
    stages: Dict[str, Any],
    metadata: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool, bool, bool]:
    stages_effective, audio_exists, srt_exists = _inject_audio_completion_from_artifacts(
        channel_code, video_number, stages, metadata
    )
    stages_effective, a_text_ok = _inject_script_completion_from_artifacts(
        channel_code, video_number, stages_effective, metadata
    )
    return stages_effective, a_text_ok, audio_exists, srt_exists


def _derive_effective_video_status(
    *,
    raw_status: str,
    stages: Dict[str, Any],
    a_text_ok: bool,
    audio_exists: bool,
    srt_exists: bool,
    published_locked: bool = False,
) -> str:
    raw = str(raw_status or "").strip().lower()
    if published_locked:
        return "completed"
    if audio_exists and srt_exists:
        return "completed"

    if _stage_status_value(stages.get("script_validation")) == "completed" or raw == "script_validated":
        return "script_validated"
    if raw in {"script_ready", "script_completed"}:
        return "script_ready"
    if a_text_ok:
        return "script_ready"

    # Fallback: collapse into pending / in_progress / blocked / review based on stage states.
    any_blocked = any(_stage_status_value(stages.get(stage_key)) == "blocked" for stage_key in STAGE_ORDER)
    if any_blocked or str(raw_status or "").strip().lower() == "blocked":
        return "blocked"

    any_started = any(_stage_status_value(stage_entry) != "pending" for stage_entry in (stages or {}).values())
    return "in_progress" if any_started else "pending"
