from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from video_pipeline.src.srt2images.srt_parser import parse_srt

from .manifest import build_manifest, compute_prompt_hash, segment_id
from .style_preset import StylePreset
from .text_utils import (
    join_japanese_phrases,
    make_scene_text,
    sanitize_prompt_for_vrew,
    split_sentences_jp,
    strip_banned_terms,
    validate_vrew_prompt_line,
)


def _cap_max(prompt: str, max_chars: int) -> str:
    if not max_chars or len(prompt) <= max_chars:
        return prompt
    if max_chars <= 1:
        return "。"
    return prompt[: max_chars - 1].rstrip() + "。"


def _build_prompt(preset: StylePreset, scene: str) -> str:
    core = join_japanese_phrases([preset.style_prefix, scene, preset.constraints])
    core = strip_banned_terms(core, preset.banned_terms)
    return sanitize_prompt_for_vrew(core)


def _segments_from_srt(path: Path) -> List[Dict[str, Any]]:
    base = parse_srt(path)
    out: List[Dict[str, Any]] = []
    for idx, seg in enumerate(base, start=1):
        start_ms = int(round(float(seg["start"]) * 1000))
        end_ms = int(round(float(seg["end"]) * 1000))
        out.append(
            {
                "queue_index": idx,
                "start_ms": max(0, start_ms),
                "end_ms": max(0, end_ms),
                "source_text": str(seg.get("text") or "").strip(),
            }
        )
    return out


def _segments_from_txt(path: Path, *, default_duration_ms: int) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    sentences = split_sentences_jp(text)
    out: List[Dict[str, Any]] = []
    cur = 0
    dur = max(0, int(default_duration_ms))
    for idx, sentence in enumerate(sentences, start=1):
        start_ms = cur
        end_ms = cur + dur
        cur = end_ms
        out.append(
            {
                "queue_index": idx,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "source_text": str(sentence).strip(),
            }
        )
    return out


def generate_vrew_prompts_and_manifest(
    *,
    source_type: str,
    source_path: Path,
    preset: StylePreset,
    project_id: str,
    scene_max_chars: int = 70,
    min_chars: int = 20,
    max_chars: int = 220,
) -> Tuple[List[str], Dict[str, Any]]:
    if source_type not in {"srt", "txt"}:
        raise ValueError("source_type must be 'srt' or 'txt'")
    if source_type == "srt":
        base_segments = _segments_from_srt(source_path)
    else:
        base_segments = _segments_from_txt(source_path, default_duration_ms=preset.default_duration_ms)

    prompts: List[str] = []
    manifest_segments: List[Dict[str, Any]] = []
    fmt = str(preset.image_spec.get("format") or "png").lower().strip(".")

    for seg in base_segments:
        q = int(seg["queue_index"])
        src = str(seg.get("source_text") or "").strip()
        scene = make_scene_text(src, max_chars=scene_max_chars)
        prompt = _build_prompt(preset, scene)

        # Post validation/fallback
        prompt = _cap_max(prompt, max_chars=max_chars)
        errors = validate_vrew_prompt_line(prompt, min_chars=min_chars, max_chars=max_chars, banned_terms=preset.banned_terms)
        if errors:
            # Guaranteed-safe fallback (still includes style+constraints)
            prompt = _cap_max(sanitize_prompt_for_vrew(join_japanese_phrases([preset.style_prefix, preset.constraints])), max_chars=max_chars)

        prompts.append(prompt)
        manifest_segments.append(
            {
                "queue_index": q,
                "segment_id": segment_id(q),
                "start_ms": int(seg["start_ms"]),
                "end_ms": int(seg["end_ms"]),
                "source_text": src,
                "prompt": prompt,
                "prompt_hash": compute_prompt_hash(prompt),
                "image_path": f"images/img_{q:04d}.{fmt}",
                "status": "pending",
                "error": None,
            }
        )

    manifest = build_manifest(project_id=project_id, source_type=source_type, image_spec=preset.image_spec, segments=manifest_segments)
    return prompts, manifest

