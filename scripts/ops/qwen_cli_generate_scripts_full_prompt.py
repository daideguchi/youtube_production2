#!/usr/bin/env python3
from __future__ import annotations

"""
qwen_cli_generate_scripts_full_prompt.py — Qwen Code CLI (qwen) script writer helper (manual/opt-in)

Purpose:
- Generate/overwrite A-text SoT using external `qwen` CLI (no script_pipeline LLM routing).
- Read the Git-tracked FULL prompt files:
    prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md
- Write A-text SoT to:
    workspaces/scripts/{CH}/{NNN}/content/assembled_human.md
- Mirror to:
    workspaces/scripts/{CH}/{NNN}/content/assembled.md

Safety:
- Deterministic SSOT validation (script_pipeline.validator.validate_a_text) must pass with zero hard errors.
- Non-sleep channels are protected from sleep-framing contamination by SSOT validator.
"""

import argparse
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402
from script_pipeline.validator import validate_a_text  # noqa: E402

_TIME_JUMP_MARKERS = (
    "数日後",
    "数週間後",
    "数ヶ月後",
    "数年後",
    "翌日",
    "翌週",
    "翌月",
    "次の日",
    "次の朝",
    "翌朝",
    "来週",
    "来月",
    "昨日",
    "朝食",
    "それから数日",
    "それから数週間",
    "それから数ヶ月",
    "それから数年",
)

_SLEEP_MARKERS_NON_SLEEP = (
    "おやすみ",
    "寝落ち",
    "睡眠用",
    "安眠",
    "布団",
    "ベッド",
    "枕",
    "寝室",
    "就寝",
    "入眠",
    "熟睡",
    "寝る",
    "眠る",
    "眠り",
    "眠りにつ",
    "眠りに落",
    "眠りに就",
    "眠れな",
    "眠りへ",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha1_text(text: str) -> str:
    h = hashlib.sha1()
    h.update((text or "").encode("utf-8"))
    return h.hexdigest()


def _z3(value: int | str) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        raise SystemExit(f"Invalid video: {value!r}")
    return f"{int(digits):03d}"


def _parse_indices(expr: str) -> List[int]:
    raw = str(expr or "").strip()
    if not raw:
        return []
    out: List[int] = []
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        if "-" in t:
            a, b = [x.strip() for x in t.split("-", 1)]
            if not a or not b:
                raise SystemExit(f"Invalid --videos range: {t!r}")
            lo = int(a)
            hi = int(b)
            if hi < lo:
                lo, hi = hi, lo
            out.extend(list(range(lo, hi + 1)))
        else:
            out.append(int(t))
    return sorted(set([i for i in out if i > 0]))


def _parse_videos(expr: str) -> List[str]:
    return [f"{i:03d}" for i in _parse_indices(expr)]


def _normalize_newlines(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _a_text_spoken_char_count(text: str) -> int:
    normalized = _normalize_newlines(text)
    lines: List[str] = []
    for line in normalized.split("\n"):
        if line.strip() == "---":
            continue
        lines.append(line)
    compact = "".join(lines)
    compact = compact.replace(" ", "").replace("\t", "").replace("\u3000", "")
    return len(compact.strip())


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _scratch_dir() -> Path:
    override = str(os.environ.get("QWEN_CLI_SCRATCH_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    # Run qwen from a scratch directory so it won't treat the whole repo as an implicit "workspace context".
    return repo_paths.workspace_root() / "_scratch" / "qwen_cli"


def _prompt_path(channel: str, video: str) -> Path:
    ch = str(channel).strip().upper()
    vv = _z3(video)
    return repo_paths.repo_root() / "prompts" / "antigravity_gemini" / ch / f"{ch}_{vv}_FULL_PROMPT.md"


def _output_a_text_path(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "content" / "assembled_human.md"


def _logs_dir(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "logs"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    _ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    import json

    _ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _blueprint_paths(channel: str, video: str) -> Dict[str, Path]:
    base = repo_paths.video_root(channel, video)
    return {
        "outline": base / "content" / "outline.md",
        "master_plan": base / "content" / "analysis" / "master_plan.json",
        "research_brief": base / "content" / "analysis" / "research" / "research_brief.md",
        "references": base / "content" / "analysis" / "research" / "references.json",
        "search_results": base / "content" / "analysis" / "research" / "search_results.json",
        "wikipedia_summary": base / "content" / "analysis" / "research" / "wikipedia_summary.json",
        "status": base / "status.json",
    }


def _is_outline_placeholder(text: str) -> bool:
    norm = _normalize_newlines(text).strip()
    return norm == "# Outline\n\n1. Intro\n2. Body\n3. Outro\n"


def _is_research_brief_placeholder(text: str) -> bool:
    norm = _normalize_newlines(text)
    return norm.startswith("# Research Brief") and "- Finding 1" in norm and "- Finding 2" in norm


def _read_json_any(path: Path) -> Any:
    import json

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _truncate_for_prompt(text: str, *, max_chars: int) -> str:
    s = str(text or "")
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "\n\n[TRUNCATED]\n"


def _ensure_blueprint_ready(*, channel: str, video: str, require: bool) -> Tuple[bool, str]:
    if str(os.getenv("YTM_EMERGENCY_OVERRIDE") or "").strip() == "1":
        return True, ""

    p = _blueprint_paths(channel, video)
    missing: List[str] = []
    problems: List[str] = []

    outline = p["outline"]
    if not outline.exists():
        missing.append(str(outline))
    else:
        try:
            if _is_outline_placeholder(outline.read_text(encoding="utf-8")):
                problems.append(f"outline is placeholder: {outline}")
        except Exception:
            problems.append(f"outline unreadable: {outline}")

    master_plan = p["master_plan"]
    if not master_plan.exists():
        missing.append(str(master_plan))
    else:
        mp = _read_json_any(master_plan)
        if not isinstance(mp, dict):
            problems.append(f"master_plan.json invalid JSON: {master_plan}")

    brief = p["research_brief"]
    if not brief.exists():
        missing.append(str(brief))
    else:
        try:
            if _is_research_brief_placeholder(brief.read_text(encoding="utf-8")):
                problems.append(f"research_brief is placeholder: {brief}")
        except Exception:
            problems.append(f"research_brief unreadable: {brief}")

    refs = p["references"]
    if not refs.exists():
        missing.append(str(refs))
    else:
        obj = _read_json_any(refs)
        if not isinstance(obj, list) or len(obj) == 0:
            problems.append(f"references.json empty/invalid: {refs}")

    search = p["search_results"]
    if not search.exists():
        missing.append(str(search))
    else:
        so = _read_json_any(search)
        hits = so.get("hits") if isinstance(so, dict) else None
        prov = str(so.get("provider") or "").strip() if isinstance(so, dict) else ""
        if prov == "disabled" and (not isinstance(hits, list) or len(hits) == 0):
            problems.append(f"search_results.json is placeholder (provider=disabled, hits=0): {search}")

    if missing or problems:
        msg = "\n".join(
            [
                "[POLICY] Blueprint not ready (Codex must finish research+outline before Writer runs).",
                f"- episode: {str(channel).upper()}-{_z3(video)}",
                "- required stages: topic_research -> script_outline -> script_master_plan",
                "",
                "Fix (canonical):",
                f"  ./ops script resume -- --channel {str(channel).upper()} --video {_z3(video)} --until script_master_plan --max-iter 6",
                "",
                "If you need to inject sources manually (no web provider):",
                f"  python3 scripts/ops/research_bundle.py template --channel {str(channel).upper()} --video {_z3(video)} > /tmp/research_bundle.json",
                "  # fill /tmp/research_bundle.json with sources, then:",
                "  python3 scripts/ops/research_bundle.py apply --bundle /tmp/research_bundle.json",
                "",
                "Missing:",
                *([f"  - {m}" for m in missing] if missing else ["  - (none)"]),
                "Problems:",
                *([f"  - {p2}" for p2 in problems] if problems else ["  - (none)"]),
                "",
                "Emergency override (debug only): set YTM_EMERGENCY_OVERRIDE=1 for this run.",
            ]
        )
        if require:
            raise SystemExit(msg)
        return False, msg

    wiki = p["wikipedia_summary"]
    appendix_parts: List[str] = []
    appendix_parts.append("<<<BLUEPRINT_BUNDLE_START>>>")
    appendix_parts.append("以下は Codex が確定させた設計図/根拠（SoT）です。本文にURL/脚注/参照番号は出さない。")
    appendix_parts.append(f"- episode: {str(channel).upper()}-{_z3(video)}")
    appendix_parts.append("")
    try:
        appendix_parts.append("## Outline (content/outline.md)")
        appendix_parts.append(_truncate_for_prompt(outline.read_text(encoding="utf-8"), max_chars=14000).strip())
        appendix_parts.append("")
    except Exception:
        pass
    try:
        appendix_parts.append("## Research brief (content/analysis/research/research_brief.md)")
        appendix_parts.append(_truncate_for_prompt(brief.read_text(encoding="utf-8"), max_chars=14000).strip())
        appendix_parts.append("")
    except Exception:
        pass
    try:
        appendix_parts.append("## References (content/analysis/research/references.json)")
        appendix_parts.append(_truncate_for_prompt(refs.read_text(encoding="utf-8"), max_chars=9000).strip())
        appendix_parts.append("")
    except Exception:
        pass
    try:
        appendix_parts.append("## Web search results (content/analysis/research/search_results.json)")
        appendix_parts.append(_truncate_for_prompt(search.read_text(encoding="utf-8"), max_chars=9000).strip())
        appendix_parts.append("")
    except Exception:
        pass
    try:
        if wiki.exists():
            appendix_parts.append("## Wikipedia summary (content/analysis/research/wikipedia_summary.json)")
            appendix_parts.append(_truncate_for_prompt(wiki.read_text(encoding="utf-8"), max_chars=9000).strip())
            appendix_parts.append("")
    except Exception:
        pass
    try:
        appendix_parts.append("## Master plan (content/analysis/master_plan.json)")
        appendix_parts.append(_truncate_for_prompt(master_plan.read_text(encoding="utf-8"), max_chars=9000).strip())
        appendix_parts.append("")
    except Exception:
        pass
    appendix_parts.append("<<<BLUEPRINT_BUNDLE_END>>>")
    appendix = "\n".join([x for x in appendix_parts if str(x).strip()]).strip() + "\n"
    return True, appendix


def _backup_if_diff(path: Path, new_text: str) -> Optional[Path]:
    if not path.exists():
        return None
    try:
        old_text = path.read_text(encoding="utf-8")
    except Exception:
        old_text = ""
    if _sha1_text(old_text) == _sha1_text(new_text):
        return None
    backup = path.with_name(path.name + f".bak.{_utc_now_compact()}")
    _write_text(backup, old_text)
    return backup


def _find_qwen_bin(explicit: str | None) -> str:
    """
    Policy: in this repository, qwen must be invoked via the repo shim:
      scripts/bin/qwen
    The shim enforces qwen-oauth and blocks provider/model switching.
    """
    shim = repo_paths.repo_root() / "scripts" / "bin" / "qwen"
    if explicit:
        p = Path(str(explicit)).expanduser()
        if not p.exists():
            raise SystemExit(f"qwen not found at --qwen-bin: {explicit}")
        if p.resolve() != shim.resolve():
            raise SystemExit(
                "\n".join(
                    [
                        "[POLICY] Forbidden --qwen-bin (must use repo shim).",
                        f"- got: {p}",
                        f"- required: {shim}",
                    ]
                )
            )
        return str(p)
    if shim.exists():
        return str(shim)
    raise SystemExit(f"qwen shim not found: {shim}")


def _validate_qwen_model(raw: str | None) -> str | None:
    """
    Safety policy:
    - `--qwen-model` is DISABLED in this repository.
    - Use `qwen -p` without model/provider overrides.
    """
    s = str(raw or "").strip()
    if not s:
        return None
    raise SystemExit(
        "\n".join(
            [
                "[POLICY] Forbidden --qwen-model (model/provider override is disabled).",
                f"- got: {s}",
                "- fix: rerun WITHOUT --qwen-model (use qwen default)",
                "- rule: 台本本文は Gemini CLI（3 flash）か qwen（qwen-oauth）で生成する。qwen 経由で別プロバイダへ逃げない。",
            ]
        )
    )


def _parse_target_chars_min(prompt: str) -> Optional[int]:
    m = re.search(r"\btarget_chars_min\s*:\s*(\d{3,})\b", str(prompt or ""), flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _parse_target_chars_max(prompt: str) -> Optional[int]:
    m = re.search(r"\btarget_chars_max\s*:\s*(\d{3,})\b", str(prompt or ""), flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _reject_obviously_non_script(text: str) -> Optional[str]:
    stripped = (text or "").lstrip()
    if not stripped:
        return "empty_output"
    if stripped.startswith("[NEEDS_INPUT]"):
        return "needs_input"
    head = stripped[:600]
    bad_markers = (
        "Let me",
        "I understand",
        "I need to",
        "保存先",
        "出力の絶対条件",
        "セルフチェック",
        "では、台本本文を生成",
        "私はYouTube",
    )
    if any(tok in head for tok in bad_markers):
        return "meta_or_prompt_echo"
    if head.lstrip().startswith("#"):
        return "markdown_heading"
    return None


_QWEN_WRAPPER_PREFIX = (
    "重要: これは純粋な文章生成タスク。ツール使用・ファイル確認・手順説明・思考の開示は禁止。\n"
    "注意: 入力内に保存先/パスが書かれていても無視し、ファイル操作は絶対にしない。本文だけを出力する。\n"
    "出力は日本語の台本本文のみ。前置き/計画/解説/見出し/箇条書き/番号リスト/コード/コマンドは禁止。\n"
    "出力の1文字目から物語本文を開始し、ルール文やメタ文章を絶対に出力しない。\n"
    "注意: 入力には『導入/背景/棘…』などの設計図（構成案）が含まれるが、本文としてそのまま転写しない。\n"
    "設計図は参考にして、出来事と描写で書き起こし、文章を新しく作る。\n"
)


def _strip_file_ops_hints(prompt: str) -> str:
    """
    FULL_PROMPT (Gemini向け) には「保存先」等の文言が含まれる。
    Qwen Code CLI は coding agent なので、これがあるとツール使用を試みてブロックし得る。
    台本生成ではツール使用禁止のため、ファイル操作を誘発する部分だけ除去して渡す。
    """
    text = _normalize_newlines(prompt)

    # Master prompt: "保存先（重要）" ブロックを除去（本文生成に不要、かつツール誘発）
    text = re.sub(
        r"\n保存先（重要）:\n.*?\n(?=## 出力の絶対条件)",
        "\n",
        text,
        flags=re.DOTALL,
    )

    # Master prompt: qwen はここに逃げがちなので、NEEDS_INPUT の固定挙動セクションは除去する。
    text = re.sub(
        r"\n## 失敗時の固定挙動（重要）\n.*?\n(?=## 内容と品質の絶対条件)",
        "\n",
        text,
        flags=re.DOTALL,
    )

    # Per-episode prompt: "使い方（固定）" ブロックを除去（貼り方ガイドで本文要件ではない）
    text = re.sub(
        r"\n## 0\) 使い方（固定）\n.*?\n(?=## 1\) CHANNEL PROMPT)",
        "\n",
        text,
        flags=re.DOTALL,
    )

    # Per-episode prompt: INPUT CHECK ブロックを除去（qwenには不要、[NEEDS_INPUT]誘発要因）
    text = re.sub(
        r"\n### INPUT CHECK（不足なら \[NEEDS_INPUT\]）\n.*?(?=\n## )",
        "\n",
        text,
        flags=re.DOTALL,
    )

    return text.lstrip()


def _build_prompt(*, base_prompt: str, instruction: str | None, current_a_text: str | None) -> str:
    parts: List[str] = [str(base_prompt or "").rstrip()]
    if current_a_text:
        parts.append("<<<CURRENT_A_TEXT_START>>>")
        parts.append(str(current_a_text).rstrip())
        parts.append("<<<CURRENT_A_TEXT_END>>>")
    if instruction:
        parts.append("<<<OPERATOR_INSTRUCTION_START>>>")
        parts.append(str(instruction).strip())
        parts.append("<<<OPERATOR_INSTRUCTION_END>>>")
    joined = "\n\n".join([p for p in parts if str(p).strip()]).strip()
    return joined + "\n"


def _tail_context(text: str, *, max_chars: int = 1800) -> str:
    normalized = _normalize_newlines(text).rstrip()
    if not normalized:
        return ""
    limit = max(200, int(max_chars))
    if len(normalized) <= limit:
        return normalized + "\n"
    tail = normalized[-limit:]
    # Try to cut at a paragraph boundary to reduce accidental repetition.
    idx = tail.find("\n\n")
    if idx >= 0 and idx <= 400:
        tail = tail[idx + 2 :]
    return tail.strip() + "\n"


def _strip_edge_pause_lines(text: str) -> str:
    normalized = _normalize_newlines(text)
    lines = normalized.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    changed = True
    while changed:
        changed = False
        while lines and not lines[0].strip():
            lines.pop(0)
            changed = True
        if lines and lines[0].strip() == "---":
            lines.pop(0)
            changed = True
    changed = True
    while changed:
        changed = False
        while lines and not lines[-1].strip():
            lines.pop()
            changed = True
        if lines and lines[-1].strip() == "---":
            lines.pop()
            changed = True
    out = "\n".join(lines).strip()
    return out + ("\n" if out else "")


def _find_time_jump_marker(text: str) -> str | None:
    blob = str(text or "")
    for marker in _TIME_JUMP_MARKERS:
        if marker and marker in blob:
            return marker
    return None


def _strip_time_jump_paragraphs(text: str) -> tuple[str, int]:
    normalized = _normalize_newlines(text).strip()
    if not normalized:
        return "", 0
    paras = [p.strip() for p in re.split(r"\n\s*\n+", normalized) if p.strip()]
    kept: List[str] = []
    removed = 0
    for p in paras:
        if any(tok in p for tok in _TIME_JUMP_MARKERS):
            removed += 1
            continue
        kept.append(p)
    out = "\n\n".join(kept).rstrip()
    return (out + "\n") if out else "", removed


def _strip_sleep_paragraphs(text: str) -> tuple[str, int]:
    normalized = _normalize_newlines(text).strip()
    if not normalized:
        return "", 0
    paras = [p.strip() for p in re.split(r"\n\s*\n+", normalized) if p.strip()]
    kept: List[str] = []
    removed = 0
    for p in paras:
        if any(tok in p for tok in _SLEEP_MARKERS_NON_SLEEP):
            removed += 1
            continue
        kept.append(p)
    out = "\n\n".join(kept).rstrip()
    return (out + "\n") if out else "", removed


def _strip_continuation_overlap(*, combined: str, chunk: str) -> str:
    """
    qwen が「現状の全文を再掲→続き」を返すことがあるため、再掲ぶんを剥がす。
    """
    base = _normalize_newlines(combined).rstrip()
    if not base:
        return chunk

    cand_full = _normalize_newlines(chunk).rstrip()
    cand = cand_full.lstrip()
    if not cand:
        return chunk

    if cand.startswith(base):
        rest = cand[len(base) :].lstrip("\n").strip()
        return (rest + "\n") if rest else ""

    max_overlap = min(len(base), 2000)
    for k in [max_overlap, 1600, 1200, 1000, 800, 600, 400, 300, 250, 200, 160, 120]:
        if k <= 0 or len(base) < k:
            continue
        if cand.startswith(base[-k:]):
            rest = cand[k:].lstrip("\n").strip()
            return (rest + "\n") if rest else ""

    return chunk


def _dedupe_long_paragraphs(text: str, *, min_chars: int = 30) -> tuple[str, int]:
    """
    Remove exact duplicate paragraphs (whitespace-insensitive) when the paragraph is long enough.
    This is a deterministic salvage step for "duplicate_paragraph" SSOT hard errors.
    Returns (deduped_text, removed_count).
    """
    normalized = _normalize_newlines(text).strip()
    if not normalized:
        return "", 0
    paras = [p.strip() for p in re.split(r"\n\s*\n+", normalized) if p.strip()]
    seen: set[str] = set()
    kept: List[str] = []
    removed = 0
    for p in paras:
        key = re.sub(r"\s+", "", p)
        if len(key) >= int(min_chars):
            if key in seen:
                removed += 1
                continue
            seen.add(key)
        kept.append(p)
    out = "\n\n".join(kept).rstrip()
    return (out + "\n") if out else "", removed


def _dedupe_near_paragraphs(text: str, *, min_chars: int = 200, similarity: float = 0.93) -> tuple[str, int]:
    """
    Remove near-duplicate long paragraphs (high similarity after whitespace collapse).
    This targets common LLM failure modes where the same idea is restated with minor edits.
    Returns (deduped_text, removed_count).
    """
    normalized = _normalize_newlines(text).strip()
    if not normalized:
        return "", 0
    paras = [p.strip() for p in re.split(r"\n\s*\n+", normalized) if p.strip()]
    kept: List[str] = []
    kept_compact: List[str] = []
    removed = 0
    threshold = float(similarity)
    min_len = int(min_chars)
    for p in paras:
        compact = re.sub(r"\s+", "", p)
        if len(compact) < min_len:
            kept.append(p)
            kept_compact.append(compact)
            continue
        is_dup = False
        for prev in kept_compact:
            if len(prev) < min_len:
                continue
            if difflib.SequenceMatcher(a=prev, b=compact).ratio() >= threshold:
                is_dup = True
                break
        if is_dup:
            removed += 1
            continue
        kept.append(p)
        kept_compact.append(compact)
    out = "\n\n".join(kept).rstrip()
    return (out + "\n") if out else "", removed


def _dedupe_near_paragraphs_story(text: str) -> tuple[str, int]:
    # Story channels often repeat the same scene with minor rewrites; be more aggressive.
    return _dedupe_near_paragraphs(text, min_chars=80, similarity=0.90)


def _dedupe_tail_exact_paragraphs(text: str, *, tail_paras: int = 14, min_chars: int = 70) -> tuple[str, int]:
    """
    Remove exact duplicate paragraphs in the *tail* (including repeats of earlier paragraphs),
    allowing shorter paragraphs than the global dedupe threshold.

    This targets a common failure mode where the model repeats the closing paragraph(s)
    multiple times, which often slips under the global min_chars.
    """
    normalized = _normalize_newlines(text).strip()
    if not normalized:
        return "", 0
    paras = [p.strip() for p in re.split(r"\n\s*\n+", normalized) if p.strip()]
    if not paras:
        return "", 0

    tail_n = max(1, int(tail_paras))
    cut = max(0, len(paras) - tail_n)
    head = paras[:cut]
    tail = paras[cut:]

    threshold = max(0, int(min_chars))
    seen: set[str] = set()
    for p in head:
        key = re.sub(r"\s+", "", p)
        if len(key) >= threshold:
            seen.add(key)

    kept_tail: List[str] = []
    removed = 0
    for p in tail:
        key = re.sub(r"\s+", "", p)
        if len(key) >= threshold:
            if key in seen:
                removed += 1
                continue
            seen.add(key)
        kept_tail.append(p)

    out = "\n\n".join([*head, *kept_tail]).rstrip()
    return (out + "\n") if out else "", removed


def _continue_instruction(*, add_min: int, add_max: int, total_min: int | None, total_max: int | None) -> str:
    lo = int(max(0, add_min))
    hi = int(max(lo, add_max))
    total_range = ""
    if total_min is not None and total_max is not None and total_min > 0 and total_max > 0:
        total_range = f"（全体は必ず {int(total_min)}〜{int(total_max)} 字）"
    return (
        "指示: <<<CURRENT_A_TEXT_START>>> の直後から、自然につながる『続きだけ』を書いてください。"
        "要約・言い換え連打・前文の繰り返し・同一段落の再掲は禁止。"
        "不足分は『同じ出来事の中での棘/場面/観察』を追加し、生活音/手元/距離感など具体で伸ばしてください。"
        "重要: 新しい出来事/新しい場所への外出/買い物/散歩/喫茶店/公園/別の電話/別の人物の追加で水増ししない。"
        "重要: 追加分は『同じ場面の延長』で深掘りする（場面転換を増やさない）。"
        "時間ジャンプ（次の日/次の朝/昨日/朝食/翌日/翌朝/来週/来月/数日後/数週間/数ヶ月/数年後/翌週/翌月/それから数…）で水増ししない。"
        "結末/再定義/一手/余韻を二重に書かない（同じ締めを繰り返さない）。"
        f"今から書く追加分は必ず {lo}〜{hi} 字{total_range}。"
        "最後は物語として完結し、句点などで確実に閉じてください。"
    )


def _run_qwen_cli(
    *,
    qwen_bin: str,
    prompt: str,
    model: str | None,
    sandbox: bool,
    approval_mode: str | None,
    yolo: bool,
    timeout_sec: int,
) -> tuple[int, str, str, float]:
    # qwen_bin is the repo shim; auth-type/model/provider switching is enforced there.
    cmd: List[str] = [qwen_bin, "--output-format", "text"]
    if model:
        cmd += ["--model", str(model)]
    if sandbox:
        cmd.append("--sandbox")
    if approval_mode:
        cmd += ["--approval-mode", str(approval_mode)]
    elif yolo:
        cmd.append("--yolo")

    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=str(prompt),
            text=True,
            capture_output=True,
            cwd=str(_scratch_dir()),
            env=env,
            timeout=max(1, int(timeout_sec)),
        )
        elapsed = time.time() - start
        return int(proc.returncode), str(proc.stdout or ""), str(proc.stderr or ""), float(elapsed)
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - start
        stdout = str(e.stdout or "")
        stderr = (str(e.stderr or "") + f"\n[timeout] seconds={int(timeout_sec)}\n").strip() + "\n"
        return 124, stdout, stderr, float(elapsed)


def _extend_until_min(
    *,
    qwen_bin: str,
    base_prompt: str,
    base_instruction: str | None,
    model: str | None,
    sandbox: bool,
    approval_mode: str | None,
    yolo: bool,
    timeout_sec: int,
    logs_dir: Path,
    script_id: str,
    a_text: str,
    min_spoken_chars: int,
    target_chars_min: int | None,
    target_chars_max: int | None,
    max_continue_rounds: int,
) -> tuple[str, Optional[str]]:
    combined = _normalize_newlines(a_text).rstrip() + "\n"
    for cont in range(1, max(0, int(max_continue_rounds)) + 1):
        spoken = _a_text_spoken_char_count(combined)
        if spoken >= int(min_spoken_chars):
            return combined, None

        missing = int(min_spoken_chars) - spoken
        need_min = max(200, min(missing, 900))
        need_max = max(need_min, min(missing + 200, 1500))
        if target_chars_max is not None and target_chars_max > 0:
            remaining_max = max(0, int(target_chars_max) - spoken)
            need_max = min(need_max, remaining_max)
            need_min = min(need_min, need_max)

        instruction_parts: List[str] = []
        if base_instruction:
            instruction_parts.append(str(base_instruction).strip())
        instruction_parts.append(
            _continue_instruction(add_min=need_min, add_max=need_max, total_min=target_chars_min, total_max=target_chars_max)
        )
        cont_prompt = _build_prompt(
            base_prompt=_QWEN_WRAPPER_PREFIX,
            instruction="\n\n".join([p for p in instruction_parts if p]).strip(),
            current_a_text=_tail_context(combined),
        )
        cont_prompt_log = logs_dir / f"qwen_cli_prompt__cont{cont:02d}.txt"
        cont_stdout_log = logs_dir / f"qwen_cli_stdout__cont{cont:02d}.txt"
        cont_stderr_log = logs_dir / f"qwen_cli_stderr__cont{cont:02d}.txt"
        _write_text(cont_prompt_log, cont_prompt)

        rc, stdout, stderr, _elapsed = _run_qwen_cli(
            qwen_bin=qwen_bin,
            prompt=cont_prompt,
            model=model,
            sandbox=sandbox,
            approval_mode=approval_mode,
            yolo=yolo,
            timeout_sec=timeout_sec,
        )
        _write_text(cont_stdout_log, stdout)
        _write_text(cont_stderr_log, stderr)
        if rc != 0:
            return combined, f"{script_id}: qwen_exit={rc} (see {cont_stderr_log})"

        chunk = _normalize_newlines(stdout).rstrip() + "\n"
        reject_reason = _reject_obviously_non_script(chunk)
        if reject_reason:
            return combined, f"{script_id}: rejected_output={reject_reason} (see {cont_stdout_log})"

        cleaned = _strip_edge_pause_lines(chunk)
        cleaned = _strip_continuation_overlap(combined=combined, chunk=cleaned)
        cleaned = _strip_edge_pause_lines(cleaned)
        ch = str(script_id).split("-", 1)[0].strip().upper()
        if ch in {"CH28", "CH29"}:
            cleaned, _removed_tj = _strip_time_jump_paragraphs(cleaned)
            cleaned, _removed_sleep = _strip_sleep_paragraphs(cleaned)
        cleaned_compact = cleaned.strip()
        if not cleaned_compact:
            continue
        if len(cleaned_compact) >= 200 and cleaned_compact in combined:
            continue

        combined = combined.rstrip() + "\n\n" + cleaned
        combined = combined.rstrip() + "\n"

    return (
        combined,
        f"{script_id}: rejected_output=too_short_after_continuations min={min_spoken_chars} spoken={_a_text_spoken_char_count(combined)}",
    )


def cmd_run(args: argparse.Namespace) -> int:
    channel = str(args.channel or "").strip().upper()
    if not re.fullmatch(r"CH\d{2}", channel):
        raise SystemExit(f"Invalid --channel: {args.channel!r} (expected CHxx)")

    videos: List[str] = []
    if args.video:
        videos = [_z3(args.video)]
    elif args.videos:
        videos = _parse_videos(args.videos)
    else:
        raise SystemExit("Specify --video NNN or --videos NNN-NNN")

    qwen_bin = _find_qwen_bin(args.qwen_bin)
    qwen_model = _validate_qwen_model(args.qwen_model)
    _ensure_dir(_scratch_dir())

    failures: List[str] = []

    for vv in videos:
        script_id = f"{channel}-{vv}"
        prompt_path = _prompt_path(channel, vv)
        out_path = _output_a_text_path(channel, vv)
        mirror_path = out_path.with_name("assembled.md")
        logs_dir = _logs_dir(channel, vv)

        if not prompt_path.exists():
            raise SystemExit(f"Prompt not found: {prompt_path} ({script_id})")

        base_prompt = _strip_file_ops_hints(_read_text(prompt_path))
        ok_blueprint, blueprint_payload = _ensure_blueprint_ready(channel=channel, video=vv, require=bool(args.run))
        if ok_blueprint and blueprint_payload:
            base_prompt = (base_prompt.rstrip() + "\n\n" + blueprint_payload.strip()).strip() + "\n"
        wrapped_prompt = (_QWEN_WRAPPER_PREFIX + "\n" + base_prompt.strip()).strip() + "\n"
        detected_min = _parse_target_chars_min(base_prompt)
        detected_max = _parse_target_chars_max(base_prompt)

        min_spoken_chars = int(args.min_spoken_chars or 0)
        if min_spoken_chars <= 0 and detected_min:
            min_spoken_chars = int(detected_min)

        if not args.run:
            print(f"[DRY-RUN] {script_id}")
            print(f"- qwen: {qwen_bin}")
            if qwen_model:
                print(f"- model: {qwen_model}")
            if args.qwen_sandbox:
                print("- sandbox: true")
            if args.qwen_approval_mode:
                print(f"- approval_mode: {args.qwen_approval_mode}")
            elif args.qwen_yolo:
                print("- yolo: true")
            print(f"- prompt: {prompt_path}")
            if ok_blueprint:
                print(f"- blueprint: OK")
            else:
                print(f"- blueprint: MISSING (run: ./ops script resume -- --channel {channel} --video {vv} --until script_master_plan --max-iter 6)")
            print(f"- output: {out_path}")
            print("")
            continue

        _ensure_dir(logs_dir)
        prompt_log = logs_dir / "qwen_cli_prompt.txt"
        stdout_log = logs_dir / "qwen_cli_stdout.txt"
        stderr_log = logs_dir / "qwen_cli_stderr.txt"
        meta_log = logs_dir / "qwen_cli_meta.json"

        max_attempts = max(1, int(args.max_attempts))
        max_continue_rounds = max(0, int(args.max_continue_rounds))
        last_failure: Optional[str] = None

        for attempt in range(1, max_attempts + 1):
            retry_hint = ""
            if attempt > 1:
                retry_hint = (
                    "再試行: 直前の出力が不合格。本文のみを出力し、ルール説明/見出し/箇条書き/番号リスト/マーカー文字列/段落重複を絶対に出さない。"
                )
            attempt_instruction = str(args.instruction or "").strip()
            if retry_hint:
                attempt_instruction = (attempt_instruction + "\n\n" + retry_hint).strip() if attempt_instruction else retry_hint
            if attempt_instruction:
                attempt_instruction = (attempt_instruction + f"\nretry_attempt: {attempt}").strip()

            attempt_prompt = _build_prompt(
                base_prompt=wrapped_prompt,
                instruction=attempt_instruction if attempt_instruction else None,
                current_a_text=None,
            )

            attempt_prompt_log = logs_dir / f"qwen_cli_prompt__attempt{attempt:02d}.txt"
            attempt_stdout_log = logs_dir / f"qwen_cli_stdout__attempt{attempt:02d}.txt"
            attempt_stderr_log = logs_dir / f"qwen_cli_stderr__attempt{attempt:02d}.txt"
            attempt_meta_log = logs_dir / f"qwen_cli_meta__attempt{attempt:02d}.json"
            _write_text(prompt_log, attempt_prompt)
            _write_text(attempt_prompt_log, attempt_prompt)

            rc, stdout, stderr, elapsed = _run_qwen_cli(
                qwen_bin=qwen_bin,
                prompt=attempt_prompt,
                model=qwen_model,
                sandbox=bool(args.qwen_sandbox),
                approval_mode=str(args.qwen_approval_mode) if args.qwen_approval_mode else None,
                yolo=bool(args.qwen_yolo),
                timeout_sec=int(args.timeout_sec),
            )
            _write_text(stdout_log, stdout)
            _write_text(stderr_log, stderr)
            _write_text(attempt_stdout_log, stdout)
            _write_text(attempt_stderr_log, stderr)
            _write_json(
                meta_log,
                {
                    "schema_version": 1,
                    "tool": "qwen_cli_generate_scripts_full_prompt",
                    "at": _utc_now_iso(),
                    "script_id": script_id,
                    "prompt_path": str(prompt_path),
                    "output_path": str(out_path),
                    "qwen_bin": qwen_bin,
                    "qwen_model": qwen_model or "",
                    "qwen_sandbox": bool(args.qwen_sandbox),
                    "qwen_approval_mode": args.qwen_approval_mode,
                    "qwen_yolo": bool(args.qwen_yolo),
                    "timeout_sec": int(args.timeout_sec),
                    "elapsed_sec": elapsed,
                    "exit_code": rc,
                    "attempt": attempt,
                },
            )
            _write_json(
                attempt_meta_log,
                {
                    "schema_version": 1,
                    "tool": "qwen_cli_generate_scripts_full_prompt",
                    "at": _utc_now_iso(),
                    "script_id": script_id,
                    "prompt_path": str(prompt_path),
                    "output_path": str(out_path),
                    "qwen_bin": qwen_bin,
                    "qwen_model": qwen_model or "",
                    "qwen_sandbox": bool(args.qwen_sandbox),
                    "qwen_approval_mode": args.qwen_approval_mode,
                    "qwen_yolo": bool(args.qwen_yolo),
                    "timeout_sec": int(args.timeout_sec),
                    "elapsed_sec": elapsed,
                    "exit_code": rc,
                    "attempt": attempt,
                },
            )

            if rc != 0:
                last_failure = f"{script_id}: qwen_exit={rc} (see {attempt_stderr_log})"
                continue

            a_text = _normalize_newlines(stdout).rstrip() + "\n"
            reject_reason = _reject_obviously_non_script(a_text)
            if reject_reason:
                last_failure = f"{script_id}: rejected_output={reject_reason} (see {attempt_stdout_log})"
                continue

            if min_spoken_chars > 0 and not bool(args.allow_short):
                spoken_chars = _a_text_spoken_char_count(a_text)
                if spoken_chars < min_spoken_chars and max_continue_rounds > 0:
                    a_text, err = _extend_until_min(
                        qwen_bin=qwen_bin,
                        base_prompt=wrapped_prompt,
                        base_instruction=attempt_instruction if attempt_instruction else None,
                        model=qwen_model,
                        sandbox=bool(args.qwen_sandbox),
                        approval_mode=str(args.qwen_approval_mode) if args.qwen_approval_mode else None,
                        yolo=bool(args.qwen_yolo),
                        timeout_sec=int(args.timeout_sec),
                        logs_dir=logs_dir,
                        script_id=script_id,
                        a_text=a_text,
                        min_spoken_chars=min_spoken_chars,
                        target_chars_min=detected_min,
                        target_chars_max=detected_max,
                        max_continue_rounds=max_continue_rounds,
                    )
                    if err:
                        last_failure = err
                        continue
                    spoken_chars = _a_text_spoken_char_count(a_text)
                if spoken_chars < min_spoken_chars:
                    last_failure = (
                        f"{script_id}: rejected_output=too_short spoken_chars={spoken_chars} < min={min_spoken_chars} (see {attempt_stdout_log})"
                    )
                    continue

            a_text, removed_dupes = _dedupe_long_paragraphs(a_text)
            if removed_dupes:
                _write_text(
                    logs_dir / f"qwen_cli_dedupe_note__attempt{attempt:02d}.txt",
                    f"removed_duplicate_paragraphs={removed_dupes}\n",
                )
            if channel in {"CH28", "CH29"}:
                a_text, removed_near = _dedupe_near_paragraphs_story(a_text)
            else:
                a_text, removed_near = _dedupe_near_paragraphs(a_text)
            if removed_near:
                _write_text(
                    logs_dir / f"qwen_cli_dedupe_note__attempt{attempt:02d}__near.txt",
                    f"removed_near_duplicate_paragraphs={removed_near}\n",
                )
            a_text, removed_tail = _dedupe_tail_exact_paragraphs(a_text)
            if removed_tail:
                _write_text(
                    logs_dir / f"qwen_cli_dedupe_note__attempt{attempt:02d}__tail.txt",
                    f"removed_tail_duplicate_paragraphs={removed_tail}\n",
                )

            if channel in {"CH28", "CH29"}:
                a_text, removed_tj = _strip_time_jump_paragraphs(a_text)
                if removed_tj:
                    _write_text(
                        logs_dir / f"qwen_cli_timejump_stripped__attempt{attempt:02d}.txt",
                        f"removed_time_jump_paragraphs={removed_tj}\n",
                    )
                a_text, removed_sleep = _strip_sleep_paragraphs(a_text)
                if removed_sleep:
                    _write_text(
                        logs_dir / f"qwen_cli_sleep_stripped__attempt{attempt:02d}.txt",
                        f"removed_sleep_paragraphs={removed_sleep}\n",
                    )

            if min_spoken_chars > 0 and (not bool(args.allow_short)):
                spoken_chars = _a_text_spoken_char_count(a_text)
                if spoken_chars < min_spoken_chars and max_continue_rounds > 0:
                    a_text, err = _extend_until_min(
                        qwen_bin=qwen_bin,
                        base_prompt=wrapped_prompt,
                        base_instruction=attempt_instruction if attempt_instruction else None,
                        model=qwen_model,
                        sandbox=bool(args.qwen_sandbox),
                        approval_mode=str(args.qwen_approval_mode) if args.qwen_approval_mode else None,
                        yolo=bool(args.qwen_yolo),
                        timeout_sec=int(args.timeout_sec),
                        logs_dir=logs_dir,
                        script_id=script_id,
                        a_text=a_text,
                        min_spoken_chars=min_spoken_chars,
                        target_chars_min=detected_min,
                        target_chars_max=detected_max,
                        max_continue_rounds=max_continue_rounds,
                    )
                    if err:
                        last_failure = err
                        continue

                    a_text, removed_dupes2 = _dedupe_long_paragraphs(a_text)
                    if removed_dupes2:
                        _write_text(
                            logs_dir / f"qwen_cli_dedupe_note__attempt{attempt:02d}__post_continue.txt",
                            f"removed_duplicate_paragraphs={removed_dupes2}\n",
                        )
                    if channel in {"CH28", "CH29"}:
                        a_text, removed_near2 = _dedupe_near_paragraphs_story(a_text)
                    else:
                        a_text, removed_near2 = _dedupe_near_paragraphs(a_text)
                    if removed_near2:
                        _write_text(
                            logs_dir / f"qwen_cli_dedupe_note__attempt{attempt:02d}__post_continue__near.txt",
                            f"removed_near_duplicate_paragraphs={removed_near2}\n",
                        )
                    a_text, removed_tail2 = _dedupe_tail_exact_paragraphs(a_text)
                    if removed_tail2:
                        _write_text(
                            logs_dir / f"qwen_cli_dedupe_note__attempt{attempt:02d}__post_continue__tail.txt",
                            f"removed_tail_duplicate_paragraphs={removed_tail2}\n",
                        )
                    if channel in {"CH28", "CH29"}:
                        a_text, removed_tj2 = _strip_time_jump_paragraphs(a_text)
                        if removed_tj2:
                            _write_text(
                                logs_dir / f"qwen_cli_timejump_stripped__attempt{attempt:02d}__post_continue.txt",
                                f"removed_time_jump_paragraphs={removed_tj2}\n",
                            )
                        a_text, removed_sleep2 = _strip_sleep_paragraphs(a_text)
                        if removed_sleep2:
                            _write_text(
                                logs_dir / f"qwen_cli_sleep_stripped__attempt{attempt:02d}__post_continue.txt",
                                f"removed_sleep_paragraphs={removed_sleep2}\n",
                            )
                    spoken_chars = _a_text_spoken_char_count(a_text)

                if spoken_chars < min_spoken_chars:
                    last_failure = (
                        f"{script_id}: rejected_output=too_short_after_dedupe spoken_chars={spoken_chars} < min={min_spoken_chars} "
                        f"(see {attempt_stdout_log})"
                    )
                    continue

            issues, _stats = validate_a_text(a_text, {"assembled_path": str(mirror_path)})
            hard_errors = [it for it in issues if isinstance(it, dict) and str(it.get("severity") or "") == "error"]
            if hard_errors:
                codes = ", ".join(sorted({str(it.get("code") or "") for it in hard_errors if it.get("code")}))
                last_failure = (
                    f"{script_id}: rejected_output=script_validation_error codes=[{codes}] (see {attempt_stdout_log})"
                )
                continue

            backup_human = _backup_if_diff(out_path, a_text)
            backup_mirror = _backup_if_diff(mirror_path, a_text)
            _write_text(out_path, a_text)
            _write_text(mirror_path, a_text)
            backup_note = ""
            if backup_human:
                backup_note = f" (backup: {backup_human.name})"
            elif backup_mirror:
                backup_note = f" (backup: {backup_mirror.name})"
            print(f"[OK] {script_id} -> {out_path} + {mirror_path}{backup_note}")
            last_failure = None
            break

        if last_failure:
            failures.append(last_failure)

    if failures:
        print("[ERROR] Some items failed:", file=sys.stderr)
        for msg in failures:
            print(f"- {msg}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qwen_cli_generate_scripts_full_prompt.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="generate A-text via qwen CLI (dry-run by default)")
    sp.add_argument("--channel", required=True, help="e.g. CH28")
    mg = sp.add_mutually_exclusive_group(required=True)
    mg.add_argument("--video", help="e.g. 002")
    mg.add_argument("--videos", help="e.g. 001-030 or 1,2,3")
    sp.add_argument("--run", action="store_true", help="Execute qwen and write assembled_human.md (default: dry-run)")

    sp.add_argument("--instruction", default="", help="Optional operator instruction appended to the prompt")
    sp.add_argument(
        "--min-spoken-chars",
        type=int,
        default=0,
        help="Reject overwrite if output spoken chars is below this minimum (0=auto-detect from prompt target_chars_min)",
    )
    sp.add_argument(
        "--allow-short",
        action="store_true",
        help="Allow overwriting even if output is below target_chars_min / --min-spoken-chars (not recommended)",
    )
    sp.add_argument("--max-attempts", type=int, default=5, help="Max attempts per episode (default: 5)")
    sp.add_argument(
        "--max-continue-rounds",
        type=int,
        default=3,
        help="If output is too short, ask qwen to continue up to N rounds (default: 3). Set 0 to disable.",
    )

    sp.add_argument("--qwen-bin", default="", help="Explicit qwen binary path (optional)")
    sp.add_argument("--qwen-model", default="", help=argparse.SUPPRESS)
    sp.add_argument(
        "--qwen-sandbox",
        default=True,
        action=argparse.BooleanOptionalAction,
        help="Run qwen CLI with --sandbox (default: true)",
    )
    sp.add_argument(
        "--qwen-approval-mode",
        choices=["default", "plan", "auto-edit", "yolo"],
        default="",
        help="Qwen CLI approval mode (avoid 'plan' for long-form text generation)",
    )
    sp.add_argument("--qwen-yolo", action="store_true", help="Legacy: pass qwen --yolo (ignored if --qwen-approval-mode is set)")

    sp.add_argument("--timeout-sec", type=int, default=1800, help="Timeout seconds per episode (default: 1800)")
    sp.set_defaults(func=cmd_run)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
