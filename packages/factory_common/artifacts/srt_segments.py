from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from factory_common.artifacts.utils import atomic_write_json, read_json, utc_now_iso
from factory_common.timeline_manifest import sha1_file


SRT_SEGMENTS_SCHEMA_V1 = "ytm.srt_segments.v1"


class _SourceSrt(BaseModel):
    path: str
    sha1: str


class SrtSegment(BaseModel):
    index: int = Field(..., ge=1)
    start_sec: float = Field(..., ge=0.0)
    end_sec: float = Field(..., ge=0.0)
    text: str = ""

    @model_validator(mode="after")
    def _validate_order(self) -> "SrtSegment":
        if self.end_sec < self.start_sec:
            raise ValueError(f"end_sec < start_sec for segment index={self.index}")
        return self


class SrtSegmentsArtifactV1(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    schema_id: Literal[SRT_SEGMENTS_SCHEMA_V1] = Field(default=SRT_SEGMENTS_SCHEMA_V1, alias="schema")
    generated_at: str
    source_srt: _SourceSrt
    segments: List[SrtSegment]
    episode: Optional[str] = None
    meta: Dict[str, Any] = Field(default_factory=dict)


def build_srt_segments_artifact(
    *,
    srt_path: Path,
    segments: List[Dict[str, Any]],
    episode: Optional[str] = None,
) -> SrtSegmentsArtifactV1:
    items: List[SrtSegment] = []
    for i, seg in enumerate(segments, start=1):
        try:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        except Exception:
            start, end = 0.0, 0.0
        items.append(
            SrtSegment(
                index=i,
                start_sec=round(start, 3),
                end_sec=round(end, 3),
                text=str(seg.get("text") or "").strip(),
            )
        )

    return SrtSegmentsArtifactV1(
        generated_at=utc_now_iso(),
        source_srt=_SourceSrt(path=str(srt_path), sha1=sha1_file(srt_path)),
        segments=items,
        episode=episode,
    )


def write_srt_segments_artifact(path: Path, artifact: SrtSegmentsArtifactV1) -> None:
    atomic_write_json(path, artifact.model_dump(mode="json", by_alias=True))


def load_srt_segments_artifact(
    path: Path,
    *,
    expected_srt_path: Optional[Path] = None,
    strict_sha1: bool = True,
) -> SrtSegmentsArtifactV1:
    obj = read_json(path)
    art = SrtSegmentsArtifactV1.model_validate(obj)
    if expected_srt_path is not None:
        expected_sha1 = sha1_file(expected_srt_path)
        if strict_sha1 and art.source_srt.sha1 != expected_sha1:
            raise ValueError(
                f"SRT sha1 mismatch for {path}: expected {expected_sha1} but artifact has {art.source_srt.sha1}"
            )
    return art
