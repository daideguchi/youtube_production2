from __future__ import annotations

import json
import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from factory_common.llm_router import get_router

logger = logging.getLogger(__name__)


def is_think_or_agent_mode() -> bool:
    return (os.getenv("LLM_MODE") or "").strip().lower() in ("think", "agent")


def _truncate(text: str, limit: int) -> str:
    t = " ".join((text or "").split())
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 1)].rstrip() + "…"


def _combine_segments_for_prompt(segments: List[Dict[str, Any]], max_chars: int = 30_000) -> str:
    lines: List[str] = []
    for i, seg in enumerate(segments, start=1):
        try:
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", 0.0))
        except Exception:
            start, end = 0.0, 0.0
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"[{i}@{start:.2f}-{end:.2f}] {text}")
    story = "\n".join(lines)
    if len(story) > max_chars:
        story = story[:max_chars] + "\n...(truncated)"
    return story


def _extract_json_object(text: str) -> str:
    """
    Extract a top-level JSON object from a possibly noisy LLM response.
    - Picks first '{' and tries to find its matching closing brace using a stack.
    - If truncated, appends missing closers.
    """
    if not text:
        return ""
    s = text.strip()
    start = s.find("{")
    if start < 0:
        return ""

    in_str = False
    escape = False
    stack: List[str] = []
    end_idx: int | None = None

    for i, ch in enumerate(s[start:], start=start):
        if in_str:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_str = False
            continue

        if ch == '"':
            in_str = True
            continue
        if ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in ("}", "]"):
            if stack and ch == stack[-1]:
                stack.pop()
                if not stack:
                    end_idx = i
                    break

    if end_idx is not None:
        json_str = s[start : end_idx + 1]
    else:
        json_str = s[start:].strip()
        if stack:
            json_str = json_str + "".join(reversed(stack))

    # Remove trailing commas (common LLM glitch)
    json_str = re.sub(r",\s*}", "}", json_str)
    json_str = re.sub(r",\s*]", "]", json_str)
    return json_str


@dataclass(frozen=True)
class PlannedSection:
    start_segment: int  # 1-based inclusive
    end_segment: int  # 1-based inclusive
    summary: str = ""
    visual_focus: str = ""
    emotional_tone: str = ""
    persona_needed: bool = False
    role_tag: str = ""
    section_type: str = ""


def _coerce_sections(obj: Any, *, segment_count: int) -> List[PlannedSection]:
    if not isinstance(obj, dict):
        raise ValueError("plan JSON must be an object")

    raw = obj.get("sections")
    if raw is None:
        # Accept alternative key
        raw = obj.get("cues")
    if not isinstance(raw, list) or not raw:
        raise ValueError("plan JSON missing non-empty 'sections' array")

    sections: List[PlannedSection] = []
    for item in raw:
        if isinstance(item, list):
            # Legacy compact format:
            # [start_segment,end_segment,summary,visual_focus,emotional_tone,persona_needed,role_tag,section_type]
            start = item[0] if len(item) > 0 else None
            end = item[1] if len(item) > 1 else None
            summary = str(item[2]) if len(item) > 2 and item[2] is not None else ""
            visual_focus = str(item[3]) if len(item) > 3 and item[3] is not None else ""
            emotional_tone = str(item[4]) if len(item) > 4 and item[4] is not None else ""
            persona_needed = bool(item[5]) if len(item) > 5 else False
            role_tag = str(item[6]) if len(item) > 6 and item[6] is not None else ""
            section_type = str(item[7]) if len(item) > 7 and item[7] is not None else ""
        elif isinstance(item, dict):
            start = item.get("start_segment") or item.get("start") or item.get("start_idx")
            end = item.get("end_segment") or item.get("end") or item.get("end_idx")
            summary = str(item.get("summary") or "")
            visual_focus = str(item.get("visual_focus") or "")
            emotional_tone = str(item.get("emotional_tone") or "")
            persona_needed = bool(item.get("persona_needed") or False)
            role_tag = str(item.get("role_tag") or "")
            section_type = str(item.get("section_type") or "")
        else:
            continue

        try:
            s_i = int(start)
            e_i = int(end)
        except Exception:
            continue

        if s_i < 1:
            s_i = 1
        if e_i > segment_count:
            e_i = segment_count
        if e_i < s_i:
            continue

        sections.append(
            PlannedSection(
                start_segment=s_i,
                end_segment=e_i,
                summary=_truncate(summary, 60),
                visual_focus=_truncate(visual_focus, 180),
                emotional_tone=_truncate(emotional_tone, 40),
                persona_needed=bool(persona_needed),
                role_tag=_truncate(role_tag, 40),
                section_type=_truncate(section_type, 40),
            )
        )

    if not sections:
        raise ValueError("no valid sections parsed from plan")

    # Sort and lightly repair overlap/gaps by expanding/advancing boundaries.
    sections = sorted(sections, key=lambda x: (x.start_segment, x.end_segment))
    repaired: List[PlannedSection] = []
    prev_end = 0
    for sec in sections:
        start = max(sec.start_segment, prev_end + 1)
        end = max(start, sec.end_segment)
        if start != sec.start_segment or end != sec.end_segment:
            logger.warning(
                "Plan boundary adjusted: [%d,%d] -> [%d,%d]",
                sec.start_segment,
                sec.end_segment,
                start,
                end,
            )
        repaired.append(
            PlannedSection(
                start_segment=start,
                end_segment=end,
                summary=sec.summary,
                visual_focus=sec.visual_focus,
                emotional_tone=sec.emotional_tone,
                persona_needed=sec.persona_needed,
                role_tag=sec.role_tag,
                section_type=sec.section_type,
            )
        )
        prev_end = end
        if prev_end >= segment_count:
            break

    # Ensure full coverage to the last segment.
    if repaired and repaired[-1].end_segment < segment_count:
        last = repaired[-1]
        repaired[-1] = PlannedSection(
            start_segment=last.start_segment,
            end_segment=segment_count,
            summary=last.summary,
            visual_focus=last.visual_focus,
            emotional_tone=last.emotional_tone,
            persona_needed=last.persona_needed,
            role_tag=last.role_tag,
            section_type=last.section_type,
        )
    if repaired and repaired[0].start_segment > 1:
        first = repaired[0]
        repaired[0] = PlannedSection(
            start_segment=1,
            end_segment=first.end_segment,
            summary=first.summary,
            visual_focus=first.visual_focus,
            emotional_tone=first.emotional_tone,
            persona_needed=first.persona_needed,
            role_tag=first.role_tag,
            section_type=first.section_type,
        )

    # Final normalization: ensure strictly consecutive
    normalized: List[PlannedSection] = []
    cursor = 1
    for sec in repaired:
        if sec.end_segment < cursor:
            continue
        start = max(cursor, sec.start_segment)
        end = max(start, sec.end_segment)
        normalized.append(
            PlannedSection(
                start_segment=start,
                end_segment=end,
                summary=sec.summary,
                visual_focus=sec.visual_focus,
                emotional_tone=sec.emotional_tone,
                persona_needed=sec.persona_needed,
                role_tag=sec.role_tag,
                section_type=sec.section_type,
            )
        )
        cursor = end + 1
        if cursor > segment_count:
            break

    if not normalized:
        raise ValueError("sections normalization produced empty result")
    if normalized[0].start_segment != 1 or normalized[-1].end_segment != segment_count:
        raise ValueError("sections do not cover full segment range after normalization")

    return normalized


def _default_target_sections(*, segments: List[Dict[str, Any]], base_seconds: float) -> int:
    if not segments:
        return 0
    total_duration = float(segments[-1]["end"]) - float(segments[0]["start"])
    target = max(10, int(math.ceil(total_duration / max(1.0, float(base_seconds)))))
    env_override = os.getenv("SRT2IMAGES_TARGET_SECTIONS")
    if env_override:
        try:
            ov = int(env_override)
            if ov >= 5:
                target = ov
        except Exception:
            logger.warning("Invalid SRT2IMAGES_TARGET_SECTIONS=%s (must be int)", env_override)
    return target


def plan_sections_via_router(
    *,
    segments: List[Dict[str, Any]],
    channel_id: Optional[str],
    base_seconds: float,
    style_hint: str = "",
) -> List[PlannedSection]:
    if not segments:
        return []
    story = _combine_segments_for_prompt(segments)
    seg_count = len([s for s in segments if (s.get("text") or "").strip()])
    seg_count = seg_count or len(segments)

    target_sections = _default_target_sections(segments=segments, base_seconds=base_seconds)
    min_sections = max(5, target_sections - 1)
    max_sections = target_sections + 1

    extra_rapid = ""
    if (channel_id or "").upper() == "CH01":
        extra_rapid = (
            "\n"
            "- CRITICAL FOR CH01: rapid pacing; prefer shorter cuts when actions/thoughts change.\n"
            "- Adjacent sections MUST vary camera/pose/angle/subject to avoid repetition.\n"
        )

    style_block = f"\nChannel style hints:\n{style_hint}\n" if style_hint.strip() else ""

    prompt = f"""
You are preparing storyboard image cues for a narrated YouTube video.
Input is a Japanese SRT script with numbered segments like [index@start-end].

Split the script into between {min_sections} and {max_sections} visual sections.
Each section must:
- Cover consecutive SRT segments (no overlap, no gaps; the full script must be covered).
- Average around ~{base_seconds:.1f}s per image, but DO NOT be perfectly uniform; create pacing variation.
- Describe ONE clear visual idea the viewer should picture (concrete action/pose/setting/props/lighting).
- Avoid putting text inside the scene.
- Do NOT invent extra characters. Do NOT default to monks/meditation/正座/赤鉢巻/鎌おじさん unless the script explicitly demands it.
{extra_rapid}
Return ONLY a JSON object (no markdown) with this schema:
{{"sections":[[start_segment,end_segment,summary,visual_focus,emotional_tone,persona_needed,role_tag,section_type],...]}}

Field rules:
- start_segment/end_segment: 1-based inclusive indices from the markers.
- summary: <= 30 Japanese characters (short label).
- visual_focus: <= 14 English words, concrete camera-ready subject (must differ from adjacent).
- emotional_tone: <= 2 words.
- persona_needed: boolean; true ONLY if recurring characters must stay consistent.
- role_tag: one of explanation|story|dialogue|list_item|metaphor|quote|hook|cta|recap|transition|viewer_address
- section_type: one of story|dialogue|exposition|list|analysis|instruction|context|other
{style_block}
Script:
{story}
""".strip()

    router = get_router()
    content = router.call(
        task="visual_image_cues_plan",
        messages=[{"role": "user", "content": prompt}],
        response_format="json_object",
        temperature=0.3,
    )
    json_str = _extract_json_object(str(content or ""))
    if not json_str:
        raise ValueError("failed to extract JSON object from plan response")
    data = json.loads(json_str)
    return _coerce_sections(data, segment_count=len(segments))


def make_cues_from_sections(
    *,
    segments: List[Dict[str, Any]],
    sections: List[PlannedSection],
    fps: int,
) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    if not segments or not sections:
        return cues

    for i, sec in enumerate(sections, start=1):
        start_idx0 = max(0, sec.start_segment - 1)
        end_idx0 = min(len(segments) - 1, sec.end_segment - 1)
        slice_segments = segments[start_idx0 : end_idx0 + 1]
        if not slice_segments:
            continue

        start_sec = float(slice_segments[0]["start"])
        end_sec = float(slice_segments[-1]["end"])
        text_joined = " ".join(str(s.get("text") or "").strip() for s in slice_segments if str(s.get("text") or "").strip()).strip()

        cue = {
            "index": i,
            "start_sec": round(start_sec, 3),
            "end_sec": round(end_sec, 3),
            "duration_sec": round(end_sec - start_sec, 3),
            "text": text_joined,
            "summary": sec.summary or _truncate(text_joined, 60),
            "visual_focus": sec.visual_focus.strip(),
            "emotional_tone": sec.emotional_tone.strip(),
            "context_reason": "",
            "section_type": sec.section_type.strip(),
            "role_tag": sec.role_tag.strip(),
            "use_persona": bool(sec.persona_needed or (sec.section_type in ("story", "dialogue"))),
        }
        cue["start_frame"] = int(round(cue["start_sec"] * fps))
        cue["end_frame"] = int(round(cue["end_sec"] * fps))
        cue["duration_frames"] = max(1, cue["end_frame"] - cue["start_frame"])
        cues.append(cue)
    return cues

