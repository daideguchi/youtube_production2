from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from factory_common import locks as coord_locks
from factory_common.artifacts.llm_text_output import LlmTextOutputArtifactV1, load_llm_text_artifact
from factory_common.artifacts.utils import atomic_write_json
from factory_common.episode_progress import build_episode_progress_view
from factory_common.paths import repo_root as ssot_repo_root
from factory_common.paths import script_data_root
from factory_common.timeline_manifest import sha1_file

try:
    from script_pipeline.runner import (
        SCRIPT_MANIFEST_FILENAME,
        _load_stage_defs,
        _normalize_llm_output,
        _write_script_manifest,
        reconcile_status as reconcile_script_status,
        run_stage as run_script_stage,
    )
    from script_pipeline.sot import load_status
except Exception:  # pragma: no cover - optional in limited envs
    SCRIPT_MANIFEST_FILENAME = "script_manifest.json"
    _load_stage_defs = None  # type: ignore[assignment]
    _normalize_llm_output = None  # type: ignore[assignment]
    _write_script_manifest = None  # type: ignore[assignment]
    reconcile_script_status = None  # type: ignore[assignment]
    run_script_stage = None  # type: ignore[assignment]
    load_status = None  # type: ignore[assignment]

router = APIRouter(tags=["pipeline-boxes"])


def _normalize_channel(channel: str) -> str:
    raw = (channel or "").strip()
    if not raw or Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="Invalid channel identifier")
    return raw.upper()


def _normalize_video(video: str) -> str:
    raw = (video or "").strip()
    if not raw or Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="Invalid video identifier")
    if not raw.isdigit():
        raise HTTPException(status_code=400, detail="Video identifier must be numeric")
    return raw.zfill(3)


def _script_base_dir(channel: str, video: str) -> Path:
    base = (script_data_root() / channel / video).resolve()
    if not base.exists() or not base.is_dir():
        raise HTTPException(status_code=404, detail="script directory not found")
    return base


def _safe_artifact_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="artifact_name is required")
    if Path(raw).name != raw:
        raise HTTPException(status_code=400, detail="artifact_name must be a filename")
    if not raw.endswith(".json"):
        raise HTTPException(status_code=400, detail="artifact_name must end with .json")
    if raw.startswith("."):
        raise HTTPException(status_code=400, detail="artifact_name must not start with '.'")
    return raw


def _read_json_limited(path: Path, *, max_bytes: int = 5_000_000) -> Dict[str, Any]:
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    try:
        if path.stat().st_size > max_bytes:
            raise HTTPException(status_code=400, detail="file too large")
        return json.loads(path.read_text(encoding="utf-8"))
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid json: {exc}") from exc


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)


def _read_text_best_effort(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")


def _strip_code_fences(text: str) -> str:
    t = str(text or "").strip()
    if t.startswith("```"):
        t = t.strip("`").strip()
    for prefix in ("json", "JSON"):
        if t.startswith(prefix):
            # Some models prefix outputs with "json" even when not asked.
            t = t[len(prefix) :].strip()
    return t.strip()


def _set_script_validation_pending(status_obj: Dict[str, Any]) -> None:
    stages = status_obj.get("stages")
    if not isinstance(stages, dict):
        stages = {}
        status_obj["stages"] = stages
    sv = stages.get("script_validation")
    if not isinstance(sv, dict):
        sv = {"status": "pending", "details": {}}
        stages["script_validation"] = sv
    sv["status"] = "pending"
    details = sv.get("details")
    if not isinstance(details, dict):
        details = {}
    # Clear prior error context to avoid stale UI.
    for key in ("error", "error_codes", "issues", "fix_hints", "llm_quality_gate"):
        details.pop(key, None)
    sv["details"] = details


class ScriptReviewApplyRequest(BaseModel):
    comment: str = Field(..., description="Review comment/instructions to apply to the A-text")
    expected_updated_at: Optional[str] = Field(
        default=None, description="Optimistic concurrency guard (status.json updated_at)"
    )
    dry_run: bool = Field(default=False, description="If true, do not write files; return revised text only")


class ScriptReviewApplyResponse(BaseModel):
    status: Literal["ok"] = "ok"
    updated_at: Optional[str] = None
    assembled_human: str
    llm: Optional[Dict[str, Any]] = None


@router.get("/api/channels/{channel}/videos/{video}/script-manifest")
def get_script_manifest(channel: str, video: str) -> Dict[str, Any]:
    ch = _normalize_channel(channel)
    no = _normalize_video(video)
    base = _script_base_dir(ch, no)
    manifest_path = base / SCRIPT_MANIFEST_FILENAME
    return _read_json_limited(manifest_path)


@router.get("/api/channels/{channel}/episode-progress")
def get_episode_progress(channel: str, videos: Optional[str] = None) -> Dict[str, Any]:
    """
    Derived, read-only progress view aggregated from multiple SoTs.

    Query:
      - videos: comma-separated list (e.g. "012,013") (optional)
    """
    ch = _normalize_channel(channel)
    vids = [v.strip() for v in str(videos or "").split(",") if v.strip()] if videos else None
    return build_episode_progress_view(ch, videos=vids)


@router.post("/api/channels/{channel}/videos/{video}/script-manifest/refresh")
def refresh_script_manifest(channel: str, video: str) -> Dict[str, Any]:
    if load_status is None or _load_stage_defs is None or _write_script_manifest is None:
        raise HTTPException(status_code=503, detail="script_pipeline is not available in this environment")
    ch = _normalize_channel(channel)
    no = _normalize_video(video)
    base = _script_base_dir(ch, no)
    try:
        st = load_status(ch, no)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=404, detail=f"status.json not found: {exc}") from exc
    try:
        stage_defs = _load_stage_defs()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to load stage defs: {exc}") from exc
    try:
        _write_script_manifest(base, st, stage_defs)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to write script manifest: {exc}") from exc
    return _read_json_limited(base / SCRIPT_MANIFEST_FILENAME)


@router.post("/api/channels/{channel}/videos/{video}/script-pipeline/reconcile")
def reconcile_script_pipeline(channel: str, video: str) -> Dict[str, Any]:
    if reconcile_script_status is None:
        raise HTTPException(status_code=503, detail="script_pipeline is not available in this environment")
    ch = _normalize_channel(channel)
    no = _normalize_video(video)
    base = _script_base_dir(ch, no)
    try:
        st = reconcile_script_status(ch, no, allow_downgrade=True)
        if _load_stage_defs is not None and _write_script_manifest is not None:
            try:
                stage_defs = _load_stage_defs()
                _write_script_manifest(base, st, stage_defs)
            except Exception:
                pass
    except SystemExit as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"reconcile failed: {exc}") from exc
    return _read_json_limited(base / "status.json")


@router.post("/api/channels/{channel}/videos/{video}/script-pipeline/run/{stage}")
def run_script_pipeline_stage(channel: str, video: str, stage: str) -> Dict[str, Any]:
    if run_script_stage is None:
        raise HTTPException(status_code=503, detail="script_pipeline is not available in this environment")
    ch = _normalize_channel(channel)
    no = _normalize_video(video)
    base = _script_base_dir(ch, no)
    stage_name = (stage or "").strip()
    if stage_name != "script_validation":
        raise HTTPException(status_code=400, detail="only script_validation can be executed via API")
    status_path = base / "status.json"
    if not status_path.exists():
        raise HTTPException(status_code=404, detail="status.json not found")
    try:
        run_script_stage(ch, no, stage_name)
    except SystemExit as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"stage run failed: {exc}") from exc
    return _read_json_limited(status_path)


@router.post(
    "/api/channels/{channel}/videos/{video}/script-review/apply",
    response_model=ScriptReviewApplyResponse,
)
def apply_script_review_comment(channel: str, video: str, payload: ScriptReviewApplyRequest) -> ScriptReviewApplyResponse:
    """
    Apply a human review comment to the full A-text (assembled_human.md), using LLMRouter.
    This endpoint never exposes API keys and respects coordination locks.
    """
    ch = _normalize_channel(channel)
    no = _normalize_video(video)
    base = _script_base_dir(ch, no)

    comment = str(payload.comment or "").strip()
    if not comment:
        raise HTTPException(status_code=400, detail="comment is required")

    status_path = base / "status.json"
    if not status_path.exists():
        raise HTTPException(status_code=404, detail="status.json not found")

    active_locks = coord_locks.default_active_locks_for_mutation()
    for p in (
        status_path,
        base / "content" / "assembled.md",
        base / "content" / "assembled_human.md",
    ):
        blocking = coord_locks.find_blocking_lock(p, active_locks)
        if blocking:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "blocked_by_lock",
                    "lock": {"id": blocking.lock_id, "created_by": blocking.created_by, "mode": blocking.mode},
                },
            )

    status_obj = _read_json_limited(status_path)
    expected = str(payload.expected_updated_at or "").strip()
    if expected:
        current = str(status_obj.get("updated_at") or "").strip()
        if current and current != expected:
            raise HTTPException(status_code=409, detail="最新の情報を再取得してからやり直してください。")

    content_dir = base / "content"
    assembled = content_dir / "assembled.md"
    assembled_human = content_dir / "assembled_human.md"
    if assembled.parent.name != "content" or assembled_human.parent.name != "content":
        raise HTTPException(status_code=400, detail="invalid content dir")

    src_path = assembled_human if assembled_human.exists() else assembled
    if not src_path.exists():
        raise HTTPException(status_code=404, detail="A-text not found (assembled_human.md / assembled.md)")
    src_text = _read_text_best_effort(src_path).strip()
    if not src_text:
        raise HTTPException(status_code=400, detail="A-text is empty")

    channel_note = ""
    if ch == "CH23":
        channel_note = (
            "CH23 policy: this is empathy-style narration. "
            "Do NOT encourage the viewer or give motivational lines; keep it observational/empathic."
        )

    prompt = (
        "You are a professional Japanese YouTube narration script editor.\n"
        "Task: Apply the human review comment to the full A-text with minimal necessary edits.\n"
        f"{channel_note}\n\n"
        "Hard rules for the output:\n"
        "- Output ONLY the revised A-text (no headings, no bullet lists, no numbering, no markdown fences).\n"
        "- Do NOT include URLs, citations, footnotes, or bracketed references.\n"
        "- Keep the story coherent; do not invent new facts.\n"
        "- Preserve the original tone and pacing unless the comment requires change.\n\n"
        "Human review comment:\n"
        f"{comment}\n\n"
        "Current A-text:\n"
        "```\n"
        f"{src_text}\n"
        "```\n"
    )

    try:
        from factory_common.llm_router import get_router
    except Exception as exc:  # pragma: no cover
        raise HTTPException(status_code=503, detail=f"LLMRouter is not available: {exc}") from exc

    router = get_router()
    try:
        result = router.call_with_raw(
            task="script_human_review_apply",
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"LLM failed: {exc}") from exc

    revised = _strip_code_fences(str(result.get("content") or "")).strip()
    if not revised:
        raise HTTPException(status_code=500, detail="LLM returned empty content")

    ts = _utc_now_iso()
    if not payload.dry_run:
        _atomic_write_text(assembled_human, revised)
        _atomic_write_text(assembled, revised)
        status_obj["updated_at"] = ts
        meta = status_obj.get("metadata")
        if not isinstance(meta, dict):
            meta = {}
            status_obj["metadata"] = meta
        meta["redo_script"] = False
        meta["redo_audio"] = True
        meta["audio_reviewed"] = False
        meta["review_comment_applied_at"] = ts
        _set_script_validation_pending(status_obj)
        atomic_write_json(status_path, status_obj)

    llm_meta = {
        "provider": result.get("provider"),
        "model": result.get("model"),
        "request_id": result.get("request_id"),
        "latency_ms": result.get("latency_ms"),
    }
    llm_meta = {k: v for k, v in llm_meta.items() if v is not None}
    return ScriptReviewApplyResponse(
        updated_at=None if payload.dry_run else ts,
        assembled_human=revised,
        llm=llm_meta or None,
    )


class LlmArtifactListItem(BaseModel):
    name: str
    status: Literal["pending", "ready"] | str
    stage: Optional[str] = None
    task: Optional[str] = None
    generated_at: Optional[str] = None
    output_path: Optional[str] = None
    output_sha1: Optional[str] = None
    content_chars: Optional[int] = None
    error: Optional[str] = None


@router.get("/api/channels/{channel}/videos/{video}/llm-artifacts", response_model=List[LlmArtifactListItem])
def list_llm_artifacts(channel: str, video: str) -> List[LlmArtifactListItem]:
    ch = _normalize_channel(channel)
    no = _normalize_video(video)
    base = _script_base_dir(ch, no)
    artifacts_dir = base / "artifacts" / "llm"
    if not artifacts_dir.exists() or not artifacts_dir.is_dir():
        return []

    items: List[LlmArtifactListItem] = []
    for p in sorted(artifacts_dir.glob("*.json")):
        name = p.name
        try:
            art = load_llm_text_artifact(p)
            items.append(
                LlmArtifactListItem(
                    name=name,
                    status=art.status,
                    stage=art.stage,
                    task=art.task,
                    generated_at=art.generated_at,
                    output_path=art.output.path,
                    output_sha1=art.output.sha1,
                    content_chars=len(art.content or ""),
                )
            )
        except Exception as exc:  # noqa: BLE001
            items.append(LlmArtifactListItem(name=name, status="error", error=str(exc)))
    return items


@router.get("/api/channels/{channel}/videos/{video}/llm-artifacts/{artifact_name}")
def get_llm_artifact(channel: str, video: str, artifact_name: str) -> Dict[str, Any]:
    ch = _normalize_channel(channel)
    no = _normalize_video(video)
    base = _script_base_dir(ch, no)
    safe_name = _safe_artifact_name(artifact_name)
    path = base / "artifacts" / "llm" / safe_name
    obj = _read_json_limited(path)
    # Validate schema when possible, but return raw for forward-compatibility.
    try:
        _ = LlmTextOutputArtifactV1.model_validate(obj)
    except Exception:
        pass
    return obj


class LlmArtifactUpdatePayload(BaseModel):
    status: Literal["pending", "ready"] = Field(..., description="pending | ready")
    content: str = Field("", description="LLM output text to write")
    notes: Optional[str] = Field(default=None, description="Optional notes")
    apply_output: bool = Field(default=False, description="Also write artifact.content to output.path")


def _resolve_output_path(*, base_dir: Path, output_path: str) -> Path:
    p = Path(output_path)
    resolved = p.resolve() if p.is_absolute() else (ssot_repo_root() / p).resolve()
    repo = ssot_repo_root().resolve()
    try:
        resolved.relative_to(repo)
    except Exception as exc:
        raise HTTPException(status_code=403, detail="artifact output path is outside repository") from exc
    try:
        resolved.relative_to(base_dir.resolve())
    except Exception as exc:
        raise HTTPException(status_code=403, detail="artifact output path is outside script base dir") from exc
    return resolved


@router.put("/api/channels/{channel}/videos/{video}/llm-artifacts/{artifact_name}")
def update_llm_artifact(
    channel: str,
    video: str,
    artifact_name: str,
    payload: LlmArtifactUpdatePayload = Body(...),
) -> Dict[str, Any]:
    ch = _normalize_channel(channel)
    no = _normalize_video(video)
    base = _script_base_dir(ch, no)
    safe_name = _safe_artifact_name(artifact_name)
    path = base / "artifacts" / "llm" / safe_name

    if not path.exists():
        raise HTTPException(status_code=404, detail="artifact not found")

    try:
        art = load_llm_text_artifact(path)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid artifact: {exc}") from exc

    if art.channel and art.channel.upper() != ch:
        raise HTTPException(status_code=400, detail="artifact.channel mismatch")
    if art.video and str(art.video).zfill(3) != no:
        raise HTTPException(status_code=400, detail="artifact.video mismatch")

    updated = art.model_copy(deep=True)
    updated.status = payload.status
    updated.content = payload.content or ""
    if payload.notes is not None:
        updated.notes = payload.notes
    if updated.status == "ready" and not updated.content.strip():
        raise HTTPException(status_code=400, detail="status=ready requires non-empty content")

    if payload.apply_output:
        if _normalize_llm_output is None:
            raise HTTPException(status_code=503, detail="script_pipeline is not available in this environment")
        out_path = _resolve_output_path(base_dir=base, output_path=updated.output.path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(updated.content.rstrip("\n") + "\n", encoding="utf-8")
        try:
            _normalize_llm_output(out_path, updated.stage)
        except Exception:
            # Fail-soft: keep output written even if normalization fails
            pass
        try:
            updated.output.sha1 = sha1_file(out_path)
        except Exception:
            updated.output.sha1 = None

    atomic_write_json(path, updated.model_dump(mode="json", by_alias=True))
    return updated.model_dump(mode="json", by_alias=True)
