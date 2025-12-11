from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple, Union

import yaml

from .reading_structs import RiskySpan, RubyToken


def _normalize(text: str) -> str:
    table = str.maketrans("０１２３４５６７８９．，", "0123456789.,")
    normalized = (text or "").translate(table)
    return re.sub(r"[\s、。・,，\.．]", "", normalized)

def _katakana_to_hiragana(text: str) -> str:
    table = {code: code - 0x60 for code in range(ord("ァ"), ord("ヴ") + 1)}
    return text.translate(table)


def normalize_for_compare(kana: str) -> str:
    """
    Normalize kana for comparing MeCab vs VOICEVOX:
    - Hiragana -> Katakana
    - Remove long vowel marks
    - Collapse typical VOICEVOX "audibility" variations (コオ/コウ, キョオ/キョウ, テエ/テイ, etc.)
    """
    if kana is None:
        return ""
    text = str(kana)
    text = text.translate({code: code + 0x60 for code in range(ord("ぁ"), ord("ゖ") + 1)})  # ひらがな→カタカナ
    text = text.replace("ー", "")
    replacements = [
        ("オオ", "オウ"),
        ("オー", "オウ"),
        ("コオ", "コウ"),
        ("ゴオ", "ゴウ"),
        ("ソオ", "ソウ"),
        ("ゾオ", "ゾウ"),
        ("トオ", "トウ"),
        ("ドオ", "ドウ"),
        ("ホオ", "ホウ"),
        ("モオ", "モウ"),
        ("ョオ", "ョウ"),
        ("テエ", "テイ"),
        ("デエ", "デイ"),
        ("キョオ", "キョウ"),
        ("ギョオ", "ギョウ"),
    ]
    for a, b in replacements:
        text = text.replace(a, b)
    return _normalize(text)


def _normalize_voicevox_kana(text: str) -> str:
    """Alias for VOICEVOX kana normalization used in auditor."""
    return normalize_for_compare(text)


def is_trivial_diff(expected: str, actual: str) -> bool:
    """Return True when the difference is cosmetic and should not hit the LLM."""

    if expected is None or actual is None:
        return True

    norm_expected = normalize_for_compare(str(expected))
    norm_actual = normalize_for_compare(str(actual))

    if norm_expected == norm_actual:
        return True

    # Single character delta or long sound mark fluctuation
    if abs(len(norm_expected) - len(norm_actual)) <= 1:
        diff_positions = [i for i, (a, b) in enumerate(zip(norm_expected, norm_actual)) if a != b]
        diff_chars = len(diff_positions)
        if diff_chars <= 1:
            # 先頭1文字差で長さが十分ある場合（例: ツライ/カライ）は非トリビアル扱い
            if diff_positions and diff_positions[0] == 0 and max(len(norm_expected), len(norm_actual)) >= 3:
                return False
            return True
    return False


def _is_numeric_surface(surface: str) -> bool:
    return bool(re.search(r"[0-9０-９]+", surface))


def load_hazard_terms(path: Path | None = None) -> Set[str]:
    """Load hazard entries from YAML. Returns a set of surfaces."""

    if path is None:
        path = Path(__file__).resolve().parents[1] / "data" / "hazard_readings.yaml"
    if not path.exists():
        return set()

    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        terms = []
        for item in payload.get("entries", []) or []:
            term = item.get("term")
            if isinstance(term, str) and term:
                terms.append(term)
        return set(terms)
    except Exception:
        return set()


def is_hazard_surface(surface: str, hazard_dict: Iterable[str]) -> bool:
    hazard_set = hazard_dict if isinstance(hazard_dict, set) else set(hazard_dict)
    return surface in hazard_set or _is_numeric_surface(surface) or bool(re.search(r"[A-Za-z]{2,}", surface))


def collect_risky_candidates(
    tokens: Sequence[Union[RubyToken, Dict[str, object]]],
    ruby_map: Dict[int, str],
    hazard_dict: Iterable[str],
    reading_dict: Iterable[str],
) -> List[RiskySpan]:
    risky: List[RiskySpan] = []
    dict_surfaces = set(reading_dict)

    for idx, raw_tok in enumerate(tokens):
        tok = raw_tok if isinstance(raw_tok, RubyToken) else RubyToken.from_dict(raw_tok)
        if tok.surface in dict_surfaces:
            continue

        mecab_reading = tok.reading_hira or tok.surface
        ruby_reading = ruby_map.get(idx, mecab_reading)

        if is_hazard_surface(tok.surface, hazard_dict):
            risky.append(
                RiskySpan(
                    line_id=tok.line_id,
                    token_index=tok.token_index,
                    risk_score=1.0,
                    reason=f"hazard:{tok.surface}",
                    surface=tok.surface,
                )
            )
            continue

        if is_trivial_diff(mecab_reading, ruby_reading):
            continue
    return risky


def group_risky_terms(
    risky_spans: Sequence[RiskySpan], max_examples: int = 3
) -> Dict[Tuple[str, str], List[int]]:
    grouped: Dict[Tuple[str, str], List[int]] = defaultdict(list)
    for span in risky_spans:
        key = (str(span.surface or span.reason), str(span.reason))
        if len(grouped[key]) < max_examples:
            grouped[key].append(span.token_index)
    return grouped
