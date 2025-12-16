from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class MetaSanitizeResult:
    text: str
    removed_counts: Dict[str, int]


_RE_MD_REF_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", flags=re.MULTILINE)
# e.g. ([戦国ヒストリー][13]) / （[戦国ヒストリー][13]）
_RE_MD_REF_PAREN = re.compile(r"[\(（]\s*\[[^\]]+\]\[\d+\]\s*[\)）]")
# e.g. [戦国ヒストリー][13]
_RE_MD_REF = re.compile(r"\[[^\]]+\]\[\d+\]")
# e.g. [13] (NOTE: keep single-digit [1] etc to avoid clobbering pause tags like [1])
_RE_NUM_FOOTNOTE = re.compile(r"\[(\d{2,})\]")

_RE_URL = re.compile(r"https?://[^\s)）\]】」』<>]+")
_RE_WWW = re.compile(r"\bwww\.[^\s)）\]】」』<>]+")
_RE_EMPTY_PARENS = re.compile(r"[\(（]\s*[\)）]")


def strip_meta_from_script(text: str) -> MetaSanitizeResult:
    """
    Remove “meta” tokens that must never appear in spoken A-text / subtitles.

    Targets:
      - Markdown reference definitions: [id]: https://...
      - Markdown reference citations: ([label][13]) / [label][13]
      - Bare numeric footnotes: [13] (2+ digits only)
      - URLs: https://... / www....
      - Empty parentheses left after removal: () / （）

    Keeps:
      - Pause tags like [0.5] or [1] (single-digit) used by some TTS flows.
    """
    src = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    removed: Dict[str, int] = {}

    def _subn(pattern: re.Pattern[str], repl: str, s: str, key: str) -> Tuple[str, int]:
        out, n = pattern.subn(repl, s)
        if n:
            removed[key] = removed.get(key, 0) + n
        return out, n

    out = src
    out, _ = _subn(_RE_MD_REF_DEF, "", out, "md_ref_def")
    out, _ = _subn(_RE_MD_REF_PAREN, "", out, "md_ref_paren")
    out, _ = _subn(_RE_MD_REF, "", out, "md_ref")
    out, _ = _subn(_RE_NUM_FOOTNOTE, "", out, "numeric_footnote")
    out, _ = _subn(_RE_URL, "", out, "url")
    out, _ = _subn(_RE_WWW, "", out, "www")
    out, _ = _subn(_RE_EMPTY_PARENS, "", out, "empty_parens")

    # Line cleanup (keep structure, but avoid messy artifacts)
    lines = []
    for line in out.split("\n"):
        cleaned = re.sub(r"[ \t]{2,}", " ", line).rstrip()
        cleaned = re.sub(r" +([。．、，,！!？?])", r"\1", cleaned)
        cleaned = re.sub(r"([（(]) +", r"\1", cleaned)
        cleaned = re.sub(r" +([）)])", r"\1", cleaned)
        lines.append(cleaned)
    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"

    return MetaSanitizeResult(text=out, removed_counts=removed)

