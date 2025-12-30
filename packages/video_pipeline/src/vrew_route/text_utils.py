from __future__ import annotations

import re
from typing import Iterable, List


_RE_URL = re.compile(r"https?://\S+")
_RE_BRACKETS = re.compile(r"(\([^)]*\)|\[[^\]]*\]|（[^）]*）|【[^】]*】)")
_RE_WS = re.compile(r"\s+")
_RE_SENT_SPLIT = re.compile(r"(?<=[。！？!?])")


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
    t = re.sub(r"[。！？!?]+\s*$", "", t).strip()
    if max_chars > 0 and len(t) > max_chars:
        t = t[: max(0, max_chars - 1)].rstrip() + "…"
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


def sanitize_prompt_for_vrew(prompt: str) -> str:
    """
    Vrew本文要件:
      - 1行=1プロンプト
      - 末尾は必ず「。」
      - 行中に「。」を含めない（末尾以外は「、」へ）
    """
    t = normalize_whitespace(prompt)

    # Normalize end punctuation to "。"
    t = re.sub(r"[。！？!?]+\s*$", "。", t).strip()
    if not t.endswith("。"):
        t = t.rstrip("。") + "。"

    # Replace any internal "。" with "、" (keep last "。")
    inner = t[:-1].replace("。", "、")
    inner = re.sub(r"[！？!?]+", "、", inner)
    inner = re.sub(r"、{2,}", "、", inner)
    inner = re.sub(r"\s*、\s*", "、", inner).strip()

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
