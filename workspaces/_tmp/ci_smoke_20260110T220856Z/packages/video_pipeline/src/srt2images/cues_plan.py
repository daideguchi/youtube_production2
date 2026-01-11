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
    from factory_common.llm_exec_slots import effective_llm_mode

    return effective_llm_mode() in ("think", "agent")


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
    refined_prompt: str = ""
    persona_needed: bool = False
    role_tag: str = ""
    section_type: str = ""


def _coerce_sections(obj: Any, *, segment_count: int) -> List[PlannedSection]:
    if not isinstance(obj, dict):
        raise ValueError("plan JSON must be an object")

    raw = obj.get("sections")
    if raw is None:
        # Accept common alternative keys (LLMs often vary singular/plural).
        raw = obj.get("section")
    if raw is None:
        raw = obj.get("cues")
    if raw is None:
        raw = obj.get("cue")
    if not isinstance(raw, list) or not raw:
        raise ValueError("plan JSON missing non-empty 'sections' array")

    sections: List[PlannedSection] = []
    for item in raw:
        if isinstance(item, list):
            # Legacy compact format:
            # [start_segment,end_segment,summary,visual_focus,emotional_tone,persona_needed,role_tag,section_type,refined_prompt]
            start = item[0] if len(item) > 0 else None
            end = item[1] if len(item) > 1 else None
            summary = str(item[2]) if len(item) > 2 and item[2] is not None else ""
            visual_focus = str(item[3]) if len(item) > 3 and item[3] is not None else ""
            emotional_tone = str(item[4]) if len(item) > 4 and item[4] is not None else ""
            persona_needed = bool(item[5]) if len(item) > 5 else False
            role_tag = str(item[6]) if len(item) > 6 and item[6] is not None else ""
            section_type = str(item[7]) if len(item) > 7 and item[7] is not None else ""
            refined_prompt = str(item[8]) if len(item) > 8 and item[8] is not None else ""
        elif isinstance(item, dict):
            start = item.get("start_segment") or item.get("start") or item.get("start_idx")
            end = item.get("end_segment") or item.get("end") or item.get("end_idx")
            summary = str(item.get("summary") or "")
            visual_focus = str(item.get("visual_focus") or "")
            emotional_tone = str(item.get("emotional_tone") or "")
            refined_prompt = str(item.get("refined_prompt") or item.get("prompt") or "")
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
                refined_prompt=_truncate(refined_prompt, 420),
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
                refined_prompt=sec.refined_prompt,
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
            refined_prompt=last.refined_prompt,
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
            refined_prompt=first.refined_prompt,
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
                refined_prompt=sec.refined_prompt,
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


def _normalize_visual_focus_key(text: str) -> str:
    s = str(text or "").strip().lower()
    if not s:
        return ""
    # Normalize punctuation/whitespace so exact duplicates are reliably detected.
    s = re.sub(r"[\r\n\t]+", " ", s)
    s = re.sub(r"[\\-_/]+", " ", s)
    s = re.sub(r"[^\w\s]+", " ", s)
    return " ".join(s.split())


def _find_duplicate_visual_focus(sections: List["PlannedSection"]) -> Dict[str, List[int]]:
    seen: Dict[str, List[int]] = {}
    for idx, sec in enumerate(sections, start=1):
        key = _normalize_visual_focus_key(sec.visual_focus)
        if not key:
            continue
        seen.setdefault(key, []).append(idx)
    return {k: v for k, v in seen.items() if len(v) > 1}


def plan_sections_via_router(
    *,
    segments: List[Dict[str, Any]],
    channel_id: Optional[str],
    base_seconds: float,
    style_hint: str = "",
) -> List[PlannedSection]:
    if not segments:
        return []
    max_chars = int(os.getenv("SRT2IMAGES_CUES_PLAN_MAX_CHARS", "20000"))
    story = _combine_segments_for_prompt(segments, max_chars=max_chars)
    seg_count = len([s for s in segments if (s.get("text") or "").strip()])
    seg_count = seg_count or len(segments)

    target_sections = _default_target_sections(segments=segments, base_seconds=base_seconds)
    min_sections = max(5, target_sections - 1)
    max_sections = target_sections + 1

    extra_rapid = ""
    if (channel_id or "").upper() == "CH01":
        extra_rapid = (
            "\n"
            "- CRITICAL FOR CH01: steady pacing; prefer shorter cuts only when actions/thoughts clearly change.\n"
            "- Adjacent sections MUST vary camera/pose/angle/subject to avoid repetition.\n"
        )

    extra_slow = ""
    if (channel_id or "").upper() == "CH12":
        extra_slow = (
            "\n"
            "- CRITICAL FOR CH12: slower pacing; aim ~20–30s per image around the target average.\n"
            "- Avoid micro-cuts (<15s) unless there's a clear scene change.\n"
            "- Prefer one sustained scene over rapid cuts when the motif/setting continues.\n"
        )

    buddhist_narrator_channels = {"CH12", "CH13", "CH14", "CH15", "CH16", "CH17"}
    extra_buddhist = ""
    if (channel_id or "").upper() in buddhist_narrator_channels:
        extra_buddhist = (
            "\n"
            "- Visual motif (CH12-17): it is OK (often recommended) to depict a calm Japanese monk narrator (40–60s) or subtle Buddhist/temple elements even if not explicitly spelled out.\n"
            "- If a person appears, keep the same face/clothes/age across all sections for that character.\n"
            "- Still avoid adding extra unrelated characters; keep scenes simple and calm.\n"
            "- Do NOT default to sitting/meditation poses unless explicitly described; prefer standing/walking/handling simple props.\n"
        )

    style_block = f"\nChannel style hints:\n{style_hint}\n" if style_hint.strip() else ""

    monk_policy = (
        "- Do NOT invent extra characters.\n"
        "- Do NOT default to monks/meditation/正座/赤鉢巻/鎌おじさん unless the script explicitly demands it.\n"
    )
    if (channel_id or "").upper() in buddhist_narrator_channels:
        monk_policy = (
            "- Do NOT invent extra characters.\n"
            "- CH12-17 exception: a consistent monk narrator motif is allowed; avoid extra random people.\n"
        )

    prompt = f"""
You are preparing storyboard image cues for a narrated YouTube video.
Input is a Japanese SRT script with numbered segments like [index@start-end].

Split the script into between {min_sections} and {max_sections} visual sections.
Each section must:
- Cover consecutive SRT segments (no overlap, no gaps; the full script must be covered).
- Average around ~{base_seconds:.1f}s per image, but DO NOT be perfectly uniform; create pacing variation.
- Describe ONE clear visual idea the viewer should picture (concrete action/pose/setting/props/lighting).
- CRITICAL: Visual Focus must be faithful to the section content. If using metaphor/symbolism, it must be explicitly grounded in THIS section; do NOT default to generic cliché symbols.
- CRITICAL: Avoid repetition across sections. Do not reuse the same main subject/prop; make each `visual_focus` distinct (not just a trivial rephrase).
- Avoid putting text inside the scene.
{monk_policy.rstrip()}
{extra_rapid}
{extra_slow}
{extra_buddhist}
Return ONLY a JSON object (no markdown) with this schema:
{{"sections":[[start_segment,end_segment,summary,visual_focus,emotional_tone,persona_needed,role_tag,section_type,refined_prompt],...]}}

Field rules:
- start_segment/end_segment: 1-based inclusive indices from the markers.
- summary: <= 30 Japanese characters (short label).
- visual_focus: <= 14 English words, concrete camera-ready subject (must differ from adjacent).
- emotional_tone: <= 2 words.
- refined_prompt: <= 220 chars, English, camera-ready scene (action/pose + setting/props + lighting + camera angle/distance). Must be distinct across sections; avoid repetition; NO text in image.
- persona_needed: boolean; true ONLY if recurring characters must stay consistent.
- role_tag: one of explanation|story|dialogue|list_item|metaphor|quote|hook|cta|recap|transition|viewer_address
- section_type: one of story|dialogue|exposition|list|analysis|instruction|context|other
{style_block}
Script:
{story}
""".strip()

    router = get_router()
    # Token/cost guardrail: keep output cap proportional to requested section count.
    # Some providers (OpenRouter) may reject requests when max_tokens is too high for remaining credits.
    per_section = int(os.getenv("SRT2IMAGES_CUES_PLAN_TOKENS_PER_SECTION", "55"))
    base_cap = int(os.getenv("SRT2IMAGES_CUES_PLAN_BASE_TOKENS", "1200"))
    hard_cap = int(os.getenv("SRT2IMAGES_CUES_PLAN_MAX_TOKENS", "3200"))
    max_tokens = min(hard_cap, max(base_cap, per_section * max_sections))
    attempt_note = ""
    last_repeats: Dict[str, List[int]] = {}
    last_missing_refined: List[int] = []
    for attempt in range(2):
        prompt_run = prompt
        if attempt_note:
            prompt_run = f"{prompt}\n\n{attempt_note}".strip()

        content = router.call(
            task="visual_image_cues_plan",
            messages=[{"role": "user", "content": prompt_run}],
            response_format="json_object",
            temperature=0.3,
            max_tokens=max_tokens,
        )
        json_str = _extract_json_object(str(content or ""))
        if not json_str:
            raise ValueError("failed to extract JSON object from plan response")
        data = json.loads(json_str)

        sections = _coerce_sections(data, segment_count=len(segments))
        if len(sections) > max_sections:
            # Cap by merging adjacent sections with the weakest topic boundary.
            # This avoids "too many images" without resorting to equal spacing.
            texts = [str(s.get("text") or "").strip() for s in segments]
            tokens_per_seg = [set(_tokenize_loose(t)) for t in texts]

            while len(sections) > max_sections and len(sections) >= 2:
                best_i = 0
                best_score = float("inf")
                for i in range(len(sections) - 1):
                    boundary_idx = sections[i].end_segment - 1  # 1-based -> 0-based boundary
                    if boundary_idx < 0 or boundary_idx >= len(tokens_per_seg) - 1:
                        score = 0.0
                    else:
                        score = _boundary_score(idx=boundary_idx, tokens_per_seg=tokens_per_seg, texts=texts, window=3)
                    if score < best_score:
                        best_score = score
                        best_i = i

                a = sections[best_i]
                b = sections[best_i + 1]
                merged = PlannedSection(
                    start_segment=a.start_segment,
                    end_segment=b.end_segment,
                    summary=_truncate((a.summary or b.summary or ""), 60),
                    visual_focus=_truncate((a.visual_focus or b.visual_focus or ""), 180),
                    emotional_tone=_truncate((a.emotional_tone or b.emotional_tone or ""), 40),
                    refined_prompt=_truncate((a.refined_prompt or b.refined_prompt or ""), 420),
                    persona_needed=bool(a.persona_needed or b.persona_needed),
                    role_tag=_truncate((a.role_tag or b.role_tag or ""), 40),
                    section_type=_truncate((a.section_type or b.section_type or ""), 40),
                )
                sections = sections[:best_i] + [merged] + sections[best_i + 2 :]

        repeats = _find_duplicate_visual_focus(sections)
        missing_refined = [i for i, s in enumerate(sections, start=1) if not str(s.refined_prompt or "").strip()]
        if not repeats and not missing_refined:
            return sections

        last_repeats = repeats
        last_missing_refined = missing_refined
        logger.warning(
            "cues_plan: plan issues detected (attempt=%d, unique_repeats=%d, missing_refined=%d). Retrying once.",
            attempt + 1,
            len(repeats),
            len(missing_refined),
        )
        attempt_note = (
            "IMPORTANT: Fix your previous output.\n"
            "- `refined_prompt` is REQUIRED for every section (short English scene prompt; no text in image).\n"
            "- `visual_focus` must be distinct and faithful to THAT section.\n"
            "- Do NOT reuse the same main prop/symbol/location across sections.\n"
            "Return ONLY the full JSON object in the original schema."
        )

    raise RuntimeError(
        "visual_image_cues_plan output is not acceptable "
        f"(unique_repeats={len(last_repeats)}, missing_refined_prompt={len(last_missing_refined)}). "
        "Use THINK/AGENT mode and edit visual_cues_plan.json manually."
    )


_TRANSITION_PREFIXES: tuple[str, ...] = (
    "さて",
    "では",
    "次に",
    "そして",
    "さらに",
    "一方",
    "ところで",
    "しかし",
    "つまり",
    "結論",
    "まとめ",
    "最後に",
    "まず",
    "ここで",
    "ここから",
)

_LIST_MARKERS: tuple[str, ...] = (
    "一つ目",
    "二つ目",
    "三つ目",
    "四つ目",
    "五つ目",
    "第一",
    "第二",
    "第三",
    "ポイント",
    "方法",
    "理由",
)

_JA_STOPWORDS: set[str] = {
    "これ",
    "それ",
    "あれ",
    "ここ",
    "そこ",
    "あそこ",
    "そして",
    "しかし",
    "だから",
    "つまり",
    "また",
    "なので",
    "ため",
    "よう",
    "こと",
    "もの",
    "ところ",
    "とき",
    "時",
    "私",
    "あなた",
    "皆",
    "みな",
    "さん",
    "人",
    "自分",
    "感じ",
    "気",
    "的",
    "的な",
    "今日",
    "今",
    "いま",
    "何",
    "何度",
    "全部",
    "全て",
    "すべて",
    "など",
}

def _tokenize_loose(text: str) -> List[str]:
    """
    Lightweight tokenization that works reasonably for Japanese narration text without external NLP deps.
    - Extracts kanji/kana/latin runs.
    - Removes common particles/stopwords.
    - Keeps medium-length tokens to capture topic changes.
    """
    if not text:
        return []
    raw = str(text)
    # NOTE: Avoid hiragana-only tokens (mostly grammatical in narration); keep kanji/katakana/latin.
    toks = re.findall(r"[A-Za-z]{2,}|[一-龠]+|[ァ-ンー]{2,}", raw)
    out: List[str] = []
    for t in toks:
        t = t.strip()
        if not t:
            continue
        if t.isascii():
            t = t.lower()
        # filter digits-only / tiny kana
        if re.fullmatch(r"\d+", t):
            continue
        if t in _JA_STOPWORDS:
            continue
        # Filter out noisy single-character tokens unless they are useful drawable motifs.
        if len(t) == 1 and not t.isascii():
            if t not in {"心", "光", "闇", "夢", "道", "森", "海", "月", "星", "鍵", "扉", "鏡", "魂"}:
                continue
        out.append(t)
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def _boundary_score(
    *,
    idx: int,
    tokens_per_seg: List[set[str]],
    texts: List[str],
    window: int = 3,
) -> float:
    """
    Score a boundary between segment idx and idx+1.
    Higher means "better cut point" (topic/role transition).
    """
    n = len(tokens_per_seg)
    if idx < 0 or idx >= n - 1:
        return 0.0

    left_start = max(0, idx - window + 1)
    right_end = min(n, idx + 1 + window)
    left = set().union(*tokens_per_seg[left_start : idx + 1])
    right = set().union(*tokens_per_seg[idx + 1 : right_end])

    # topic shift
    shift = 1.0 - _jaccard(left, right)

    # rhetorical markers (prefer cutting right before a "now, next, conclusion" etc.)
    nxt = (texts[idx + 1] or "").strip()
    prv = (texts[idx] or "").strip()

    bonus = 0.0
    if nxt:
        for p in _TRANSITION_PREFIXES:
            if nxt.startswith(p):
                bonus += 0.35
                break
        for m in _LIST_MARKERS:
            if m in nxt[:12]:
                bonus += 0.25
                break
    if prv.endswith(("。", "！", "？")):
        bonus += 0.05

    return shift + bonus


def _classify_role_and_section(text: str) -> tuple[str, str]:
    raise RuntimeError(
        "Heuristic role/section classification is disabled by SSOT; use visual_image_cues_plan (router/THINK/AGENT) instead."
    )


def _infer_emotional_tone(tokens: List[str], text: str) -> str:
    raise RuntimeError(
        "Heuristic emotional_tone inference is disabled by SSOT; use visual_image_cues_plan (router/THINK/AGENT) instead."
    )


def _build_summary(tokens: List[str], text: str) -> str:
    """
    Build a short Japanese label (<=30 chars) from keywords.
    """
    raise RuntimeError(
        "Heuristic summary builder is disabled by SSOT; use visual_image_cues_plan (router/THINK/AGENT) instead."
    )


def _build_visual_focus(tokens: List[str], text: str, *, prev_focus: str = "") -> str:
    """
    Build a concrete visual focus line. Keep it drawable and avoid embedded text.
    """
    raise RuntimeError(
        "Heuristic visual_focus generation is disabled by SSOT; use visual_image_cues_plan (router/THINK/AGENT) instead."
    )


def plan_sections_heuristic(
    *,
    segments: List[Dict[str, Any]],
    base_seconds: float,
) -> List[PlannedSection]:
    """
    Non-LLM section planning for THINK MODE.

    Goals:
    - Variable number of sections based on duration (NOT fixed 10).
    - Context-based boundaries (topic/role transition scoring), not equal spacing.
    - Provide concrete visual_focus strings to drive prompt building.
    """
    raise RuntimeError(
        "plan_sections_heuristic is disabled by SSOT; use plan_sections_via_router (visual_image_cues_plan) instead."
    )
    if not segments:
        return []

    texts = [str(s.get("text") or "").strip() for s in segments]
    tokens_list = [_tokenize_loose(t) for t in texts]
    tokens_per_seg = [set(toks) for toks in tokens_list]

    # Target number of sections derived from duration.
    target = _default_target_sections(segments=segments, base_seconds=base_seconds)
    target = max(5, min(target, len(segments)))  # at least 5, at most 1 segment per section

    starts = [float(s.get("start") or 0.0) for s in segments]
    ends = [float(s.get("end") or 0.0) for s in segments]
    total_end = ends[-1] if ends else 0.0

    # Precompute boundary scores.
    b_scores = [
        _boundary_score(idx=i, tokens_per_seg=tokens_per_seg, texts=texts, window=3)
        for i in range(len(segments) - 1)
    ]

    sections: List[PlannedSection] = []
    start_idx = 0
    prev_focus = ""

    for sec_i in range(target):
        remaining_sections = target - sec_i
        # Last section: consume rest.
        if remaining_sections <= 1:
            end_idx = len(segments) - 1
        else:
            remaining_duration = max(0.0, total_end - starts[start_idx])
            ideal = remaining_duration / max(1, remaining_sections)
            # keep some pacing variation around base_seconds
            ideal = max(base_seconds * 0.6, min(base_seconds * 1.6, ideal))
            min_dur = max(8.0, ideal * 0.5)
            max_dur = max(min_dur, ideal * 1.8)

            earliest_t = starts[start_idx] + min_dur
            latest_t = starts[start_idx] + max_dur

            # Ensure we leave at least 1 segment for each remaining section.
            max_end_idx = len(segments) - remaining_sections
            max_end_idx = max(start_idx, max_end_idx)

            candidates: List[int] = []
            for j in range(start_idx, max_end_idx + 1):
                if ends[j] < earliest_t:
                    continue
                if ends[j] > latest_t:
                    break
                candidates.append(j)

            if not candidates:
                # Fallback: pick the nearest feasible end around ideal.
                desired_t = starts[start_idx] + ideal
                best_j = start_idx
                best_dt = float("inf")
                for j in range(start_idx, max_end_idx + 1):
                    dt = abs(ends[j] - desired_t)
                    if dt < best_dt:
                        best_dt = dt
                        best_j = j
                    if ends[j] > desired_t and dt > best_dt:
                        break
                end_idx = best_j
            else:
                # Choose candidate maximizing (boundary_score - duration_penalty)
                desired_t = starts[start_idx] + ideal
                best_obj = -1e9
                best_j = candidates[-1]
                for j in candidates:
                    dur = max(0.001, ends[j] - starts[start_idx])
                    penalty = abs(ends[j] - desired_t) / max(1e-6, ideal)
                    boundary = b_scores[j] if j < len(b_scores) else 0.0
                    obj = boundary * 1.4 - penalty
                    if obj > best_obj:
                        best_obj = obj
                        best_j = j
                end_idx = best_j

        slice_segs = segments[start_idx : end_idx + 1]
        slice_tokens = [t for seg_tokens in tokens_list[start_idx : end_idx + 1] for t in seg_tokens]
        text_joined = " ".join((texts[k] for k in range(start_idx, end_idx + 1) if texts[k])).strip()

        role_tag, section_type = _classify_role_and_section(text_joined)
        summary = _build_summary(slice_tokens, text_joined)
        focus = _build_visual_focus(slice_tokens, text_joined, prev_focus=prev_focus)
        tone = _infer_emotional_tone(slice_tokens, text_joined)

        sections.append(
            PlannedSection(
                start_segment=start_idx + 1,
                end_segment=end_idx + 1,
                summary=summary,
                visual_focus=focus,
                emotional_tone=tone,
                persona_needed=False,
                role_tag=role_tag,
                section_type=section_type,
            )
        )
        prev_focus = focus
        start_idx = end_idx + 1
        if start_idx >= len(segments):
            break

    # Normalize to full coverage with strict consecutiveness.
    if not sections:
        return []
    # Expand last section to end if needed.
    if sections[-1].end_segment < len(segments):
        last = sections[-1]
        sections[-1] = PlannedSection(
            start_segment=last.start_segment,
            end_segment=len(segments),
            summary=last.summary,
            visual_focus=last.visual_focus,
            emotional_tone=last.emotional_tone,
            persona_needed=last.persona_needed,
            role_tag=last.role_tag,
            section_type=last.section_type,
        )

    # Coerce through shared normalization/validation logic.
    return _coerce_sections(
        {"sections": [
            [
                s.start_segment,
                s.end_segment,
                s.summary,
                s.visual_focus,
                s.emotional_tone,
                s.persona_needed,
                s.role_tag,
                s.section_type,
            ]
            for s in sections
        ]},
        segment_count=len(segments),
    )


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
            "refined_prompt": sec.refined_prompt.strip(),
            "context_reason": "",
            "section_type": sec.section_type.strip(),
            "role_tag": sec.role_tag.strip(),
            "use_persona": bool(sec.persona_needed or (sec.section_type in ("story", "dialogue"))),
        }
        cue["start_frame"] = int(round(cue["start_sec"] * fps))
        cue["end_frame"] = int(round(cue["end_sec"] * fps))
        cue["duration_frames"] = max(1, cue["end_frame"] - cue["start_frame"])
        cues.append(cue)

    # CRITICAL: Ensure continuity (no gaps/overlaps) between cues.
    # CapCut transition injection expects consecutive segments (within ~20ms).
    for idx in range(len(cues) - 1):
        cur_start = float(cues[idx].get("start_sec") or 0.0)
        next_start = float(cues[idx + 1].get("start_sec") or 0.0)
        if next_start < cur_start:
            logger.warning(
                "cues_plan continuity skipped (next_start < cur_start): idx=%d cur_start=%.3f next_start=%.3f",
                idx,
                cur_start,
                next_start,
            )
            continue
        cues[idx]["end_sec"] = round(next_start, 3)
        cues[idx]["duration_sec"] = round(float(cues[idx]["end_sec"]) - float(cues[idx]["start_sec"]), 3)

    for cue in cues:
        cue["start_frame"] = int(round(float(cue["start_sec"]) * fps))
        cue["end_frame"] = int(round(float(cue["end_sec"]) * fps))
        cue["duration_frames"] = max(1, cue["end_frame"] - cue["start_frame"])
    return cues
