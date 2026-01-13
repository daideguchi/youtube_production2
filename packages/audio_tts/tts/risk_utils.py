from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple, Union

# yaml is optional; hazard terms are best-effort when missing.
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

from .reading_structs import RiskySpan, RubyToken

from factory_common.paths import audio_pkg_root, repo_root


def _normalize(text: str) -> str:
    table = str.maketrans("０１２３４５６７８９．，", "0123456789.,")
    normalized = (text or "").translate(table)
    # VOICEVOX kana contains accent markers and separators like: "' / _"
    # Strip them so we can compare to MeCab readings (plain kana).
    normalized = normalized.replace("'", "").replace("’", "").replace("/", "").replace("_", "")
    # Treat mid-dot as punctuation for reading comparisons.
    normalized = normalized.replace("・", "")
    # Keep only kana + ASCII alnum (drop punctuation, quotes, brackets, etc.).
    return re.sub(r"[^0-9A-Za-z\u3040-\u309f\u30a0-\u30ff]", "", normalized)

def _katakana_to_hiragana(text: str) -> str:
    table = {code: code - 0x60 for code in range(ord("ァ"), ord("ヴ") + 1)}
    return text.translate(table)

_VOWEL_A = set("アカサタナハマヤラワガザダバパァャヮ")
_VOWEL_I = set("イキシチニヒミリギジヂビピィ")
_VOWEL_U = set("ウクスツヌフムユルグズヅブプゥュヴ")
_VOWEL_E = set("エケセテネヘメレゲゼデベペェ")
_VOWEL_O = set("オコソトノホモヨロヲゴゾドボポォョ")

def _collapse_long_vowels(text: str) -> str:
    """
    Collapse long vowels for safe comparisons.

    Goal:
    - Treat length differences as cosmetic (e.g., コーヒー vs コヒー vs コオヒイ).
    - Avoid false positives in fail-fast mismatch detection.

    Strategy:
    - Drop the Katakana long-vowel mark "ー".
    - Drop vowel-extension kana that follow a kana with the same vowel class:
      - A-class + ア
      - I-class + イ
      - U-class + ウ
      - E-class + (エ|イ)  # エー / エイ
      - O-class + (オ|ウ)  # オー / オウ
    """
    if not text:
        return text
    out: list[str] = []
    prev: str | None = None
    for ch in str(text):
        if ch == "ー":
            continue
        if prev:
            if prev in _VOWEL_A and ch == "ア":
                continue
            if prev in _VOWEL_I and ch == "イ":
                continue
            if prev in _VOWEL_U and ch == "ウ":
                continue
            if prev in _VOWEL_E and ch in {"エ", "イ"}:
                continue
            if prev in _VOWEL_O and ch in {"オ", "ウ"}:
                continue
        out.append(ch)
        prev = ch
    return "".join(out)

def normalize_for_compare(kana: str) -> str:
    """
    Normalize kana for comparing MeCab vs VOICEVOX:
    - Hiragana -> Katakana
    - Strip VOICEVOX markers/punctuation
    - Collapse long vowels (ー / vowel repeats / オウ / エイ)
    - Unify orthographic variants (ヅ/ズ, ヂ/ジ, ヲ/オ)
    """
    if kana is None:
        return ""
    text = str(kana)
    text = text.translate({code: code + 0x60 for code in range(ord("ぁ"), ord("ゖ") + 1)})  # ひらがな→カタカナ
    text = _normalize(text)
    # VOICEVOX kana sometimes expands yōon (拗音) in longer phrases:
    #   ギョウ -> ギヨウ, キョク -> キヨク, etc.
    # For comparison only, normalize: I-row kana + ヨ + <katakana> -> I-row kana + smallョ + same char.
    # This reduces false mismatches without rewriting the script itself.
    text = re.sub(r"([キギシジチニヒミリピビ])ヨ([ァ-ヺー])", r"\1ョ\2", text)
    # Orthographic variants that should not trigger audits
    # (e.g., ヅ/ズ, ヂ/ジ, ヲ/オ are effectively the same in modern pronunciation).
    text = text.replace("ヅ", "ズ").replace("ヂ", "ジ").replace("ヲ", "オ")
    # Foreign V-sounds: VOICEVOX often collapses ヴ系 -> バ/ビ/ベ/ボ.
    text = text.replace("ヴァ", "バ").replace("ヴィ", "ビ").replace("ヴェ", "ベ").replace("ヴォ", "ボ").replace("ヴ", "ブ")
    # NOTE: Particle pronunciation (は=ワ / へ=エ) should be handled at token-level.
    # Doing a blind string replace here breaks real words (e.g., 一発=イチハツ).
    text = _collapse_long_vowels(text)
    # Colloquial contraction normalization:
    # - MeCab often yields: コウイウ / ソウイウ / ドウイウ
    # - VOICEVOX often yields: コーユー / ソーユー / ドーユー (or similar)
    # After long-vowel collapsing, these become: コイウ / ソイウ / ドイウ vs コユ / ソユ / ドユ.
    text = text.replace("コイウ", "コユ").replace("ソイウ", "ソユ").replace("ドイウ", "ドユ")
    return text


def _normalize_voicevox_kana(text: str) -> str:
    """Alias for VOICEVOX kana normalization used in auditor."""
    return normalize_for_compare(text)


def is_trivial_diff(expected: str, actual: str) -> bool:
    """Return True when the difference is cosmetic and should not hit the LLM."""

    if expected is None or actual is None:
        return True

    norm_expected = normalize_for_compare(str(expected))
    norm_actual = normalize_for_compare(str(actual))

    return norm_expected == norm_actual


def _is_numeric_surface(surface: str) -> bool:
    return bool(re.search(r"[0-9０-９]+", surface))


def load_hazard_terms(path: Path | None = None) -> Set[str]:
    """Load hazard entries from YAML. Returns a set of surfaces."""

    if path is None:
        candidates = [
            repo_root() / "data" / "hazard_readings.yaml",
            audio_pkg_root() / "data" / "hazard_readings.yaml",
        ]
        path = next((p for p in candidates if p.exists()), candidates[0])
    if not path.exists():
        return set()
    if yaml is None:
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
