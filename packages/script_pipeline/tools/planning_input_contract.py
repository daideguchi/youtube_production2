from __future__ import annotations

"""
Planning input contract (title-anchored) enforcement helpers.

Goal:
- Prevent "context contamination" from Planning CSV rows (e.g. content_summary from another episode)
  from misleading A-text generation/validation.

This module is deterministic and channel-agnostic.
"""

import re
import unicodedata
from typing import Any, Dict, Tuple, List


_BRACKET_TAG_RE = re.compile(r"【([^】]+)】")

# Planning fields that can strongly steer the script theme.
# If the row is likely contaminated, we drop these so the pipeline falls back to:
#   title (absolute truth) + SSOT patterns/persona/channel_prompt
#
# NOTE:
# - We intentionally keep `concept_intent` and `outline_notes` even when other fields look contaminated.
#   Those are often curated and still useful for staying on-topic, while "tagged summaries/tags" are
#   the most common contamination source.
_DROP_ON_THEME_MISALIGN: tuple[str, ...] = (
    "historical_episodes",
    "content_summary",
    "content_notes",
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


def normalize_bracket_tag(tag: str | None) -> str:
    """Normalize tags for "format-only" variations (e.g., ニコラ・テスラ vs ニコラテスラ)."""
    s = unicodedata.normalize("NFKC", str(tag or "")).strip()
    # Remove whitespace and common separators/punctuation that often fluctuate in planning tags.
    s = re.sub(r"[\s\u3000・･·、,\.／/\\\-‐‑‒–—―ー〜~]", "", s)
    return s


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
    title_tag_norm = normalize_bracket_tag(title_tag)
    summary_tag_norm = normalize_bracket_tag(summary_tag)

    coherence = "no_tags"
    drop_theme_hints = False
    dropped: List[str] = []
    tag_format_variation = False

    if title_tag and summary_tag:
        if title_tag_norm and title_tag_norm == summary_tag_norm:
            coherence = "ok"
            tag_format_variation = title_tag != summary_tag
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
        # so we conservatively drop theme-bearing planning hints.
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
        "title_tag_normalized": title_tag_norm,
        "content_summary_tag_normalized": summary_tag_norm,
        "drop_theme_hints": drop_theme_hints,
    }
    if tag_format_variation:
        integrity["tag_format_variation"] = True
    if dropped:
        integrity["dropped_planning_keys"] = dropped
    return out, integrity
