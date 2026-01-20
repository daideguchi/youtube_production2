#!/usr/bin/env python3
from __future__ import annotations

"""
claude_cli_generate_scripts_full_prompt.py — Claude Code CLI (claude) script writer helper (manual/opt-in)

Purpose:
- Generate/overwrite A-text SoT using external `claude` CLI (no script_pipeline LLM routing).
- Read the Git-tracked FULL prompt files:
    prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md
- Write A-text SoT to:
    workspaces/scripts/{CH}/{NNN}/content/assembled_human.md
- Mirror to:
    workspaces/scripts/{CH}/{NNN}/content/assembled.md

Policy (SSOT):
- Default CLI backend: Claude (model: Sonnet 4.5).
- Opus is allowed only when explicitly instructed (pass --claude-model opus).
- If Claude is rate-limited/unavailable: fallback to Gemini 3 Flash Preview, then to qwen.

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
    # Run claude from a scratch directory so it won't treat the whole repo as an implicit "workspace context".
    return repo_paths.workspace_root() / "_scratch" / "claude_cli"


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
    # Placeholder emitted by script_pipeline runner (deterministic generator).
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
    """
    Blueprint gate (Codex responsibility):
    - topic_research: research_brief + references + search_results (non-placeholder)
    - script_outline: outline.md (non-placeholder)
    - script_master_plan: master_plan.json exists (schema optional)
    """
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


def _find_claude_bin(explicit: str | None) -> str:
    if explicit:
        p = Path(str(explicit)).expanduser()
        if p.exists():
            return str(p)
        raise SystemExit(f"claude not found at --claude-bin: {explicit}")
    found = shutil.which("claude")
    if found:
        return found
    raise SystemExit("claude CLI not found. Install `claude` and ensure it is on PATH.")


_CLAUDE_ALLOWED_ALIASES = {"sonnet", "opus"}


def _validate_claude_model(raw: str | None) -> str:
    """
    Policy:
    - Default: sonnet (Sonnet 4.5).
    - Allow only sonnet/opus aliases, or explicit 4.5 pinned names.
    """
    s = str(raw or "").strip()
    if not s:
        return "sonnet"
    low = s.lower()
    if low in _CLAUDE_ALLOWED_ALIASES:
        return low
    if re.fullmatch(r"claude-(sonnet|opus)-4-5-\d{8}", low):
        return s
    raise SystemExit(
        "\n".join(
            [
                "[POLICY] Forbidden --claude-model (unsupported).",
                f"- got: {s}",
                "- allowed: sonnet | opus | claude-sonnet-4-5-YYYYMMDD | claude-opus-4-5-YYYYMMDD",
                "- note: default is sonnet (4.5). Use opus only when explicitly instructed.",
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


def _strip_code_fences(text: str) -> str:
    s = (text or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z0-9_-]*\n?", "", s).strip()
        if s.endswith("```"):
            s = s[: -3].rstrip()
    return s + ("\n" if s else "")


def _reject_obviously_non_script(text: str) -> Optional[str]:
    stripped = (text or "").lstrip()
    if not stripped:
        return "empty_output"
    if stripped.startswith("[NEEDS_INPUT]"):
        return "needs_input"
    head = stripped[:600]
    bad_markers = (
        "Let me",
        "Sure",
        "Here's",
        "Here is",
        "I can",
        "I will",
        "As an AI",
        "I’m",
        "I'm",
        "You should",
        "We should",
        "I think",
        "以下に",
        "以下は",
        "了解",
        "承知",
        "では",
        "まず",
        "結論",
        "要約",
        "ポイント",
        "箇条書き",
        "見出し",
        "###",
        "- ",
    )
    for m in bad_markers:
        if m and m in head:
            return f"non_script_marker:{m}"
    return None


_TIMEJUMP_FILLER_MARKERS = (
    "数日後",
    "数週間",
    "数ヶ月",
    "数年後",
    "翌週",
    "翌月",
    "それから数",
)


def _find_timejump_filler_marker(text: str) -> str | None:
    compact = re.sub(r"\s+", "", _normalize_newlines(text))
    for marker in _TIMEJUMP_FILLER_MARKERS:
        if marker in compact:
            return marker
    return None


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


def _dedupe_long_paragraphs(text: str, *, min_chars: int = 120) -> tuple[str, int]:
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
        "時間ジャンプ（数日後/数週間/数ヶ月/数年後/翌週/翌月/それから数…）で水増ししない。"
        "結末/再定義/一手/余韻を二重に書かない（同じ締めを繰り返さない）。"
        f"今から書く追加分は必ず {lo}〜{hi} 字{total_range}。"
        "最後は物語として完結し、句点などで確実に閉じてください。"
    )


def _run_claude_cli(
    *,
    claude_bin: str,
    prompt: str,
    model: str,
    timeout_sec: int,
) -> tuple[int, str, str, float]:
    cmd: List[str] = [
        claude_bin,
        "-p",
        "--output-format",
        "text",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--tools",
        "",
    ]
    if model:
        cmd += ["--model", str(model)]

    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")

    start = time.time()
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


def _extend_until_min(
    *,
    claude_bin: str,
    model: str,
    base_prompt: str,
    base_instruction: str | None,
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

        need_min = int(min_spoken_chars) - spoken
        if target_chars_max is not None and target_chars_max > 0:
            need_max = max(need_min, int(target_chars_max) - spoken)
        else:
            need_max = need_min + 1800

        instruction_parts: List[str] = []
        if base_instruction:
            instruction_parts.append(str(base_instruction).strip())
        instruction_parts.append(
            _continue_instruction(add_min=need_min, add_max=need_max, total_min=target_chars_min, total_max=target_chars_max)
        )
        cont_prompt = _build_prompt(
            base_prompt=base_prompt,
            instruction="\n\n".join([p for p in instruction_parts if p]).strip(),
            current_a_text=combined,
        )
        cont_prompt_log = logs_dir / f"claude_cli_prompt__cont{cont:02d}.txt"
        cont_stdout_log = logs_dir / f"claude_cli_stdout__cont{cont:02d}.txt"
        cont_stderr_log = logs_dir / f"claude_cli_stderr__cont{cont:02d}.txt"
        _write_text(cont_prompt_log, cont_prompt)

        rc, stdout, stderr, _elapsed = _run_claude_cli(
            claude_bin=claude_bin,
            prompt=cont_prompt,
            model=model,
            timeout_sec=timeout_sec,
        )
        _write_text(cont_stdout_log, stdout)
        _write_text(cont_stderr_log, stderr)
        if rc != 0:
            return combined, f"{script_id}: claude_exit={rc} (see {cont_stderr_log})"

        chunk = _strip_code_fences(_normalize_newlines(stdout).rstrip())
        reject_reason = _reject_obviously_non_script(chunk)
        if reject_reason:
            return combined, f"{script_id}: rejected_output={reject_reason} (see {cont_stdout_log})"

        cleaned = _strip_edge_pause_lines(chunk)
        cleaned_compact = cleaned.strip()
        if not cleaned_compact:
            return combined, f"{script_id}: rejected_output=empty_continuation (see {cont_stdout_log})"
        if len(cleaned_compact) >= 200 and cleaned_compact in combined:
            continue

        combined = combined.rstrip() + "\n\n" + cleaned
        combined = combined.rstrip() + "\n"

    return (
        combined,
        f"{script_id}: rejected_output=too_short_after_continuations min={min_spoken_chars} spoken={_a_text_spoken_char_count(combined)}",
    )


_LIMIT_PATTERNS = [
    # Rate/usage limits
    r"\brate[- ]?limit\b",
    r"\busage limit\b",
    r"\bquota\b",
    r"\bresource_exhausted\b",
    r"\btoo many requests\b",
    r"\b429\b",
    r"\boverloaded\b",
    r"\bcapacity\b",
    r"\bexceeded\b",
    r"\breached\b.*\blimit\b",
    r"\blimit\b.*\breached\b",
    r"\breached\b.*\bquota\b",
    r"\bquota\b.*\breached\b",
    # Auth / setup issues (treat as "unavailable" for fallback chain)
    r"\bnot authenticated\b",
    r"\bunauthorized\b",
    r"\bforbidden\b",
    r"\b401\b",
    r"\b403\b",
    r"\bapi key\b",
    r"\bsetup-token\b",
    # Missing CLI/tooling
    r"\bnot found\b",
]


def _looks_like_limit_error(text: str) -> bool:
    s = str(text or "")
    compact = s.lower()
    for pat in _LIMIT_PATTERNS:
        if re.search(pat, compact, flags=re.IGNORECASE):
            return True
    return False


def _run_fallback_gemini(args: argparse.Namespace, *, channel: str, video: str) -> tuple[int, str, str]:
    cmd: List[str] = [
        "python3",
        "scripts/ops/gemini_cli_generate_scripts.py",
        "run",
        "--channel",
        channel,
        "--video",
        video,
        "--gemini-model",
        "gemini-3-flash-preview",
    ]
    if bool(args.run):
        cmd.append("--run")
    instruction = str(getattr(args, "instruction", "") or "").strip()
    if instruction:
        cmd += ["--instruction", instruction]
    if int(getattr(args, "min_spoken_chars", 0) or 0) > 0:
        cmd += ["--min-spoken-chars", str(int(args.min_spoken_chars))]
    if bool(getattr(args, "allow_short", False)):
        cmd.append("--allow-short")
    cmd += ["--max-attempts", str(int(getattr(args, "max_attempts", 5) or 5))]
    cmd += ["--max-continue-rounds", str(int(getattr(args, "max_continue_rounds", 3) or 3))]
    cmd += ["--timeout-sec", str(int(getattr(args, "timeout_sec", 1800) or 1800))]

    proc = subprocess.run(cmd, text=True, capture_output=True, cwd=str(repo_paths.repo_root()))
    return int(proc.returncode), str(proc.stdout or ""), str(proc.stderr or "")


def _run_fallback_qwen(args: argparse.Namespace, *, channel: str, video: str) -> tuple[int, str, str]:
    cmd: List[str] = [
        "python3",
        "scripts/ops/qwen_cli_generate_scripts_full_prompt.py",
        "run",
        "--channel",
        channel,
        "--video",
        video,
    ]
    if bool(args.run):
        cmd.append("--run")
    instruction = str(getattr(args, "instruction", "") or "").strip()
    if instruction:
        cmd += ["--instruction", instruction]
    if int(getattr(args, "min_spoken_chars", 0) or 0) > 0:
        cmd += ["--min-spoken-chars", str(int(args.min_spoken_chars))]
    if bool(getattr(args, "allow_short", False)):
        cmd.append("--allow-short")
    cmd += ["--max-attempts", str(int(getattr(args, "max_attempts", 5) or 5))]
    cmd += ["--max-continue-rounds", str(int(getattr(args, "max_continue_rounds", 3) or 3))]
    cmd += ["--timeout-sec", str(int(getattr(args, "timeout_sec", 1800) or 1800))]

    proc = subprocess.run(cmd, text=True, capture_output=True, cwd=str(repo_paths.repo_root()))
    return int(proc.returncode), str(proc.stdout or ""), str(proc.stderr or "")


def _handle_fallback(
    *,
    args: argparse.Namespace,
    backend: str,
    channel: str,
    video: str,
    reason: str,
) -> tuple[bool, str]:
    """
    Returns (ok, final_backend).
    """
    script_id = f"{channel}-{video}"
    if backend == "claude":
        print(f"[FALLBACK] {script_id}: Claude unavailable ({reason}) -> Gemini 3 Flash Preview", file=sys.stderr)
        rc, _out, err = _run_fallback_gemini(args, channel=channel, video=video)
        if rc == 0:
            return True, "gemini"
        if _looks_like_limit_error(err):
            print(f"[FALLBACK] {script_id}: Gemini quota/limit -> qwen", file=sys.stderr)
            rc2, _out2, _err2 = _run_fallback_qwen(args, channel=channel, video=video)
            return (rc2 == 0), "qwen"
        return False, "gemini"

    if backend == "gemini":
        print(f"[FALLBACK] {script_id}: Gemini unavailable ({reason}) -> qwen", file=sys.stderr)
        rc2, _out2, _err2 = _run_fallback_qwen(args, channel=channel, video=video)
        return (rc2 == 0), "qwen"

    return False, backend


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

    claude_bin = _find_claude_bin(args.claude_bin)
    claude_model = _validate_claude_model(args.claude_model)
    _ensure_dir(_scratch_dir())

    failures: List[str] = []
    backend: str = "claude"

    for vv in videos:
        script_id = f"{channel}-{vv}"
        prompt_path = _prompt_path(channel, vv)
        out_path = _output_a_text_path(channel, vv)
        mirror_path = out_path.with_name("assembled.md")
        logs_dir = _logs_dir(channel, vv)

        if not prompt_path.exists():
            raise SystemExit(f"Prompt not found: {prompt_path} ({script_id})")

        base_prompt = _read_text(prompt_path)
        ok_blueprint, blueprint_payload = _ensure_blueprint_ready(channel=channel, video=vv, require=bool(args.run))
        if ok_blueprint and blueprint_payload:
            base_prompt = (base_prompt.rstrip() + "\n\n" + blueprint_payload.strip()).strip() + "\n"
        detected_min = _parse_target_chars_min(base_prompt)
        detected_max = _parse_target_chars_max(base_prompt)

        min_spoken_chars = int(args.min_spoken_chars or 0)
        if min_spoken_chars <= 0 and detected_min:
            min_spoken_chars = int(detected_min)

        if not args.run:
            print(f"[DRY-RUN] {script_id}")
            print(f"- claude: {claude_bin}")
            print(f"- model: {claude_model}")
            print(f"- fallback: Claude -> Gemini(gemini-3-flash-preview) -> qwen")
            print(f"- prompt: {prompt_path}")
            if ok_blueprint:
                print(f"- blueprint: OK")
            else:
                print(f"- blueprint: MISSING (run: ./ops script resume -- --channel {channel} --video {vv} --until script_master_plan --max-iter 6)")
            print(f"- output: {out_path}")
            print("")
            continue

        _ensure_dir(logs_dir)
        prompt_log = logs_dir / "claude_cli_prompt.txt"
        stdout_log = logs_dir / "claude_cli_stdout.txt"
        stderr_log = logs_dir / "claude_cli_stderr.txt"
        meta_log = logs_dir / "claude_cli_meta.json"

        max_attempts = max(1, int(args.max_attempts))
        max_continue_rounds = max(0, int(args.max_continue_rounds))
        last_failure: Optional[str] = None

        while True:
            if backend == "claude":
                for attempt in range(1, max_attempts + 1):
                    retry_hint = ""
                    if attempt > 1:
                        retry_hint = (
                            "再試行: 直前の出力が不合格。本文のみを出力し、ルール説明/見出し/箇条書き/番号リスト/マーカー文字列/段落重複を絶対に出さない。"
                        )
                    attempt_instruction = str(args.instruction or "").strip()
                    if retry_hint:
                        attempt_instruction = (
                            (attempt_instruction + "\n\n" + retry_hint).strip() if attempt_instruction else retry_hint
                        )
                    if attempt_instruction:
                        attempt_instruction = (attempt_instruction + f"\nretry_attempt: {attempt}").strip()

                    attempt_prompt = _build_prompt(
                        base_prompt=base_prompt,
                        instruction=attempt_instruction if attempt_instruction else None,
                        current_a_text=None,
                    )

                    attempt_prompt_log = logs_dir / f"claude_cli_prompt__attempt{attempt:02d}.txt"
                    attempt_stdout_log = logs_dir / f"claude_cli_stdout__attempt{attempt:02d}.txt"
                    attempt_stderr_log = logs_dir / f"claude_cli_stderr__attempt{attempt:02d}.txt"
                    attempt_meta_log = logs_dir / f"claude_cli_meta__attempt{attempt:02d}.json"
                    _write_text(prompt_log, attempt_prompt)
                    _write_text(attempt_prompt_log, attempt_prompt)

                    rc, stdout, stderr, elapsed = _run_claude_cli(
                        claude_bin=claude_bin,
                        prompt=attempt_prompt,
                        model=claude_model,
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
                            "tool": "claude_cli_generate_scripts_full_prompt",
                            "at": _utc_now_iso(),
                            "script_id": script_id,
                            "prompt_path": str(prompt_path),
                            "output_path": str(out_path),
                            "claude_bin": claude_bin,
                            "claude_model": claude_model,
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
                            "tool": "claude_cli_generate_scripts_full_prompt",
                            "at": _utc_now_iso(),
                            "script_id": script_id,
                            "prompt_path": str(prompt_path),
                            "output_path": str(out_path),
                            "claude_bin": claude_bin,
                            "claude_model": claude_model,
                            "timeout_sec": int(args.timeout_sec),
                            "elapsed_sec": elapsed,
                            "exit_code": rc,
                            "attempt": attempt,
                        },
                    )

                    if rc != 0:
                        if _looks_like_limit_error(stderr) or _looks_like_limit_error(stdout):
                            ok, backend = _handle_fallback(
                                args=args, backend="claude", channel=channel, video=vv, reason="rate_limit_or_unavailable"
                            )
                            if ok:
                                last_failure = None
                                break
                            last_failure = f"{script_id}: claude_unavailable (see {attempt_stderr_log})"
                            break
                        last_failure = f"{script_id}: claude_exit={rc} (see {attempt_stderr_log})"
                        continue

                    a_text = _strip_code_fences(_normalize_newlines(stdout).rstrip())
                    a_text = a_text.rstrip() + "\n" if a_text else ""
                    reject_reason = _reject_obviously_non_script(a_text)
                    if reject_reason:
                        last_failure = f"{script_id}: rejected_output={reject_reason} (see {attempt_stdout_log})"
                        continue

                    if min_spoken_chars > 0 and not bool(args.allow_short):
                        spoken_chars = _a_text_spoken_char_count(a_text)
                        if spoken_chars < min_spoken_chars and max_continue_rounds > 0:
                            a_text, err = _extend_until_min(
                                claude_bin=claude_bin,
                                model=claude_model,
                                base_prompt=base_prompt,
                                base_instruction=attempt_instruction if attempt_instruction else None,
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
                                f"{script_id}: rejected_output=too_short spoken_chars={spoken_chars} < min={min_spoken_chars} "
                                f"(see {attempt_stdout_log})"
                            )
                            continue

                    a_text = _strip_edge_pause_lines(a_text)

                    a_text, removed_dupes = _dedupe_long_paragraphs(a_text)
                    if removed_dupes:
                        _write_text(
                            logs_dir / f"claude_cli_dedupe_note__attempt{attempt:02d}.txt",
                            f"removed_duplicate_paragraphs={removed_dupes}\n",
                        )
                    a_text, removed_near = _dedupe_near_paragraphs(a_text)
                    if removed_near:
                        _write_text(
                            logs_dir / f"claude_cli_dedupe_note__attempt{attempt:02d}__near.txt",
                            f"removed_near_duplicate_paragraphs={removed_near}\n",
                        )

                    marker = _find_timejump_filler_marker(a_text)
                    if marker:
                        last_failure = f"{script_id}: rejected_output=timejump_filler marker={marker} (see {attempt_stdout_log})"
                        continue

                    issues, _stats = validate_a_text(a_text, {"assembled_path": str(mirror_path)})
                    hard_errors = [it for it in issues if isinstance(it, dict) and str(it.get("severity") or "") == "error"]
                    if hard_errors:
                        codes = ", ".join(sorted({str(it.get("code") or "") for it in hard_errors if it.get("code")}))
                        last_failure = f"{script_id}: rejected_output=script_validation_error codes=[{codes}] (see {attempt_stdout_log})"
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

                if last_failure is None:
                    break
                failures.append(last_failure)
                break

            if backend == "gemini":
                rc, _out, err = _run_fallback_gemini(args, channel=channel, video=vv)
                if rc == 0:
                    last_failure = None
                    break
                if _looks_like_limit_error(err):
                    ok, backend = _handle_fallback(args=args, backend="gemini", channel=channel, video=vv, reason="rate_limit_or_unavailable")
                    if ok:
                        last_failure = None
                        break
                failures.append(f"{script_id}: gemini_failed (see episode logs)")
                break

            if backend == "qwen":
                rc, _out, _err = _run_fallback_qwen(args, channel=channel, video=vv)
                if rc == 0:
                    last_failure = None
                    break
                failures.append(f"{script_id}: qwen_failed (see episode logs)")
                break

            failures.append(f"{script_id}: unknown_backend={backend}")
            break

    if failures:
        print("[ERROR] Some items failed:", file=sys.stderr)
        for msg in failures:
            print(f"- {msg}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claude_cli_generate_scripts_full_prompt.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="generate A-text via claude CLI (dry-run by default)")
    sp.add_argument("--channel", required=True, help="e.g. CH28")
    mg = sp.add_mutually_exclusive_group(required=True)
    mg.add_argument("--video", help="e.g. 002")
    mg.add_argument("--videos", help="e.g. 001-030 or 1,2,3")
    sp.add_argument("--run", action="store_true", help="Execute claude and write assembled_human.md (default: dry-run)")

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
        help="If output is too short, ask Claude to continue up to N rounds (default: 3). Set 0 to disable.",
    )

    sp.add_argument("--claude-bin", default="", help="Explicit claude binary path (optional)")
    sp.add_argument(
        "--claude-model",
        default="sonnet",
        help="Claude model alias/name (default: sonnet). Use opus only when explicitly instructed.",
    )
    sp.add_argument("--timeout-sec", type=int, default=1800, help="Timeout seconds per episode (default: 1800)")
    sp.set_defaults(func=cmd_run)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
