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
# Common prompt/meta leakage lines that must never appear in spoken scripts.
_RE_META_INSTRUCTION_LINE = re.compile(
    r"^\s*(?:では|それでは|ここから)[、,]?\s*(?:エンディング|結び|締め)\s*(?:を)?\s*(?:作成|生成)(?:していきます|します)[。．]?\s*$",
    flags=re.MULTILINE,
)
# Trailing “teaser” lines that indicate the script got cut right before the closing.
# These are removed ONLY when they appear at the end (to avoid harming normal mid-script transitions).
_RE_TRAILING_TEASER_LINE = re.compile(
    r"^\s*(?:"
    r"(?:最後に)[、,]?\s*.*?(?:結び|締め|締めくくり).*?(?:お届け|お伝え|紹介)(?:し(?:ます|ましょう)|ましょう)?"
    r"|(?:さて)[、,]?\s*.*?(?:終わりに近づ|終わりが近づ).*?"
    r"|(?:では|それでは)[、,]?\s*.*?(?:見ていきましょう|話していきましょう|確認していきましょう|結んでいきましょう)"
    r")\s*[。．]?\s*$"
)

# Script-outline / planning leakage (must never appear in spoken scripts).
_RE_EPISODE_HEADER_LINE = re.compile(r"^\s*CH\d{2}-\d{3}\s*[:：]\s*\S+")
_META_BLOCK_HEADERS = {
    "設定",
    "CSVデータ",
    "詳細構成",
    "構成案",
    "プロット",
}
_RE_META_BLOCK_KV = re.compile(r"^\s*[\w\u3040-\u30ff\u4e00-\u9fff]{1,20}\s*[:：]\s*\S+")
_RE_LENGTH_META = re.compile(r"(?:[-—−]\s*)?約\s*\d{2,5}\s*字\b")
_RE_LETTERED_LIST_PREFIX = re.compile(r"^\s*[A-Za-z]\s*[.)）:、]\s+")
_RE_MD_BOLD = re.compile(r"\*\*(.+?)\*\*")


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
    out, _ = _subn(_RE_META_INSTRUCTION_LINE, "", out, "meta_instruction_line")
    out, _ = _subn(_RE_MD_BOLD, r"\1", out, "md_bold")

    # Line cleanup (keep structure, but avoid messy artifacts)
    lines = []
    for line in out.split("\n"):
        cleaned = re.sub(r"[ \t]{2,}", " ", line).rstrip()
        cleaned = re.sub(r" +([。．、，,！!？?])", r"\1", cleaned)
        cleaned = re.sub(r"([（(]) +", r"\1", cleaned)
        cleaned = re.sub(r" +([）)])", r"\1", cleaned)

        # Remove outline-ish metadata that commonly leaks into A-text.
        cleaned2, n = _RE_LENGTH_META.subn("", cleaned)
        if n:
            removed["length_meta"] = removed.get("length_meta", 0) + n
        cleaned = cleaned2

        cleaned2, n = _RE_LETTERED_LIST_PREFIX.subn("", cleaned, count=1)
        if n:
            removed["lettered_list_prefix"] = removed.get("lettered_list_prefix", 0) + n
            # If the remaining label looks like a bare heading, add a sentence-ending mark
            # to keep TTS from reading it like a fragment.
            tail = cleaned2.rstrip()
            if tail and tail[-1] not in "。．.!！?？」「』）)":
                cleaned2 = tail + "。"
        cleaned = cleaned2

        lines.append(cleaned)

    # Drop an episode header line if it appears at the start (common outline export leakage).
    header_removed = 0
    for i, line in enumerate(lines):
        if not line.strip() or line.strip() == "---":
            continue
        if _RE_EPISODE_HEADER_LINE.match(line):
            lines.pop(i)
            header_removed = 1
        break
    if header_removed:
        removed["episode_header_line"] = removed.get("episode_header_line", 0) + header_removed

    # Remove structured meta blocks like "設定" / "CSVデータ" sections.
    kept: list[str] = []
    i = 0
    removed_meta_lines = 0
    removed_meta_headers = 0
    while i < len(lines):
        stripped = (lines[i] or "").strip()
        if stripped in _META_BLOCK_HEADERS:
            removed_meta_headers += 1
            i += 1
            # Skip following key-value-ish lines until a blank line or a pause separator.
            while i < len(lines):
                s = (lines[i] or "").strip()
                if s == "---":
                    break
                if not s:
                    # consume the blank line that terminates the meta block
                    i += 1
                    break
                if _RE_META_BLOCK_KV.match(lines[i]) or _RE_EPISODE_HEADER_LINE.match(lines[i]):
                    removed_meta_lines += 1
                    i += 1
                    continue
                # If it doesn't look like a KV line, stop (avoid deleting real prose).
                break
            continue
        kept.append(lines[i])
        i += 1
    if removed_meta_headers:
        removed["meta_block_header"] = removed.get("meta_block_header", 0) + removed_meta_headers
    if removed_meta_lines:
        removed["meta_block_lines"] = removed.get("meta_block_lines", 0) + removed_meta_lines
    lines = kept

    # Remove trailing teaser lines (only at the end; keep internal occurrences).
    teaser_removed = 0
    while True:
        # Find last “core” line (ignore trailing blanks and pause separators).
        i = len(lines) - 1
        while i >= 0 and (not lines[i].strip() or lines[i].strip() == "---"):
            i -= 1
        if i < 0:
            break
        if _RE_TRAILING_TEASER_LINE.match(lines[i] or ""):
            teaser_removed += 1
            lines.pop(i)
            continue
        break
    if teaser_removed:
        removed["trailing_teaser_line"] = removed.get("trailing_teaser_line", 0) + teaser_removed

    out = "\n".join(lines)
    out = re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"

    return MetaSanitizeResult(text=out, removed_counts=removed)
