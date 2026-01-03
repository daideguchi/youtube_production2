from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List


_RE_URL = re.compile(r"https?://\S+")
_RE_BRACKETS = re.compile(r"(\([^)]*\)|\[[^\]]*\]|（[^）]*）|【[^】]*】)")
_RE_WS = re.compile(r"\s+")
_RE_SENT_SPLIT = re.compile(r"(?<=[。！？!?])")
_RE_ASCII_WORD = re.compile(r"[A-Za-z]+")
_RE_JP_ALLOWED = re.compile(r"[^0-9０-９ぁ-ゔゞァ-ヴー々〆〤一-龥、。 ]+")
_RE_MULTI_COMMA = re.compile(r"、{2,}")


def normalize_whitespace(text: str) -> str:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = t.replace("\n", " ")
    t = _RE_WS.sub(" ", t).strip()
    return t


def split_sentences_jp(text: str) -> List[str]:
    raw = text.replace("\r\n", "\n").replace("\r", "\n")
    # If there is no sentence-ending punctuation, fall back to non-empty lines.
    if not re.search(r"[。！？!?]", raw):
        lines = [ln.strip() for ln in raw.split("\n")]
        return [ln for ln in lines if ln]

    merged = normalize_whitespace(raw)
    parts = [p.strip() for p in _RE_SENT_SPLIT.split(merged)]
    out: List[str] = []
    for p in parts:
        if not p:
            continue
        out.append(p)
    return out


def make_scene_text(source_text: str, *, max_chars: int = 70) -> str:
    t = normalize_whitespace(source_text)
    t = _RE_URL.sub("", t)
    t = _RE_BRACKETS.sub("", t)
    t = normalize_whitespace(t)
    t = t.strip("「」『』\"' ")

    # Pre-sanitize so we don't truncate in the middle of ASCII fragments like "/ Husband ..."
    t = _sanitize_plain_japanese(t)

    # Drop sentence-ending punctuation (Vrewは最終段で句点制御する)
    t = re.sub(r"[。！？!?]+\s*$", "", t).strip()

    if max_chars > 0 and len(t) > max_chars:
        suffix = "など"
        budget = max(0, max_chars - len(suffix))
        if budget <= 0:
            return suffix

        cut = budget
        # Prefer cutting on natural boundaries.
        for sep in ("、", " "):
            pos = t.rfind(sep, 0, budget + 1)
            if pos >= int(budget * 0.5):
                cut = pos
                break
        t = t[:cut].rstrip(" 、") + suffix

    return t


def join_japanese_phrases(parts: Iterable[str]) -> str:
    out = ""
    for part in parts:
        p = (part or "").strip()
        if not p:
            continue
        if not out:
            out = p
            continue
        if out[-1] in "、，, 　" or p[0] in "、，, 　":
            out += p
        else:
            out += "、" + p
    return out


def _sanitize_plain_japanese(text: str) -> str:
    """
    Vrew向け: 日本語プレーンテキストを優先し、英字・装飾記号を除去/置換する。
    - 句点分割の都合で「。」は最終段で制御する（ここでは触りすぎない）
    """
    t = str(text or "")
    try:
        t = unicodedata.normalize("NFKC", t)
    except Exception:
        pass

    # Aspect ratios (common): 16:9 -> 16対9 (avoid ":" which we strip later)
    for a, b in (("16", "9"), ("9", "16"), ("1", "1"), ("4", "3"), ("3", "4"), ("21", "9")):
        t = re.sub(rf"(?<!\d){a}\s*[:：]\s*{b}(?!\d)", f"{a}対{b}", t)

    # Common English tokens -> Japanese
    # NOTE: \b is Unicode-word-boundary, so CJK text can break expected matches (e.g. "2Dデジタル").
    t = re.sub(r"(?i)(?<![A-Za-z0-9])AI(?![A-Za-z0-9])", "人工知能", t)
    t = re.sub(r"(?i)(?<![A-Za-z0-9])2D(?![A-Za-z0-9])", "二次元", t)
    t = re.sub(r"(?i)(?<![A-Za-z0-9])3D(?![A-Za-z0-9])", "三次元", t)
    t = re.sub(r"(?i)(?<![A-Za-z0-9])seinen(?![A-Za-z0-9])", "青年向け", t)

    # Punctuation normalization (avoid weird symbols)
    t = t.replace("，", "、").replace(",", "、")
    t = t.replace("．", "。").replace(".", "。")
    t = t.replace("！", "。").replace("!", "。").replace("？", "。").replace("?", "。")
    t = t.replace("・", "、")

    # Drop brackets/quotes/symbols; keep content where possible
    t = re.sub(r"[「」『』【】\[\]\(\)（）{}<>\"'`]", " ", t)
    # Keep phrase boundaries visible (vrew_source.srt often uses "/" separators)
    t = re.sub(r"[|/\\\\]", "、", t)
    t = re.sub(r"[:;：；]", " ", t)

    # Remove remaining ASCII words (after targeted replacements)
    t = _RE_ASCII_WORD.sub(" ", t)

    # Keep only JP-ish chars + punctuation/spaces
    t = _RE_JP_ALLOWED.sub(" ", t)

    t = normalize_whitespace(t)
    # Remove unnatural spaces inside Japanese words (e.g., "キッチ ン", "く れた")
    t = re.sub(r"([0-9０-９ぁ-ゔゞァ-ヴー々〆〤一-龥])\s+([0-9０-９ぁ-ゔゞァ-ヴー々〆〤一-龥])", r"\1\2", t)
    t = _RE_MULTI_COMMA.sub("、", t)
    t = t.strip(" 、")
    return t


def sanitize_prompt_for_vrew(prompt: str) -> str:
    """
    Vrew本文要件:
      - 1行=1プロンプト
      - 末尾は必ず「。」
      - 行中に「。」を含めない（末尾以外は「、」へ）
    """
    t = _sanitize_plain_japanese(prompt)
    # If sanitization removes all meaningful tokens (e.g. ASCII-only inputs),
    # fall back to the original prompt so we can still normalize punctuation.
    if (not t) or (not t.strip(" 、。")):
        t = normalize_whitespace(prompt)

    # Normalize end punctuation to "。"
    t = re.sub(r"[。！？!?]+\s*$", "。", t).strip()
    if not t.endswith("。"):
        t = t.rstrip("。") + "。"

    # Replace any internal "。" with "、" (keep last "。")
    inner = t[:-1].replace("。", "、")
    inner = re.sub(r"[！？!?]+", "、", inner)
    # Normalize comma spacing first, then collapse duplicates (spaces can hide duplicate commas).
    inner = re.sub(r"\s*、\s*", "、", inner).strip()
    inner = re.sub(r"、{2,}", "、", inner)

    t = inner + "。"

    # Final guard: ensure no internal "。"
    if "。" in t[:-1]:
        t = t[:-1].replace("。", "、") + "。"
    return t


def strip_banned_terms(text: str, banned_terms: List[str]) -> str:
    t = text
    for term in banned_terms or []:
        if not term:
            continue
        t = t.replace(term, "")
    return normalize_whitespace(t)


def validate_vrew_prompt_line(
    prompt: str, *, min_chars: int = 20, max_chars: int = 220, banned_terms: List[str] | None = None
) -> List[str]:
    errors: List[str] = []
    if not prompt.endswith("。"):
        errors.append("must_end_with_kuten")
    if "。" in prompt[:-1]:
        errors.append("internal_kuten_forbidden")
    if min_chars and len(prompt) < min_chars:
        errors.append("too_short")
    if max_chars and len(prompt) > max_chars:
        errors.append("too_long")
    for term in banned_terms or []:
        if term and term in prompt:
            errors.append(f"banned_term:{term}")
            break
    return errors
