#!/usr/bin/env python3
from __future__ import annotations

"""
gemini_cli_generate_scripts.py — Gemini CLI (non-batch) script writer helper (manual/opt-in)

Purpose:
- Provide an explicit, operator-invoked route to generate/patch A-text via `gemini` CLI.
- Keep it safe-by-default (dry-run unless --run).
- Write A-text SoT to: workspaces/scripts/{CH}/{NNN}/content/assembled_human.md
- Mirror to: workspaces/scripts/{CH}/{NNN}/content/assembled.md

Notes:
- This is NOT a silent fallback for script_pipeline. Operators must invoke it explicitly.
- Prompt source is the Git-tracked antigravity prompt files:
    prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    """
    Count "spoken" characters, matching script_pipeline.validate_a_text intent:
    - exclude pause-only lines (`---`)
    - exclude whitespace/newlines
    """
    normalized = _normalize_newlines(text)
    lines: List[str] = []
    for line in normalized.split("\n"):
        if line.strip() == "---":
            continue
        lines.append(line)
    compact = "".join(lines)
    compact = compact.replace(" ", "").replace("\t", "").replace("\u3000", "")
    return len(compact.strip())


_SLEEP_GUARD_TAG_MARKERS = ("#睡眠用", "#寝落ち")


def _channel_opted_in_sleep_framing(channel: str) -> bool:
    """
    SSOT: sleep-framing is opt-in per channel.
    Treat a channel as sleep-allowed only when its channel_info explicitly includes
    '#睡眠用' or '#寝落ち' in youtube_description/default_tags.
    """
    ch = str(channel or "").strip().upper()
    if not re.fullmatch(r"CH\d{2}", ch):
        return False
    root = repo_paths.repo_root() / "packages" / "script_pipeline" / "channels"
    info_path: Optional[Path] = None
    try:
        for p in root.glob(f"{ch}-*/channel_info.json"):
            info_path = p
            break
    except Exception:
        info_path = None
    if info_path is None or not info_path.exists():
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
    return any(tok in blob for tok in _SLEEP_GUARD_TAG_MARKERS)


def _sleep_framing_issue(*, a_text: str, assembled_path: Path) -> Optional[Dict[str, Any]]:
    # Deterministic check (no LLM): reuse script_pipeline.validate_a_text SSOT guard.
    issues, _stats = validate_a_text(str(a_text or ""), {"assembled_path": str(assembled_path)})
    for it in issues:
        if isinstance(it, dict) and str(it.get("code") or "") == "sleep_framing_contamination":
            return it
    return None


def _sleep_guard_instruction() -> str:
    # Keep this short; the prompt file already carries most rules.
    return (
        "重要: この台本は睡眠用ではない。視聴者を眠らせる目的の呼びかけ・使い方の提示は禁止。"
        "末尾は物語として完結させ、睡眠/寝落ち/安眠/布団/おやすみ/ゆっくりお休み 等の誘導で締めない。"
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


def _parse_chapter_count(prompt: str) -> Optional[int]:
    m = re.search(r"\bchapter_count\s*:\s*(\d{1,3})\b", str(prompt or ""), flags=re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _strip_edge_pause_lines(text: str) -> str:
    """
    Normalize a partial A-text chunk for safe concatenation:
    - remove leading/trailing blank lines
    - remove leading/trailing pause-only lines (`---`) (including blanks around them)
    """
    normalized = _normalize_newlines(text)
    lines = normalized.split("\n")
    # leading blanks
    while lines and not lines[0].strip():
        lines.pop(0)
    # leading pauses (allow blanks between)
    changed = True
    while changed:
        changed = False
        while lines and not lines[0].strip():
            lines.pop(0)
            changed = True
        if lines and lines[0].strip() == "---":
            lines.pop(0)
            changed = True
    # trailing blanks
    while lines and not lines[-1].strip():
        lines.pop()
    # trailing pauses (allow blanks between)
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


def _continue_instruction(*, add_min: int, add_max: int, total_min: int | None, total_max: int | None) -> str:
    lo = int(max(0, add_min))
    hi = int(max(lo, add_max))
    total_range = ""
    if total_min is not None and total_max is not None and total_min > 0 and total_max > 0:
        total_range = f"（全体は必ず {int(total_min)}〜{int(total_max)} 字）"
    return (
        "指示: <<<CURRENT_A_TEXT_START>>> の直後から、自然につながる『続きだけ』を書いてください。"
        "要約・言い換え連打・前文の繰り返しは禁止。"
        f"今から書く追加分は必ず {lo}〜{hi} 字{total_range}。"
        "最後は物語として完結し、句点などで確実に閉じてください。"
    )


def _extend_until_min(
    *,
    gemini_bin: str,
    base_prompt: str,
    base_instruction: str | None,
    model: str | None,
    sandbox: bool,
    approval_mode: str | None,
    yolo: bool,
    home_dir: Path | None,
    timeout_sec: int,
    logs_dir: Path,
    script_id: str,
    a_text: str,
    min_spoken_chars: int,
    target_chars_min: int | None,
    target_chars_max: int | None,
    max_continue_rounds: int,
) -> tuple[str, Optional[str]]:
    """
    Extend an A-text by asking gemini CLI to continue from CURRENT_A_TEXT.
    Returns (extended_text, error_reason).
    """
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
            include_current=True,
            current_a_text=combined,
        )

        cont_prompt_log = logs_dir / f"gemini_cli_prompt__cont{cont:02d}.txt"
        cont_stdout_log = logs_dir / f"gemini_cli_stdout__cont{cont:02d}.txt"
        cont_stderr_log = logs_dir / f"gemini_cli_stderr__cont{cont:02d}.txt"
        _write_text(cont_prompt_log, cont_prompt)

        rc, stdout, stderr, _elapsed = _run_gemini_cli(
            gemini_bin=gemini_bin,
            prompt=cont_prompt,
            model=model,
            sandbox=sandbox,
            approval_mode=approval_mode,
            yolo=yolo,
            home_dir=home_dir,
            timeout_sec=timeout_sec,
        )
        _write_text(cont_stdout_log, stdout)
        _write_text(cont_stderr_log, stderr)
        if rc != 0:
            return combined, f"{script_id}: gemini_exit={rc} (see {cont_stderr_log})"

        chunk = _normalize_newlines(stdout).rstrip() + "\n"
        reject_reason = _reject_obviously_non_script(chunk)
        if reject_reason:
            return combined, f"{script_id}: rejected_output={reject_reason} (see {cont_stdout_log})"

        cleaned = _strip_edge_pause_lines(chunk)
        if not cleaned.strip():
            return combined, f"{script_id}: rejected_output=empty_continuation (see {cont_stdout_log})"
        cleaned_compact = cleaned.strip()
        # If the model repeats a large chunk verbatim, do not append; try the next continuation round.
        if len(cleaned_compact) >= 200 and cleaned_compact in combined:
            continue

        combined = combined.rstrip() + "\n\n" + cleaned
        combined = combined.rstrip() + "\n"

    return (
        combined,
        f"{script_id}: rejected_output=too_short_after_continuations min={min_spoken_chars} spoken={_a_text_spoken_char_count(combined)}",
    )


def _parse_section_splits(expr: str) -> List[tuple[int, int]]:
    raw = str(expr or "").strip()
    if not raw:
        return []
    out: List[tuple[int, int]] = []
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        if "-" in t:
            a, b = [x.strip() for x in t.split("-", 1)]
            lo = int(a)
            hi = int(b)
            if hi < lo:
                lo, hi = hi, lo
            out.append((lo, hi))
        else:
            i = int(t)
            out.append((i, i))
    return out


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


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


def _truncate_for_prompt(text: str, *, max_chars: int) -> str:
    s = str(text or "")
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "\n\n[TRUNCATED]\n"


def _ensure_blueprint_ready(*, channel: str, video: str, require: bool) -> tuple[bool, str]:
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
        try:
            obj = json.loads(master_plan.read_text(encoding="utf-8"))
            if not isinstance(obj, dict):
                problems.append(f"master_plan.json invalid JSON: {master_plan}")
        except Exception:
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
        try:
            obj = json.loads(refs.read_text(encoding="utf-8"))
            if not isinstance(obj, list) or len(obj) == 0:
                problems.append(f"references.json empty/invalid: {refs}")
        except Exception:
            problems.append(f"references.json invalid JSON: {refs}")

    search = p["search_results"]
    if not search.exists():
        missing.append(str(search))
    else:
        try:
            so = json.loads(search.read_text(encoding="utf-8"))
        except Exception:
            so = None
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


def _prompt_path(channel: str, video: str) -> Path:
    ch = str(channel).strip().upper()
    vv = _z3(video)
    return repo_paths.repo_root() / "prompts" / "antigravity_gemini" / ch / f"{ch}_{vv}_FULL_PROMPT.md"


def _output_a_text_path(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "content" / "assembled_human.md"


def _logs_dir(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "logs"


def _scratch_dir() -> Path:
    # Run gemini CLI from a scratch directory to avoid scanning the whole repo as "workspace context".
    return repo_paths.workspace_root() / "_scratch" / "gemini_cli"


def _gemini_home_dir(channel: str, video: str) -> Path:
    ch = str(channel).strip().upper()
    vv = _z3(video)
    # Isolate gemini's global state (settings/tmp/history) per-episode to avoid cross-agent collisions
    # and to avoid mutating the user's real ~/.gemini settings.
    return _scratch_dir() / "home" / f"{ch}-{vv}"


def _ensure_gemini_settings(*, home_dir: Path, auth_type: str) -> Path:
    """
    Prepare an isolated HOME so gemini CLI can run non-interactively using GEMINI_API_KEY.

    gemini CLI stores global settings under: $HOME/.gemini/settings.json
    We write the minimal auth selection there.
    """
    gemini_dir = home_dir / ".gemini"
    _ensure_dir(gemini_dir)
    settings_path = gemini_dir / "settings.json"
    # Note: keep this file self-contained to avoid relying on the user's ~/.gemini config.
    # Provide a dedicated long-form alias suitable for A-text generation.
    payload: Dict[str, Any] = {
        "security": {"auth": {"selectedType": str(auth_type)}},
        "modelConfigs": {
            "customAliases": {
                # High maxOutputTokens + no thinking for long-form prose generation.
                "antigravity-script": {
                    "extends": "base",
                    "modelConfig": {
                        "model": "gemini-2.5-flash",
                        "generateContentConfig": {
                            "temperature": 0.9,
                            "topP": 0.95,
                            "topK": 64,
                            "maxOutputTokens": 24000,
                            "thinkingConfig": {"thinkingBudget": 0},
                        },
                    },
                }
            }
        },
    }
    settings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return settings_path


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    _ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _find_gemini_bin(explicit: str | None) -> str:
    if explicit:
        p = Path(str(explicit)).expanduser()
        if p.exists():
            return str(p)
        raise SystemExit(f"gemini not found at --gemini-bin: {explicit}")
    found = shutil.which("gemini")
    if found:
        return found
    raise SystemExit("gemini CLI not found. Install `gemini` and ensure it is on PATH.")


def _build_prompt(*, base_prompt: str, instruction: str | None, include_current: bool, current_a_text: str | None) -> str:
    parts: List[str] = [str(base_prompt or "").rstrip()]

    if include_current and current_a_text:
        parts.append("<<<CURRENT_A_TEXT_START>>>")
        parts.append(str(current_a_text).rstrip())
        parts.append("<<<CURRENT_A_TEXT_END>>>")

    if instruction:
        parts.append("<<<OPERATOR_INSTRUCTION_START>>>")
        parts.append(str(instruction).strip())
        parts.append("<<<OPERATOR_INSTRUCTION_END>>>")

    joined = "\n\n".join([p for p in parts if str(p).strip()]).strip()
    return joined + "\n"


def _read_current_a_text(channel: str, video: str) -> Optional[str]:
    content_dir = repo_paths.video_root(channel, video) / "content"
    human = content_dir / "assembled_human.md"
    mirror = content_dir / "assembled.md"
    path = human if human.exists() else mirror
    if not path.exists():
        return None
    try:
        return _read_text(path)
    except Exception:
        return None


def _run_gemini_cli(
    *,
    gemini_bin: str,
    prompt: str,
    model: str | None,
    sandbox: bool,
    approval_mode: str | None,
    yolo: bool,
    home_dir: Path | None,
    timeout_sec: int,
) -> tuple[int, str, str, float]:
    cmd: List[str] = [gemini_bin, "--output-format", "text"]
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
    if home_dir is not None:
        env["HOME"] = str(home_dir)

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


def _reject_obviously_non_script(text: str) -> Optional[str]:
    stripped = (text or "").lstrip()
    if not stripped:
        return "empty_output"
    if stripped.startswith("[NEEDS_INPUT]"):
        return "needs_input"
    return None


def cmd_run(args: argparse.Namespace) -> int:
    channel = str(args.channel or "").strip().upper()
    if not re.fullmatch(r"CH\d{2}", channel):
        raise SystemExit(f"Invalid --channel: {args.channel!r} (expected CHxx)")

    section_splits = _parse_section_splits(args.split_sections) if str(args.split_sections or "").strip() else []
    if section_splits and bool(args.include_current):
        raise SystemExit("--split-sections cannot be used with --include-current (concatenate parts instead).")

    videos: List[str] = []
    if args.video:
        videos = [_z3(args.video)]
    elif args.videos:
        videos = _parse_videos(args.videos)
    else:
        raise SystemExit("Specify --video NNN or --videos NNN-NNN")

    gemini_bin = _find_gemini_bin(args.gemini_bin)
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

        base_prompt = _read_text(prompt_path)
        ok_blueprint, blueprint_payload = _ensure_blueprint_ready(channel=channel, video=vv, require=bool(args.run))
        if ok_blueprint and blueprint_payload:
            base_prompt = (base_prompt.rstrip() + "\n\n" + blueprint_payload.strip()).strip() + "\n"
        current = _read_current_a_text(channel, vv) if bool(args.include_current) else None
        sleep_opt_in = _channel_opted_in_sleep_framing(channel)
        sleep_guard_enabled = (not sleep_opt_in) and (not bool(getattr(args, "allow_sleep_framing", False)))
        instruction = str(args.instruction or "").strip()
        if sleep_guard_enabled:
            instruction = (instruction + "\n\n" + _sleep_guard_instruction()).strip() if instruction else _sleep_guard_instruction()
        final_prompt = _build_prompt(
            base_prompt=base_prompt,
            instruction=instruction if instruction else None,
            include_current=bool(args.include_current),
            current_a_text=current,
        )

        home_dir: Path | None = None
        settings_path: Path | None = None
        if not bool(args.gemini_use_user_home):
            home_dir = _gemini_home_dir(channel, vv)
            settings_path = _ensure_gemini_settings(home_dir=home_dir, auth_type=str(args.gemini_auth_type))

        if not args.run:
            print(f"[DRY-RUN] {script_id}")
            print(f"- gemini: {gemini_bin}")
            if args.gemini_model:
                print(f"- model: {args.gemini_model}")
            if args.gemini_sandbox:
                print("- sandbox: true")
            if args.gemini_approval_mode:
                print(f"- approval_mode: {args.gemini_approval_mode}")
            elif args.gemini_yolo:
                print("- yolo: true")
            if home_dir is not None:
                print(f"- gemini_auth_type: {args.gemini_auth_type}")
                if settings_path is not None:
                    print(f"- gemini_settings: {settings_path}")
            print(f"- prompt: {prompt_path}")
            if ok_blueprint:
                print(f"- blueprint: OK")
            else:
                print(f"- blueprint: MISSING (run: ./ops script resume -- --channel {channel} --video {vv} --until script_master_plan --max-iter 6)")
            print(f"- output: {out_path}")
            if args.include_current:
                print("- include_current: true")
            if args.instruction:
                print("- instruction: (provided)")
            if sleep_guard_enabled:
                print("- sleep_guard: enabled (non-sleep channel)")
            print("")
            continue

        _ensure_dir(logs_dir)
        prompt_log = logs_dir / "gemini_cli_prompt.txt"
        stdout_log = logs_dir / "gemini_cli_stdout.txt"
        stderr_log = logs_dir / "gemini_cli_stderr.txt"
        meta_log = logs_dir / "gemini_cli_meta.json"

        _write_text(prompt_log, final_prompt)

        if section_splits:
            chapter_count = _parse_chapter_count(final_prompt)
            if chapter_count:
                for lo, hi in section_splits:
                    if lo < 1 or hi < 1 or lo > chapter_count or hi > chapter_count:
                        raise SystemExit(
                            f"--split-sections out of range: {lo}-{hi} (chapter_count={chapter_count} from prompt)"
                        )

            parts: List[str] = []
            for idx, (lo, hi) in enumerate(section_splits, start=1):
                part_suffix = f"part{idx:02d}"
                part_instruction = (
                    f"分割生成。全{chapter_count or 'N'}セクションのうち、"
                    f"セクション{lo}からセクション{hi}のみを本文として出力する。"
                    f"それ以外のセクションは一切書かない。"
                    "このパートの先頭と末尾に---は置かない。"
                    "セクション境界の区切りは---のみを最小限に使う。"
                )
                merged_instruction = (str(args.instruction or "").strip() + "\n\n" + part_instruction).strip()
                part_prompt = _build_prompt(
                    base_prompt=base_prompt,
                    instruction=merged_instruction,
                    include_current=False,
                    current_a_text=None,
                )
                part_prompt_log = logs_dir / f"gemini_cli_prompt_{part_suffix}.txt"
                part_stdout_log = logs_dir / f"gemini_cli_stdout_{part_suffix}.txt"
                part_stderr_log = logs_dir / f"gemini_cli_stderr_{part_suffix}.txt"
                part_meta_log = logs_dir / f"gemini_cli_meta_{part_suffix}.json"
                _write_text(part_prompt_log, part_prompt)

                rc, stdout, stderr, elapsed = _run_gemini_cli(
                    gemini_bin=gemini_bin,
                    prompt=part_prompt,
                    model=args.gemini_model,
                    sandbox=bool(args.gemini_sandbox),
                    approval_mode=str(args.gemini_approval_mode) if args.gemini_approval_mode else None,
                    yolo=bool(args.gemini_yolo),
                    home_dir=home_dir,
                    timeout_sec=int(args.timeout_sec),
                )
                _write_text(part_stdout_log, stdout)
                _write_text(part_stderr_log, stderr)
                _write_json(
                    part_meta_log,
                    {
                        "schema_version": 1,
                        "tool": "gemini_cli_generate_scripts",
                        "at": _utc_now_iso(),
                        "script_id": script_id,
                        "part": {"index": idx, "split": {"from": lo, "to": hi}},
                        "prompt_path": str(prompt_path),
                        "output_path": str(out_path),
                        "gemini_bin": gemini_bin,
                        "gemini_model": args.gemini_model,
                        "gemini_sandbox": bool(args.gemini_sandbox),
                        "gemini_approval_mode": args.gemini_approval_mode,
                        "gemini_yolo": bool(args.gemini_yolo),
                        "gemini_use_user_home": bool(args.gemini_use_user_home),
                        "gemini_auth_type": str(args.gemini_auth_type),
                        "gemini_home_dir": str(home_dir) if home_dir is not None else "",
                        "timeout_sec": int(args.timeout_sec),
                        "elapsed_sec": elapsed,
                        "exit_code": rc,
                    },
                )
                if rc != 0:
                    failures.append(f"{script_id}: gemini_exit={rc} (see {part_stderr_log})")
                    parts = []
                    break
                part_text = _normalize_newlines(stdout).rstrip() + "\n"
                reject_reason = _reject_obviously_non_script(part_text)
                if reject_reason:
                    failures.append(f"{script_id}: rejected_output={reject_reason} (see {part_stdout_log})")
                    parts = []
                    break
                parts.append(_strip_edge_pause_lines(part_text))

            if not parts:
                continue
            # Join parts with a single pause line between.
            joined = ""
            for i, part in enumerate(parts):
                if i == 0:
                    joined = part.rstrip() + "\n"
                    continue
                joined = joined.rstrip() + "\n\n---\n\n" + part.lstrip()
            a_text = joined.rstrip() + "\n"
            _write_text(stdout_log, a_text)
            _write_text(stderr_log, "")
            _write_json(
                meta_log,
                {
                    "schema_version": 1,
                    "tool": "gemini_cli_generate_scripts",
                    "at": _utc_now_iso(),
                    "script_id": script_id,
                    "multipart": {"enabled": True, "splits": [{"from": a, "to": b} for a, b in section_splits]},
                    "prompt_path": str(prompt_path),
                    "output_path": str(out_path),
                    "gemini_bin": gemini_bin,
                    "gemini_model": args.gemini_model,
                    "gemini_sandbox": bool(args.gemini_sandbox),
                    "gemini_approval_mode": args.gemini_approval_mode,
                    "gemini_yolo": bool(args.gemini_yolo),
                    "gemini_use_user_home": bool(args.gemini_use_user_home),
                    "gemini_auth_type": str(args.gemini_auth_type),
                    "gemini_home_dir": str(home_dir) if home_dir is not None else "",
                    "timeout_sec": int(args.timeout_sec),
                },
            )
        else:
            detected_min = _parse_target_chars_min(final_prompt)
            detected_max = _parse_target_chars_max(final_prompt)
            min_spoken_chars = int(args.min_spoken_chars or 0)
            if min_spoken_chars <= 0 and detected_min:
                min_spoken_chars = int(detected_min)

            max_attempts = max(1, int(getattr(args, "max_attempts", 5) or 5))
            max_continue_rounds = int(getattr(args, "max_continue_rounds", 0) or 0)

            success = False
            last_failure: Optional[str] = None

            for attempt in range(1, max_attempts + 1):
                retry_hint = ""
                if attempt > 1:
                    retry_hint = (
                        "再試行: 直前の出力が不合格。"
                        "本文のみを出力し、ルール説明/見出し/箇条書き/番号リスト/マーカー文字列/段落重複を絶対に出さない。"
                    )
                attempt_instruction = instruction
                if retry_hint:
                    attempt_instruction = (attempt_instruction + "\n\n" + retry_hint).strip() if attempt_instruction else retry_hint
                if attempt_instruction:
                    attempt_instruction = (attempt_instruction + f"\nretry_attempt: {attempt}").strip()

                attempt_prompt = _build_prompt(
                    base_prompt=base_prompt,
                    instruction=attempt_instruction if attempt_instruction else None,
                    include_current=bool(args.include_current),
                    current_a_text=current,
                )

                attempt_prompt_log = logs_dir / f"gemini_cli_prompt__attempt{attempt:02d}.txt"
                attempt_stdout_log = logs_dir / f"gemini_cli_stdout__attempt{attempt:02d}.txt"
                attempt_stderr_log = logs_dir / f"gemini_cli_stderr__attempt{attempt:02d}.txt"
                attempt_meta_log = logs_dir / f"gemini_cli_meta__attempt{attempt:02d}.json"
                _write_text(prompt_log, attempt_prompt)
                _write_text(attempt_prompt_log, attempt_prompt)

                rc, stdout, stderr, elapsed = _run_gemini_cli(
                    gemini_bin=gemini_bin,
                    prompt=attempt_prompt,
                    model=args.gemini_model,
                    sandbox=bool(args.gemini_sandbox),
                    approval_mode=str(args.gemini_approval_mode) if args.gemini_approval_mode else None,
                    yolo=bool(args.gemini_yolo),
                    home_dir=home_dir,
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
                        "tool": "gemini_cli_generate_scripts",
                        "at": _utc_now_iso(),
                        "script_id": script_id,
                        "prompt_path": str(prompt_path),
                        "output_path": str(out_path),
                        "gemini_bin": gemini_bin,
                        "gemini_model": args.gemini_model,
                        "gemini_sandbox": bool(args.gemini_sandbox),
                        "gemini_approval_mode": args.gemini_approval_mode,
                        "gemini_yolo": bool(args.gemini_yolo),
                        "gemini_use_user_home": bool(args.gemini_use_user_home),
                        "gemini_auth_type": str(args.gemini_auth_type),
                        "gemini_home_dir": str(home_dir) if home_dir is not None else "",
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
                        "tool": "gemini_cli_generate_scripts",
                        "at": _utc_now_iso(),
                        "script_id": script_id,
                        "prompt_path": str(prompt_path),
                        "output_path": str(out_path),
                        "gemini_bin": gemini_bin,
                        "gemini_model": args.gemini_model,
                        "gemini_sandbox": bool(args.gemini_sandbox),
                        "gemini_approval_mode": args.gemini_approval_mode,
                        "gemini_yolo": bool(args.gemini_yolo),
                        "gemini_use_user_home": bool(args.gemini_use_user_home),
                        "gemini_auth_type": str(args.gemini_auth_type),
                        "gemini_home_dir": str(home_dir) if home_dir is not None else "",
                        "timeout_sec": int(args.timeout_sec),
                        "elapsed_sec": elapsed,
                        "exit_code": rc,
                        "attempt": attempt,
                    },
                )

                if rc != 0:
                    last_failure = f"{script_id}: gemini_exit={rc} (see {attempt_stderr_log})"
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
                            gemini_bin=gemini_bin,
                            base_prompt=base_prompt,
                            base_instruction=attempt_instruction if attempt_instruction else None,
                            model=str(args.gemini_model or "").strip() if args.gemini_model else None,
                            sandbox=bool(args.gemini_sandbox),
                            approval_mode=str(args.gemini_approval_mode) if args.gemini_approval_mode else None,
                            yolo=bool(args.gemini_yolo),
                            home_dir=home_dir,
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

                if sleep_guard_enabled:
                    issue = _sleep_framing_issue(a_text=a_text, assembled_path=mirror_path)
                    if issue:
                        marker_msg = str(issue.get("message") or "").strip()
                        suffix = f" {marker_msg}" if marker_msg else ""
                        last_failure = (
                            f"{script_id}: rejected_output=sleep_framing_contamination{suffix} (see {attempt_stdout_log})"
                        )
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
                success = True
                last_failure = None
                break

            if not success and last_failure:
                failures.append(last_failure)
            continue

        detected_min = _parse_target_chars_min(final_prompt)
        detected_max = _parse_target_chars_max(final_prompt)
        min_spoken_chars = int(args.min_spoken_chars or 0)
        if min_spoken_chars <= 0 and detected_min:
            min_spoken_chars = int(detected_min)
        if min_spoken_chars > 0 and not bool(args.allow_short):
            spoken_chars = _a_text_spoken_char_count(a_text)
            max_continue_rounds = int(getattr(args, "max_continue_rounds", 0) or 0)
            if spoken_chars < min_spoken_chars and max_continue_rounds > 0:
                a_text, err = _extend_until_min(
                    gemini_bin=gemini_bin,
                    base_prompt=base_prompt,
                    base_instruction=instruction if instruction else None,
                    model=str(args.gemini_model or "").strip() if args.gemini_model else None,
                    sandbox=bool(args.gemini_sandbox),
                    approval_mode=str(args.gemini_approval_mode) if args.gemini_approval_mode else None,
                    yolo=bool(args.gemini_yolo),
                    home_dir=home_dir,
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
                    failures.append(err)
                    continue
                spoken_chars = _a_text_spoken_char_count(a_text)
            if spoken_chars < min_spoken_chars:
                failures.append(
                    f"{script_id}: rejected_output=too_short spoken_chars={spoken_chars} < min={min_spoken_chars} "
                    f"(see {stdout_log})"
                )
                continue

        if sleep_guard_enabled:
            issue = _sleep_framing_issue(a_text=a_text, assembled_path=mirror_path)
            if issue:
                marker_msg = str(issue.get("message") or "").strip()
                suffix = f" {marker_msg}" if marker_msg else ""
                failures.append(f"{script_id}: rejected_output=sleep_framing_contamination{suffix} (see {stdout_log})")
                continue

        # Deterministic SSOT validation (no LLM): reject if any hard errors remain.
        issues, _stats = validate_a_text(a_text, {"assembled_path": str(mirror_path)})
        hard_errors = [it for it in issues if isinstance(it, dict) and str(it.get("severity") or "") == "error"]
        if hard_errors:
            codes = ", ".join(sorted({str(it.get("code") or "") for it in hard_errors if it.get("code")}))
            failures.append(f"{script_id}: rejected_output=script_validation_error codes=[{codes}] (see {stdout_log})")
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

    if failures:
        print("[ERROR] Some items failed:", file=sys.stderr)
        for msg in failures:
            print(f"- {msg}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gemini_cli_generate_scripts.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="generate A-text via gemini CLI (dry-run by default)")
    sp.add_argument("--channel", required=True, help="e.g. CH06")
    mg = sp.add_mutually_exclusive_group(required=True)
    mg.add_argument("--video", help="e.g. 035")
    mg.add_argument("--videos", help="e.g. 035-040 or 35,36,40")
    sp.add_argument("--run", action="store_true", help="Execute gemini and write assembled_human.md (default: dry-run)")

    sp.add_argument("--include-current", dest="include_current", action="store_true", help="Include current A-text in the prompt")
    sp.add_argument("--instruction", default="", help="Optional operator instruction appended to the prompt")
    sp.add_argument(
        "--allow-sleep-framing",
        action="store_true",
        help="Allow sleep-framing phrases even for non-opt-in channels (NOT recommended)",
    )
    sp.add_argument(
        "--split-sections",
        default="",
        help="Generate in multiple parts by section ranges, e.g. '1-4,5-7' (cannot be used with --include-current)",
    )
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
    sp.add_argument(
        "--max-attempts",
        type=int,
        default=5,
        help="Max attempts per episode when output is rejected (default: 5)",
    )
    sp.add_argument(
        "--max-continue-rounds",
        type=int,
        default=3,
        help="If output is too short, ask gemini to continue up to N rounds (default: 3). Set 0 to disable.",
    )

    sp.add_argument("--gemini-bin", default="", help="Explicit gemini binary path (optional)")
    sp.add_argument("--gemini-model", default="", help="Gemini model (passed to gemini --model)")
    sp.add_argument("--gemini-sandbox", action="store_true", help="Run gemini CLI with --sandbox")
    sp.add_argument(
        "--gemini-auth-type",
        default="gemini-api-key",
        choices=["gemini-api-key", "oauth-personal", "vertex-ai", "cloud-shell", "compute-default-credentials"],
        help="Auth type for gemini CLI (default: gemini-api-key via isolated HOME + GEMINI_API_KEY).",
    )
    sp.add_argument(
        "--gemini-use-user-home",
        action="store_true",
        help="Use the user's real HOME/.gemini settings (not recommended for automation).",
    )
    sp.add_argument(
        "--gemini-approval-mode",
        choices=["default", "auto_edit", "yolo"],
        default="",
        help="Gemini CLI approval mode (non-interactive default excludes approval tools)",
    )
    sp.add_argument("--gemini-yolo", action="store_true", help="Legacy: pass gemini --yolo (ignored if --gemini-approval-mode is set)")

    sp.add_argument("--timeout-sec", type=int, default=1800, help="Timeout seconds per episode (default: 1800)")
    sp.set_defaults(func=cmd_run)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
