from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from factory_common.artifacts.utils import atomic_write_json, read_json, utc_now_iso
from factory_common.timeline_manifest import sha1_file


LLM_TEXT_OUTPUT_SCHEMA_V1 = "ytm.llm_text_output.v1"


class SourceFile(BaseModel):
    path: str
    sha1: str


class OutputTarget(BaseModel):
    path: str
    sha1: Optional[str] = None


class LlmTextOutputArtifactV1(BaseModel):
    """
    Generic contract for "LLM-produced text" (markdown/JSON-as-text).

    This enables artifact-driven execution:
      - status=pending: fill `content` and mark ready
      - status=ready: pipeline writes output file from `content` without calling LLM
    """

    model_config = ConfigDict(populate_by_name=True)

    schema_id: Literal[LLM_TEXT_OUTPUT_SCHEMA_V1] = Field(default=LLM_TEXT_OUTPUT_SCHEMA_V1, alias="schema")
    generated_at: str = Field(default_factory=utc_now_iso)
    status: Literal["pending", "ready"] = "ready"

    # Identifiers
    stage: str
    task: str
    channel: Optional[str] = None
    video: Optional[str] = None

    # Output payload
    output: OutputTarget
    content: str = ""

    # Provenance / reproducibility
    sources: List[SourceFile] = Field(default_factory=list)
    llm_meta: Dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


def artifact_path_for_output(
    *,
    base_dir: Path,
    stage: str,
    output_path: Path,
    log_suffix: str = "",
) -> Path:
    rel = output_path
    try:
        rel = output_path.relative_to(base_dir)
    except Exception:
        rel = Path(output_path.name)
    safe = str(rel).replace("/", "__").replace("\\", "__")
    safe = "".join(ch if ch.isalnum() or ch in "._-__" else "_" for ch in safe)
    name = f"{stage}{log_suffix}__{safe}.json"
    return base_dir / "artifacts" / "llm" / name


def build_ready_artifact(
    *,
    stage: str,
    task: str,
    channel: str | None,
    video: str | None,
    output_path: Path,
    content: str,
    sources: List[SourceFile],
    llm_meta: Dict[str, Any] | None = None,
    notes: str = "",
) -> LlmTextOutputArtifactV1:
    sha1 = sha1_file(output_path) if output_path.exists() else None
    return LlmTextOutputArtifactV1(
        status="ready",
        stage=stage,
        task=task,
        channel=channel,
        video=video,
        output=OutputTarget(path=str(output_path), sha1=sha1),
        content=content or "",
        sources=list(sources or []),
        llm_meta=dict(llm_meta or {}),
        notes=notes or "",
    )


def build_pending_artifact(
    *,
    stage: str,
    task: str,
    channel: str | None,
    video: str | None,
    output_path: Path,
    sources: List[SourceFile],
    llm_meta: Dict[str, Any] | None = None,
    notes: str = "",
) -> LlmTextOutputArtifactV1:
    return LlmTextOutputArtifactV1(
        status="pending",
        stage=stage,
        task=task,
        channel=channel,
        video=video,
        output=OutputTarget(path=str(output_path), sha1=None),
        content="",
        sources=list(sources or []),
        llm_meta=dict(llm_meta or {}),
        notes=notes or "",
    )


def write_llm_text_artifact(path: Path, artifact: LlmTextOutputArtifactV1) -> None:
    atomic_write_json(path, artifact.model_dump(mode="json", by_alias=True))


def load_llm_text_artifact(path: Path) -> LlmTextOutputArtifactV1:
    obj = read_json(path)
    return LlmTextOutputArtifactV1.model_validate(obj)
