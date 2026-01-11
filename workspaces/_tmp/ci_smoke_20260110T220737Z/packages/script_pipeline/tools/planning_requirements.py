#!/usr/bin/env python3
"""Shared helpers for planning.csv field requirements and persona text."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

from script_pipeline.tools.optional_fields_registry import FIELD_KEYS

from factory_common.paths import planning_root, repo_root

YTM_ROOT = repo_root()

SSOT_DIR = planning_root() / "personas"


@dataclass(frozen=True)
class ChannelFieldRequirement:
    min_no: int
    required_keys: List[str]


TAG_REQUIREMENT_KEYS = [
    "primary_pain_tag",
    "secondary_pain_tag",
    "life_scene",
    "key_concept",
    "benefit_blurb",
    "analogy_image",
    "description_lead",
    "description_takeaways",
]

# 必須フィールドの閾値設定（channel_code -> min_no, keys）
CHANNEL_FIELD_REQUIREMENTS: Dict[str, ChannelFieldRequirement] = {
    "CH01": ChannelFieldRequirement(min_no=191, required_keys=TAG_REQUIREMENT_KEYS),
    "CH02": ChannelFieldRequirement(min_no=101, required_keys=TAG_REQUIREMENT_KEYS),
    "CH03": ChannelFieldRequirement(min_no=101, required_keys=TAG_REQUIREMENT_KEYS),
    "CH04": ChannelFieldRequirement(min_no=101, required_keys=TAG_REQUIREMENT_KEYS),
    "CH05": ChannelFieldRequirement(min_no=101, required_keys=TAG_REQUIREMENT_KEYS),
    "CH06": ChannelFieldRequirement(min_no=101, required_keys=TAG_REQUIREMENT_KEYS),
}

# 説明文のデフォルト値
CHANNEL_DESCRIPTION_DEFAULTS: Dict[str, Dict[str, str]] = {
    "CH01": {
        "description_lead": "優しさを利用されがちなあなたへ──慈悲と境界線のお話。",
        "description_takeaways": "・慈悲と甘やかしの違い\n・距離を置く言い換え3つ",
    },
    "CH02": {
        "description_lead": "考えすぎて眠れない夜に、心を整える哲学の視点を届けます。",
        "description_takeaways": "・怒りが湧いた瞬間の対処法\n・感情を言語化する3ステップ",
    },
    "CH03": {
        "description_lead": "病院に行くほどではない不調を、今日の生活習慣で整えましょう。",
        "description_takeaways": "・膝を守る座り方\n・血流を促す足指ケア",
    },
    "CH04": {
        "description_lead": "日常の裏側で進む心理実験とアーカイブの話を覗きに行きましょう。",
        "description_takeaways": "・XX実験の真相\n・今日から使える心理トリック",
    },
    "CH05": {
        "description_lead": "大人の恋をもう一度楽しむための小さな勇気を届けます。",
        "description_takeaways": "・第二の恋に踏み出すステップ\n・家族へ伝える言葉",
    },
    "CH06": {
        "description_lead": "眠れない夜に、封印された噂の裏側を覗きませんか？",
        "description_takeaways": "・事件の年表\n・真相仮説と根拠",
    },
}


def _normalize_channel(code: str) -> str:
    return (code or "").strip().upper()


def resolve_required_field_keys(channel_code: str, video_number: Optional[int]) -> List[str]:
    requirement = CHANNEL_FIELD_REQUIREMENTS.get(_normalize_channel(channel_code))
    if not requirement:
        return []
    if video_number is None or video_number < requirement.min_no:
        return []
    return list(requirement.required_keys)


def resolve_required_columns(channel_code: str, video_number: Optional[int]) -> List[str]:
    columns: List[str] = []
    for key in resolve_required_field_keys(channel_code, video_number):
        column = FIELD_KEYS.get(key)
        if column:
            columns.append(column)
    return columns


def get_description_defaults(channel_code: str) -> Dict[str, str]:
    default = CHANNEL_DESCRIPTION_DEFAULTS.get(_normalize_channel(channel_code), {})
    return dict(default)


def get_channel_requirement_specs(channel_code: str) -> List[Dict[str, object]]:
    normalized = _normalize_channel(channel_code)
    requirement = CHANNEL_FIELD_REQUIREMENTS.get(normalized)
    if not requirement:
        return []
    columns = [
        FIELD_KEYS.get(key, key)
        for key in requirement.required_keys
        if FIELD_KEYS.get(key, key)
    ]
    return [
        {
            "min_no": requirement.min_no,
            "required_keys": list(requirement.required_keys),
            "required_columns": columns,
        }
    ]


def get_planning_template_info(channel_code: str) -> Dict[str, object]:
    normalized = _normalize_channel(channel_code)
    if not normalized:
        return {}
    template_path = planning_root() / "templates" / f"{normalized}_planning_template.csv"
    if not template_path.exists():
        return {}
    headers: List[str] = []
    sample: List[str] = []
    try:
        with template_path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.reader(handle)
            try:
                headers = next(reader)
            except StopIteration:
                headers = []
            try:
                sample = next(reader)
            except StopIteration:
                sample = []
    except FileNotFoundError:
        return {}
    try:
        relative_path = str(template_path.relative_to(YTM_ROOT))
    except ValueError:
        relative_path = str(template_path)
    return {
        "path": relative_path,
        "headers": headers,
        "sample": sample,
    }


@lru_cache(maxsize=32)
def get_channel_persona(channel_code: str) -> Optional[str]:
    normalized = _normalize_channel(channel_code)
    if not normalized:
        return None
    persona_path = SSOT_DIR / f"{normalized}_PERSONA.md"
    if not persona_path.exists():
        return None
    try:
        for line in persona_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("> "):
                value = stripped[2:].strip()
                if value:
                    return value
                break
    except FileNotFoundError:
        return None
    return None


def get_persona_doc_path(channel_code: str) -> Optional[str]:
    normalized = _normalize_channel(channel_code)
    if not normalized:
        return None
    persona_path = SSOT_DIR / f"{normalized}_PERSONA.md"
    if not persona_path.exists():
        return None
    try:
        return str(persona_path.relative_to(YTM_ROOT))
    except ValueError:
        return str(persona_path)


def clear_persona_cache() -> None:
    get_channel_persona.cache_clear()
