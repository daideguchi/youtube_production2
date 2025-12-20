from __future__ import annotations

"""
Planning input contract (L1/L2/L3) enforcement helpers.

Goal:
- Prevent "context contamination" from Planning CSV rows (e.g. content_summary from another episode)
  from misleading A-text generation/validation.

This module is deterministic and channel-agnostic.
"""

import re
from typing import Any, Dict, Tuple, List


_BRACKET_TAG_RE = re.compile(r"【([^】]+)】")

# Planning (L2) fields that can strongly steer the script theme.
# If the row is likely contaminated, we drop these so the pipeline falls back to
# L1 (title) + SSOT patterns/persona/channel_prompt.
_DROP_ON_THEME_MISALIGN: tuple[str, ...] = (
    "concept_intent",
    "content_summary",
    "content_notes",
    "outline_notes",
    "primary_pain_tag",
    "secondary_pain_tag",
    "life_scene",
    "key_concept",
    "benefit_blurb",
    "analogy_image",
    "description_lead",
    "description_takeaways",
)


def extract_bracket_tag(text: str | None) -> str:
    raw = str(text or "")
    m = _BRACKET_TAG_RE.search(raw)
    return (m.group(1) or "").strip() if m else ""


def apply_planning_input_contract(*, title: str, planning: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Apply the planning input contract and return:
      (sanitized_planning_dict, planning_integrity_dict)

    planning_integrity is intentionally stable (no timestamps) so it does not cause
    status.json to churn on every ensure_status() call.
    """
    out: Dict[str, Any] = dict(planning) if isinstance(planning, dict) else {}
    title_tag = extract_bracket_tag(title)
    summary_tag = extract_bracket_tag(str(out.get("content_summary") or ""))

    coherence = "no_tags"
    drop_theme_hints = False
    dropped: List[str] = []

    if title_tag and summary_tag:
        if title_tag == summary_tag:
            coherence = "ok"
        else:
            coherence = "tag_mismatch"
            drop_theme_hints = True
            for key in _DROP_ON_THEME_MISALIGN:
                if key in out:
                    out.pop(key, None)
                    dropped.append(key)
    elif title_tag and not summary_tag:
        coherence = "no_content_summary_tag"
    elif summary_tag and not title_tag:
        coherence = "no_title_tag"
        # No title tag means we cannot sanity-check planning tags. In practice, these rows
        # are more likely to be "mixed" (e.g., content_summary belongs to another episode),
        # so we conservatively drop theme-bearing L2 hints.
        drop_theme_hints = True
        for key in _DROP_ON_THEME_MISALIGN:
            if key in out:
                out.pop(key, None)
                dropped.append(key)

    integrity: Dict[str, Any] = {
        "schema": "ytm.planning_integrity.v1",
        "coherence": coherence,
        "title_tag": title_tag,
        "content_summary_tag": summary_tag,
        "drop_theme_hints": drop_theme_hints,
    }
    if dropped:
        integrity["dropped_planning_keys"] = dropped
    return out, integrity
