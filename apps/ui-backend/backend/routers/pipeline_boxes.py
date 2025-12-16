from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from factory_common.artifacts.llm_text_output import LlmTextOutputArtifactV1, load_llm_text_artifact
from factory_common.artifacts.utils import atomic_write_json
from factory_common.paths import repo_root as ssot_repo_root
from factory_common.paths import script_data_root
from factory_common.timeline_manifest import sha1_file

try:
    from script_pipeline.runner import SCRIPT_MANIFEST_FILENAME, _load_stage_defs, _normalize_llm_output, _write_script_manifest
    from script_pipeline.sot import load_status
except Exception:  # pragma: no cover - optional in limited envs
    SCRIPT_MANIFEST_FILENAME = "script_manifest.json"
    _load_stage_defs = None  # type: ignore[assignment]
    _normalize_llm_output = None  # type: ignore[assignment]
    _write_script_manifest = None  # type: ignore[assignment]
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


@router.get("/api/channels/{channel}/videos/{video}/script-manifest")
def get_script_manifest(channel: str, video: str) -> Dict[str, Any]:
    ch = _normalize_channel(channel)
    no = _normalize_video(video)
    base = _script_base_dir(ch, no)
    manifest_path = base / SCRIPT_MANIFEST_FILENAME
    return _read_json_limited(manifest_path)


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

