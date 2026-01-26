"""Validate required outputs for stages in the new script_pipeline."""
from __future__ import annotations

import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Dict, List, Any, Tuple

from factory_common.paths import audio_final_dir, script_data_root

from .sot import load_status
from .tools.channel_registry import find_channel_dir

DATA_ROOT = script_data_root()


_RE_URL = re.compile(r"https?://|\bwww\.", flags=re.IGNORECASE)
_RE_MD_REF_DEF = re.compile(r"^\s*\[[^\]]+\]:\s*\S+.*$", flags=re.MULTILINE)
_RE_MD_REF = re.compile(r"\[[^\]]+\]\[\d+\]")
_RE_NUM_FOOTNOTE = re.compile(r"\[(\d{2,})\]")
_RE_BULLET_LINE = re.compile(r"^\s*(?:[-*•]|・)\s+")
_RE_NUMBERED_LINE = re.compile(r"^\s*\d+\s*[.)）:、]\s+")
_RE_LETTERED_LINE = re.compile(r"^\s*[A-Za-z]\s*[.)）:、]\s+")
_RE_MD_HEADING = re.compile(r"^\s*#{1,6}\s+\S")
_RE_BAD_SEPARATOR = re.compile(r"^\s*(?:\*{3,}|_{3,}|/{3,}|={3,}|-{4,})\s*$")
_RE_TEMPLATE_TOKEN = re.compile(r"<<[A-Z0-9_]{2,}>>")
_RE_PERCENT_OR_PERCENT_WORD = re.compile(r"[%％]|パーセント")
_RE_LENGTH_META = re.compile(r"約\s*\d{2,5}\s*字")
_RE_MD_BOLD_MARKER = re.compile(r"\*\*")
_RE_EPISODE_HEADER_LINE = re.compile(r"^\s*CH\d{2}-\d{3}\s*[:：]\s*\S+")
_RE_META_BLOCK_HEADER = re.compile(r"^\s*(?:設定|CSVデータ|詳細構成|構成案|プロット)\s*$")
_RE_CHAPTER_HEADING_LINE = re.compile(r"^\s*(?:では、)?第[0-9一二三四五六七八九十]+章(?:を始めましょう)?[。！？!?]?\s*$")
_RE_A_TEXT_COMPLETE_ENDING = re.compile(r"[。！？!?][」』）)]*\s*\Z")
_RE_WS_FOR_DUP = re.compile(r"[\s\u3000]+")
_RE_QUOTE_SEG = re.compile(r"「([^「」\n]{1,40})」")
_RE_CH01_FICTIONAL_PERSON_AGE_LINE = re.compile(
    r"^\s*[一-龯々〆ヶヵ]{2,12}[、，]\s*[0-9０-９〇零一二三四五六七八九十百千]{1,6}\s*(?:歳|才)\s*[。！？!?]?\s*$"
)

# TTS/reader risk: known confusable glyphs that frequently appear as LLM typos.
# Keep this minimal and only include cases that are clearly accidental in our domain.
_SUSPICIOUS_GLYPH_REPLACEMENTS: Dict[str, str] = {
    "厊": "厳",
}

_DUMMY_A_TEXT_MARKERS = (
    "この動画の台本本文は外部管理です",
    "ダミー本文を配置しています",
)

_PUNCT_ONLY_CHARS = set("。、.,，．・…")
_RE_PUNCT_WRAP_EMPHASIS = re.compile(r"。[^\\s。！？]{1,12}。")

_SLEEP_CHANNEL_TAG_MARKERS = ("#睡眠用", "#寝落ち")
_SLEEP_FRAMING_ANY_MARKERS = (
    "寝落ち",
    "睡眠用",
    "安眠",
    "就寝",
    "入眠",
    "熟睡",
    "ベッド",
    "枕",
    "寝室",
    "寝る",
    "眠り",
    "眠りにつ",
    "眠りに落",
    "眠りに就",
    "眠りへ誘う",
    "眠りへ導く",
    "眠りへ",
)
_SLEEP_FRAMING_TAIL_MARKERS = (
    "おやすみなさい",
    "ゆっくりお休みください",
    "ゆっくりお休み",
    "就寝",
    "布団に入る前",
    "布団に入って",
    "布団に入った",
    "布団に入ると",
    "布団の中で",
    "ベッド",
    "枕",
    "寝室",
    "眠れなかった",
    "眠れなくて",
    "寝る",
    "入眠",
    "熟睡",
    "眠り",
    "眠りにつ",
    "眠りに落",
    "眠りに就",
    "眠りへ",
    "寝落ち",
    "睡眠用",
    "安眠",
)


def _unicode_desc(ch: str) -> str:
    return f"U+{ord(ch):04X} {unicodedata.name(ch, 'UNKNOWN')}"


def _canonical_a_text_path(base: Path) -> Path:
    content_dir = base / "content"
    human = content_dir / "assembled_human.md"
    assembled = content_dir / "assembled.md"
    return human if human.exists() else assembled


def _legacy_final_assembled_path(base: Path) -> Path:
    return base / "content" / "final" / "assembled.md"


def _infer_channel_from_metadata(metadata: Dict[str, Any]) -> str:
    """
    Best-effort channel inference for validators.
    We keep validate_a_text() signature stable (metadata-only) and infer from known paths.
    """
    md = metadata if isinstance(metadata, dict) else {}
    for key in ("assembled_path", "script_prompt_path", "a_text_rules_path"):
        raw = md.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        s = raw.replace("\\", "/")
        m = re.search(r"(?:^|/)workspaces/scripts/(CH\d{2})/", s, flags=re.IGNORECASE)
        if m:
            return str(m.group(1)).upper()
        m2 = re.search(r"(?:^|/)CH(\d{2})(?:/|\b)", s, flags=re.IGNORECASE)
        if m2:
            return f"CH{m2.group(1)}".upper()
    return ""

def _is_sleep_channel(channel: str) -> bool:
    """
    Heuristic SSOT:
    - Sleep channels explicitly opt-in via `#睡眠用` / `#寝落ち` in youtube_description/default_tags.
    """
    ch = str(channel or "").strip().upper()
    if not ch:
        return False
    try:
        channel_dir = find_channel_dir(ch)
    except Exception:
        channel_dir = None
    if channel_dir is None:
        return False
    info_path = channel_dir / "channel_info.json"
    if not info_path.exists():
        return False
    try:
        data = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    parts: List[str] = []
    yd = data.get("youtube_description")
    if isinstance(yd, str) and yd.strip():
        parts.append(yd)
    tags = data.get("default_tags")
    if isinstance(tags, list):
        parts.extend(str(t) for t in tags if t)
    elif isinstance(tags, str) and tags.strip():
        parts.append(tags)
    blob = "\n".join(parts)
    return any(tok in blob for tok in _SLEEP_CHANNEL_TAG_MARKERS)


def _parse_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _a_text_char_count(text: str) -> int:
    """
    Count "spoken" characters. Heuristic:
    - exclude pause-only lines (`---`)
    - exclude whitespace/newlines
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: List[str] = []
    for line in normalized.split("\n"):
        if line.strip() == "---":
            continue
        lines.append(line)
    compact = "".join(lines)
    compact = compact.replace(" ", "").replace("\t", "").replace("\u3000", "")
    return len(compact.strip())


def _strip_trailing_pause_lines(text: str) -> str:
    """Remove trailing blank lines and trailing pause-only lines (`---`) for end-of-text checks."""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    # Drop trailing blanks.
    while lines and not lines[-1].strip():
        lines.pop()
    # Drop trailing pause lines, allowing blank lines between pauses.
    changed = True
    while changed:
        changed = False
        while lines and not lines[-1].strip():
            lines.pop()
            changed = True
        if lines and lines[-1].strip() == "---":
            lines.pop()
            changed = True
    return "\n".join(lines).rstrip()

def _find_line_number(text: str, needle: str) -> int | None:
    idx = (text or "").find(needle)
    if idx < 0:
        return None
    return (text[:idx].count("\n") + 1) if text else None


def validate_a_text(text: str, metadata: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Validate A-text against SSOT global rules (see ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md).
    Returns (issues, stats).
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    channel = _infer_channel_from_metadata(metadata)
    sleep_channel = _is_sleep_channel(channel) if channel else False

    issues: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {
        "char_count": _a_text_char_count(normalized),
        "pause_lines": sum(1 for ln in lines if ln.strip() == "---"),
        "quote_marks": normalized.count("「")
        + normalized.count("」")
        + normalized.count("『")
        + normalized.count("』"),
        "paren_marks": normalized.count("（") + normalized.count("）") + normalized.count("(") + normalized.count(")"),
    }
    if channel:
        stats["channel"] = channel
        stats["sleep_channel"] = sleep_channel

    if not normalized.strip():
        issues.append({"code": "empty_script", "message": "A-text is empty", "severity": "error"})
        return issues, stats

    # Punctuation/line-break collapse guard:
    # - punctuation-only lines are never valid A-text (use blank line or `---` instead)
    # - extreme short-line ratio indicates the output has been split into 1-2 chars per line
    non_empty_non_pause = [ln for ln in lines if ln.strip() and ln.strip() != "---"]
    punct_only_line_nos: List[int] = []
    short_lines = 0
    for idx, ln in enumerate(lines, start=1):
        s = ln.strip()
        if not s or s == "---":
            continue
        if all(ch in _PUNCT_ONLY_CHARS for ch in s):
            punct_only_line_nos.append(idx)
            continue
        if len(s) <= 2:
            short_lines += 1
    stats["punct_only_lines"] = len(punct_only_line_nos)
    stats["short_lines_le2"] = short_lines
    if punct_only_line_nos:
        issues.append(
            {
                "code": "punctuation_only_line",
                "message": "Punctuation-only line detected (use blank line or `---`, never a standalone `。`/`、`/etc).",
                "line": punct_only_line_nos[0],
                "severity": "error",
            }
        )
    if non_empty_non_pause:
        ratio = short_lines / max(1, len(non_empty_non_pause))
        # Conservative thresholds: false positives are extremely unlikely in natural A-text.
        if short_lines >= 200 and ratio >= 0.20:
            issues.append(
                {
                    "code": "line_segmentation_collapse",
                    "message": f"Too many 1-2 character lines detected (short_lines={short_lines}, ratio={ratio:.3f}).",
                    "severity": "error",
                }
            )

    wrap_emphasis = len(_RE_PUNCT_WRAP_EMPHASIS.findall(normalized))
    stats["punct_wrap_emphasis"] = wrap_emphasis
    if wrap_emphasis >= 50:
        issues.append(
            {
                "code": "punctuation_wrap_emphasis_abuse",
                "message": f"Punctuation-wrapped emphasis detected too often (count={wrap_emphasis}).",
                "severity": "error",
            }
        )

    # Sleep-framing contamination guard (non-sleep channels).
    if channel and not sleep_channel:
        tail = _strip_trailing_pause_lines(normalized)
        tail_window = tail[-500:] if len(tail) > 500 else tail
        for marker in _SLEEP_FRAMING_TAIL_MARKERS:
            if marker in tail_window:
                line = _find_line_number(normalized, marker)
                issues.append(
                    {
                        "code": "sleep_framing_contamination",
                        "message": f"Sleep-framing marker found in tail: {marker}",
                        "line": line,
                        "severity": "error",
                    }
                )
                break
        else:
            for marker in _SLEEP_FRAMING_ANY_MARKERS:
                if marker in normalized:
                    line = _find_line_number(normalized, marker)
                    issues.append(
                        {
                            "code": "sleep_framing_contamination",
                            "message": f"Sleep-framing marker found: {marker}",
                            "line": line,
                            "severity": "error",
                        }
                    )
                    break

    target_min = _parse_int(metadata.get("target_chars_min"))
    target_max = _parse_int(metadata.get("target_chars_max"))
    stats["target_chars_min"] = target_min
    stats["target_chars_max"] = target_max
    if target_min is not None and stats["char_count"] < target_min:
        ratio = (stats["char_count"] / target_min) if target_min > 0 else 0.0
        # Operator-configurable threshold: below target_min is usually "warning" (slack),
        # but certain pipelines require strict enforcement (e.g. 1.0 -> always error when below min).
        try:
            threshold = float(
                str(os.getenv("SCRIPT_VALIDATION_LENGTH_TOO_SHORT_ERROR_RATIO", "0.80")).strip() or "0.80"
            )
        except Exception:
            threshold = 0.80
        threshold = max(0.0, min(1.0, threshold))
        severity = "error" if ratio < threshold else "warning"
        issues.append(
            {
                "code": "length_too_short",
                "message": f"char_count {stats['char_count']} < target_min {target_min} (ratio={ratio:.3f})",
                "severity": severity,
            }
        )
    if target_max is not None and stats["char_count"] > target_max:
        issues.append(
            {
                "code": "length_too_long",
                "message": f"char_count {stats['char_count']} > target_max {target_max}",
                "severity": "error",
            }
        )

    quote_max = _parse_int(metadata.get("a_text_quote_marks_max"))
    if quote_max is None:
        quote_max = 20
    paren_max = _parse_int(metadata.get("a_text_paren_marks_max"))
    if paren_max is None:
        paren_max = 10
    stats["quote_marks_max"] = quote_max
    stats["paren_marks_max"] = paren_max

    if stats["quote_marks"] > quote_max:
        issues.append(
            {
                "code": "too_many_quotes",
                "message": f"quote marks (「」/『』) total {stats['quote_marks']} > guideline {quote_max}",
                "severity": "warning",
            }
        )
    if stats["paren_marks"] > paren_max:
        issues.append(
            {
                "code": "too_many_parentheses",
                "message": f"parentheses (（）/()) total {stats['paren_marks']} > guideline {paren_max}",
                "severity": "warning",
            }
        )
    if channel and (not sleep_channel) and stats["pause_lines"] > 15:
        issues.append(
            {
                "code": "too_many_pause_lines",
                "message": f"pause lines (`---`) total {stats['pause_lines']} > limit 15 for non-sleep channels",
                "severity": "error",
            }
        )

    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue

        if any(marker in line for marker in _DUMMY_A_TEXT_MARKERS):
            issues.append(
                {
                    "code": "dummy_a_text",
                    "message": "Dummy/external-managed placeholder text detected; replace with real A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        # Forbidden/suspicious characters (TTS/readability guardrails)
        if "\ufffd" in line:
            issues.append(
                {
                    "code": "replacement_character",
                    "message": "Unicode replacement character (� / U+FFFD) must not appear in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        bad_controls = [ch for ch in sorted(set(line), key=ord) if unicodedata.category(ch).startswith("C")]
        if bad_controls:
            desc = ", ".join(_unicode_desc(ch) for ch in bad_controls[:3])
            more = f", …(+{len(bad_controls) - 3})" if len(bad_controls) > 3 else ""
            issues.append(
                {
                    "code": "forbidden_unicode_control",
                    "message": f"Forbidden unicode control/format character(s) found: {desc}{more}",
                    "line": idx,
                    "severity": "error",
                }
            )

        for bad, replacement in _SUSPICIOUS_GLYPH_REPLACEMENTS.items():
            if bad in line:
                issues.append(
                    {
                        "code": "suspicious_glyph",
                        "message": f"Suspicious glyph '{bad}' found; replace with '{replacement}'",
                        "line": idx,
                        "severity": "error",
                    }
                )

        if "---" in line and stripped != "---":
            issues.append(
                {
                    "code": "invalid_pause_format",
                    "message": "`---` must be a standalone line",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_BAD_SEPARATOR.match(stripped) and stripped != "---":
            issues.append(
                {
                    "code": "forbidden_separator",
                    "message": "Only `---` is allowed as a separator",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_MD_HEADING.match(stripped):
            issues.append(
                {
                    "code": "markdown_heading",
                    "message": "Headings (`# ...`) are not allowed in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_EPISODE_HEADER_LINE.match(stripped):
            issues.append(
                {
                    "code": "meta_episode_header",
                    "message": "Episode header lines (e.g. `CHxx-NNN: ...`) must not appear in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_META_BLOCK_HEADER.match(stripped):
            issues.append(
                {
                    "code": "meta_block_header",
                    "message": "Structured meta blocks (e.g. 設定/CSVデータ) must not appear in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_CHAPTER_HEADING_LINE.match(stripped):
            issues.append(
                {
                    "code": "chapter_heading_meta",
                    "message": "Chapter headings (e.g. 第3章/第3章を始めましょう) must not appear in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_BULLET_LINE.match(stripped):
            issues.append(
                {
                    "code": "forbidden_bullet",
                    "message": "Bullet/list lines are not allowed in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        # CH01/CH32 policy: forbid the one-line "Name, Age." intro pattern that tends to imply
        # fictional modern protagonists (e.g., "田村幸子、六十七歳。").
        # NOTE: This is a full-line match, so normal sentences like "ブッダが29歳のとき..." are OK.
        if channel in {"CH01", "CH32"} and _RE_CH01_FICTIONAL_PERSON_AGE_LINE.match(stripped):
            issues.append(
                {
                    "code": "ch01_fictional_person_intro",
                    "message": (
                        "CH01/CH32 forbids the one-line `姓名、年齢(歳/才)。` introduction pattern "
                        "(e.g., '田村幸子、六十七歳。'). If you need to mention age, write it as a normal sentence "
                        "(e.g., 'ブッダが29歳のとき…')."
                    ),
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_NUMBERED_LINE.match(stripped):
            issues.append(
                {
                    "code": "forbidden_numbered_list",
                    "message": "Numbered list lines are not allowed in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_LETTERED_LINE.match(stripped):
            issues.append(
                {
                    "code": "forbidden_lettered_list",
                    "message": "Lettered list lines (e.g. `A. ...`) are not allowed in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_URL.search(line):
            issues.append(
                {
                    "code": "forbidden_url",
                    "message": "URLs must not appear in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        # Quotes style guide: use 「」 only for dialogue (avoid term/emphasis quotes like 「老い」とは…).
        # This is heuristic-only (warning); human review decides whether it is truly non-dialogue.
        for m in _RE_QUOTE_SEG.finditer(line):
            inner = (m.group(1) or "").strip()
            if not inner:
                continue
            rest = line[m.end() :].lstrip()
            if not rest:
                continue
            # Likely term/definition patterns (not dialogue)
            if rest.startswith(("とは", "という", "といった", "と呼", "と「", "と『")):
                issues.append(
                    {
                        "code": "quote_non_dialogue",
                        "message": "Use 「」 for dialogue only; rewrite term/emphasis quotes into plain text",
                        "line": idx,
                        "severity": "warning",
                    }
                )
                break
            # If the quoted content already contains sentence punctuation, treat it as dialogue-like.
            if any(ch in inner for ch in "。！？!?"):
                continue
            # If followed by `と...` it's usually a quote of spoken/thought content.
            if rest.startswith("と"):
                continue
            if len(inner) <= 16:
                issues.append(
                    {
                        "code": "quote_non_dialogue",
                        "message": "Use 「」 for dialogue only; rewrite term/emphasis quotes into plain text",
                        "line": idx,
                        "severity": "warning",
                    }
                )
                break

        if _RE_PERCENT_OR_PERCENT_WORD.search(line):
            issues.append(
                {
                    "code": "forbidden_statistics",
                    "message": "Percent/statistical claim detected; ensure it is not fabricated and is context-appropriate",
                    "line": idx,
                    "severity": "warning",
                }
            )

        if _RE_LENGTH_META.search(line):
            issues.append(
                {
                    "code": "length_meta",
                    "message": "Outline/meta length markers (e.g. 約600字) must not appear in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_MD_BOLD_MARKER.search(line):
            issues.append(
                {
                    "code": "markdown_bold",
                    "message": "Markdown emphasis markers (`**...**`) must not appear in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_MD_REF_DEF.search(line) or _RE_MD_REF.search(line) or _RE_NUM_FOOTNOTE.search(line):
            issues.append(
                {
                    "code": "forbidden_citation",
                    "message": "Citations/footnotes must not appear in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        if _RE_TEMPLATE_TOKEN.search(line):
            issues.append(
                {
                    "code": "template_token",
                    "message": "Template tokens must not remain in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

        if "<REPO_ROOT>" in line or "<WORKSPACE_ROOT>" in line:
            issues.append(
                {
                    "code": "placeholder_token",
                    "message": "Placeholders must not appear in A-text",
                    "line": idx,
                    "severity": "error",
                }
            )

    # Duplicate paragraph detection (hard error).
    # Longform drift often manifests as verbatim paragraph repetition; this is cheap to detect.
    paragraphs: List[str] = []
    buf: List[str] = []
    for line in lines:
        if not line.strip() or line.strip() == "---":
            if buf:
                paragraphs.append("\n".join(buf).strip())
                buf = []
            continue
        buf.append(line)
    if buf:
        paragraphs.append("\n".join(buf).strip())

    seen_para: Dict[str, int] = {}
    for para_idx, para in enumerate(paragraphs, start=1):
        core = _RE_WS_FOR_DUP.sub("", para).strip()
        if len(core) < 120:
            continue
        if core in seen_para:
            excerpt = para.replace("\n", " ").strip()
            if len(excerpt) > 80:
                excerpt = excerpt[:80] + "…"
            issues.append(
                {
                    "code": "duplicate_paragraph",
                    "message": (
                        f"Duplicate paragraph detected (para {seen_para[core]} and {para_idx}). "
                        f"excerpt='{excerpt}'"
                    ),
                    "severity": "error",
                }
            )
            break
        seen_para[core] = para_idx

    # Ending completeness (SSOT: OPS_A_TEXT_GLOBAL_RULES.md 1.6)
    core_for_ending = _strip_trailing_pause_lines(normalized)
    if core_for_ending.strip() and not _RE_A_TEXT_COMPLETE_ENDING.search(core_for_ending.strip()):
        tail = core_for_ending.strip().replace("\n", "\\n")[-40:]
        issues.append(
            {
                "code": "incomplete_ending",
                "message": f"A-text ends without closing punctuation (possible truncation). tail='{tail}'",
                "severity": "error",
            }
        )

    uniq: Dict[tuple[str, int | None], Dict[str, Any]] = {}
    for item in issues:
        key = (str(item.get("code")), item.get("line"))
        if key not in uniq:
            uniq[key] = item
    return list(uniq.values()), stats


def validate_stage(channel: str, video: str, stage_defs: List[Dict[str, Any]]) -> List[str]:
    """Return a list of validation errors (empty = OK)."""
    errors: List[str] = []
    st = load_status(channel, video)
    base = DATA_ROOT / channel / video

    def _replace_tokens(p: str) -> Path:
        return base / p.replace("CHxx", channel).replace("NNN", video)

    for sd in stage_defs:
        name = sd.get("name")
        outputs = sd.get("outputs") or []
        # Final A-text validation gate (SSOT global rules)
        if name == "script_validation":
            script_path = _canonical_a_text_path(base)
            if not script_path.exists():
                errors.append(f"[{name}] missing: {script_path}")
                continue
            try:
                text = script_path.read_text(encoding="utf-8")
            except Exception as exc:  # pragma: no cover
                errors.append(f"[{name}] cannot read {script_path}: {exc}")
                continue

            issues, _stats = validate_a_text(text, st.metadata or {})
            legacy_final = _legacy_final_assembled_path(base)
            if legacy_final.exists():
                try:
                    legacy_text = legacy_final.read_text(encoding="utf-8")
                except Exception:
                    legacy_text = ""
                if legacy_text and legacy_text.strip() != text.strip():
                    issues.append(
                        {
                            "code": "legacy_mirror_diverged",
                            "message": f"legacy mirror differs: {legacy_final}",
                            "severity": "error",
                        }
                    )

            for issue in issues:
                severity = str(issue.get("severity") or "error").lower()
                if severity == "warning":
                    continue
                code = issue.get("code")
                line = issue.get("line")
                msg = issue.get("message") or ""
                where = f":{line}" if isinstance(line, int) else ""
                errors.append(f"[{name}] {code}{where}: {msg}")
            continue
        # custom validation for script_review: assembled.md 内容の禁止記号チェック
        if name == "script_review":
            assembled = base / "content" / "assembled.md"
            if assembled.exists():
                try:
                    txt = assembled.read_text(encoding="utf-8")
                    # Only treat "・" as a forbidden bullet when it is used as a list marker
                    # (line-leading), not as a middle dot in proper nouns (e.g., ハンナ・アーレント).
                    if re.search(r"(?m)^\\s*・\\s+", txt):
                        errors.append(f"[{name}] forbidden bullet list marker '・' found in {assembled}")
                except Exception as exc:  # pragma: no cover
                    errors.append(f"[{name}] cannot read {assembled}: {exc}")
        # dynamic chapters: require all chapters present
        if name == "script_draft":
            outline = base / "content" / "outline.md"
            chapters: List[Path] = []
            if outline.exists():
                pat = re.compile(r"^##\\s*第(\\d+)章")
                nums = []
                for line in outline.read_text(encoding="utf-8").splitlines():
                    m = pat.match(line.strip())
                    if m:
                        try:
                            nums.append(int(m.group(1)))
                        except Exception:
                            pass
                if nums:
                    for n in nums:
                        chapters.append(base / f"content/chapters/chapter_{n}.md")
            if not chapters:
                chapters.append(base / "content/chapters/chapter_1.md")
            for p in chapters:
                if not p.exists():
                    errors.append(f"[{name}] missing: {p}")
                    continue
                if p.stat().st_size == 0:
                    errors.append(f"[{name}] empty file: {p}")
            continue
        for out in outputs:
            if not out.get("required"):
                continue
            p = _replace_tokens(out["path"])
            if not p.exists():
                errors.append(f"[{name}] missing: {p}")
                continue
            try:
                if p.is_file() and p.stat().st_size == 0:
                    errors.append(f"[{name}] empty file: {p}")
            except Exception as exc:  # pragma: no cover
                errors.append(f"[{name}] cannot stat {p}: {exc}")
    return errors


def validate_completed_outputs(channel: str, video: str, stage_defs: List[Dict[str, Any]]) -> List[str]:
    """Validate required outputs only for stages marked completed in status.json.

    This is intended for guard/health checks where in-progress videos should not
    fail just because future-stage outputs do not exist yet.

    It also applies artifact lifecycle rules:
    - Once `script_review` is completed, upstream intermediates (research/outline/chapters)
      are allowed to be missing (they may have been purged/archived).
    - For `audio_synthesis`, only durable outputs under `workspaces/audio/final/**` are required.
    """
    errors: List[str] = []
    st = load_status(channel, video)
    base = DATA_ROOT / channel / video

    def _file_ok(path: Path) -> bool:
        if not path.exists():
            return False
        try:
            return (not path.is_file()) or path.stat().st_size > 0
        except Exception:
            return False

    def _replace_tokens(p: str) -> Path:
        return base / p.replace("CHxx", channel).replace("NNN", video)

    stage_status = {name: info.status for name, info in st.stages.items()}
    script_review_done = stage_status.get("script_review") == "completed"
    script_validation_done = stage_status.get("script_validation") == "completed"
    audio_done = stage_status.get("audio_synthesis") == "completed"

    for sd in stage_defs:
        name = sd.get("name")
        if not name:
            continue
        state = st.stages.get(name)
        if state is None or state.status != "completed":
            continue

        # Allow purged intermediates once assembled script exists.
        if script_review_done and name in {"topic_research", "script_outline", "chapter_brief", "script_draft"}:
            continue
        if name == "script_review":
            assembled_candidates = [
                base / "content" / "assembled_human.md",
                base / "content" / "assembled.md",
                # Legacy (for backward-compat only; should be removed)
                base / "content" / "final" / "assembled.md",
            ]
            assembled = next((p for p in assembled_candidates if p.exists()), assembled_candidates[-1])
            if not _file_ok(assembled):
                errors.append(f"[{name}] missing: {assembled}")
            continue

        if name == "audio_synthesis":
            if not audio_done:
                # Only validate completed stage.
                continue
            final_dir = audio_final_dir(channel, video)
            wav_path = final_dir / f"{channel}-{str(video).zfill(3)}.wav"
            srt_path = final_dir / f"{channel}-{str(video).zfill(3)}.srt"
            if not _file_ok(wav_path):
                errors.append(f"[{name}] missing: {wav_path}")
            if not _file_ok(srt_path):
                errors.append(f"[{name}] missing: {srt_path}")
            continue

        # dynamic chapters: require all chapters present (only when assembled is not complete yet)
        if name == "script_draft":
            outline = base / "content" / "outline.md"
            chapters: List[Path] = []
            if outline.exists():
                pat = re.compile(r"^##\\s*第(\\d+)章")
                nums = []
                for line in outline.read_text(encoding="utf-8").splitlines():
                    m = pat.match(line.strip())
                    if m:
                        try:
                            nums.append(int(m.group(1)))
                        except Exception:
                            pass
                if nums:
                    for n in nums:
                        chapters.append(base / f"content/chapters/chapter_{n}.md")
            if not chapters:
                chapters.append(base / "content/chapters/chapter_1.md")
            for p in chapters:
                if not _file_ok(p):
                    errors.append(f"[{name}] missing: {p}")
            continue

        outputs = sd.get("outputs") or []
        for out in outputs:
            if not out.get("required"):
                continue
            p = _replace_tokens(out["path"])
            if not _file_ok(p):
                errors.append(f"[{name}] missing: {p}")
    return errors
