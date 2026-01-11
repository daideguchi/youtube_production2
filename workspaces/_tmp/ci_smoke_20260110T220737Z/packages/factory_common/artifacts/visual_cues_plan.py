from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from factory_common.artifacts.utils import atomic_write_json, read_json, utc_now_iso
from factory_common.timeline_manifest import sha1_file


VISUAL_CUES_PLAN_SCHEMA_V1 = "ytm.visual_cues_plan.v1"


class _SourceSrt(BaseModel):
    path: str
    sha1: str


class VisualCuesPlanSection(BaseModel):
    start_segment: int = Field(..., ge=1)
    end_segment: int = Field(..., ge=1)
    summary: str = ""
    visual_focus: str = ""
    emotional_tone: str = ""
    refined_prompt: str = ""
    persona_needed: bool = False
    role_tag: str = ""
    section_type: str = ""

    @model_validator(mode="before")
    @classmethod
    def _accept_compact_list(cls, v: Any) -> Any:
        # Accept the compact runbook format:
        # [start_segment,end_segment,summary,visual_focus,emotional_tone,persona_needed,role_tag,section_type,refined_prompt]
        if isinstance(v, list):
            start = v[0] if len(v) > 0 else None
            end = v[1] if len(v) > 1 else None
            return {
                "start_segment": start,
                "end_segment": end,
                "summary": str(v[2]) if len(v) > 2 and v[2] is not None else "",
                "visual_focus": str(v[3]) if len(v) > 3 and v[3] is not None else "",
                "emotional_tone": str(v[4]) if len(v) > 4 and v[4] is not None else "",
                "persona_needed": bool(v[5]) if len(v) > 5 else False,
                "role_tag": str(v[6]) if len(v) > 6 and v[6] is not None else "",
                "section_type": str(v[7]) if len(v) > 7 and v[7] is not None else "",
                "refined_prompt": str(v[8]) if len(v) > 8 and v[8] is not None else "",
            }
        return v

    @model_validator(mode="after")
    def _validate_range(self) -> "VisualCuesPlanSection":
        if self.end_segment < self.start_segment:
            raise ValueError("end_segment < start_segment")
        return self


class VisualCuesPlanArtifactV1(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_id: Literal[VISUAL_CUES_PLAN_SCHEMA_V1] = Field(default=VISUAL_CUES_PLAN_SCHEMA_V1, alias="schema")
    generated_at: str
    status: Literal["pending", "ready"] = "ready"
    source_srt: _SourceSrt
    segment_count: int = Field(..., ge=1)
    base_seconds: float = Field(..., gt=0.0)
    sections: List[VisualCuesPlanSection]
    episode: Optional[str] = None
    style_hint: str = ""
    llm_task: Dict[str, Any] = Field(default_factory=dict)
    meta: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_coverage(self) -> "VisualCuesPlanArtifactV1":
        if self.status == "pending":
            # Skeleton is allowed while waiting for THINK/AGENT completion or manual fill.
            return self
        if not self.sections:
            raise ValueError("sections is empty")

        secs = sorted(self.sections, key=lambda s: (s.start_segment, s.end_segment))
        if secs[0].start_segment != 1:
            raise ValueError("sections must start from segment 1")
        if secs[-1].end_segment != self.segment_count:
            raise ValueError("sections must cover to segment_count")

        cursor = 1
        for s in secs:
            if s.start_segment != cursor:
                raise ValueError(f"gap/overlap detected at segment {cursor} (next start={s.start_segment})")
            cursor = s.end_segment + 1
        if cursor != self.segment_count + 1:
            raise ValueError("sections do not fully cover segment range")

        return self


def write_visual_cues_plan(path: Path, artifact: VisualCuesPlanArtifactV1) -> None:
    atomic_write_json(path, artifact.model_dump(mode="json", by_alias=True))


def load_visual_cues_plan(
    path: Path,
    *,
    expected_srt_path: Optional[Path] = None,
    strict_sha1: bool = True,
) -> VisualCuesPlanArtifactV1:
    obj = read_json(path)
    plan = VisualCuesPlanArtifactV1.model_validate(obj)
    if expected_srt_path is not None:
        expected_sha1 = sha1_file(expected_srt_path)
        if strict_sha1 and plan.source_srt.sha1 != expected_sha1:
            raise ValueError(
                f"SRT sha1 mismatch for {path}: expected {expected_sha1} but plan has {plan.source_srt.sha1}"
            )
    return plan


def build_visual_cues_plan_artifact(
    *,
    srt_path: Path,
    segment_count: int,
    base_seconds: float,
    sections: List[Dict[str, Any]] | List[VisualCuesPlanSection],
    episode: Optional[str] = None,
    style_hint: str = "",
    status: Literal["pending", "ready"] = "ready",
    llm_task: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> VisualCuesPlanArtifactV1:
    parsed_sections: List[VisualCuesPlanSection] = []
    for s in sections:
        parsed_sections.append(
            s if isinstance(s, VisualCuesPlanSection) else VisualCuesPlanSection.model_validate(s)
        )
    return VisualCuesPlanArtifactV1(
        generated_at=utc_now_iso(),
        status=status,
        source_srt=_SourceSrt(path=str(srt_path), sha1=sha1_file(srt_path)),
        segment_count=int(segment_count),
        base_seconds=float(base_seconds),
        sections=parsed_sections,
        episode=episode,
        style_hint=str(style_hint or ""),
        llm_task=dict(llm_task or {}),
        meta=dict(meta or {}),
    )
