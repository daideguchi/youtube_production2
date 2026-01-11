#!/usr/bin/env python3
"""Utility for tracking planning CSV optional fields."""

from __future__ import annotations

from typing import Dict

# Column (Japanese) -> field key (snake_case)
OPTIONAL_FIELDS = {
    "サムネタイトル上": "thumbnail_upper",
    "サムネタイトル下": "thumbnail_lower",
    "サムネ画像プロンプト（URL・テキスト指示込み）": "thumbnail_prompt",
    "企画意図": "concept_intent",
    "史実エピソード候補": "historical_episodes",
    "内容": "content_notes",
    "内容（企画要約）": "content_summary",
    "ターゲット層": "target_audience",
    "具体的な内容（話の構成案）": "outline_notes",
    "DALL-Eプロンプト（URL・テキスト指示込み）": "dalle_prompt",
    "サムネタイトル": "thumbnail_title",
    "悩みタグ_メイン": "primary_pain_tag",
    "悩みタグ_サブ": "secondary_pain_tag",
    "ライフシーン": "life_scene",
    "キーコンセプト": "key_concept",
    "ベネフィット一言": "benefit_blurb",
    "たとえ話イメージ": "analogy_image",
    "説明文_リード": "description_lead",
    "説明文_この動画でわかること": "description_takeaways",
}

# field key (snake_case) -> column (Japanese)
FIELD_KEYS = {value: column for column, value in OPTIONAL_FIELDS.items()}


def get_planning_section(metadata: Dict[str, object]) -> Dict[str, str]:
    planning = metadata.get("planning")
    if not isinstance(planning, dict):
        planning = {}
        metadata["planning"] = planning
    return planning


def update_planning_from_row(planning: Dict[str, str], row: Dict[str, str]) -> None:
    for column_name, key in OPTIONAL_FIELDS.items():
        value = row.get(column_name)
        if value:
            planning[key] = value
