#!/usr/bin/env python3
from __future__ import annotations

"""
claude_cli_polish_existing_a_text.py — A-text polisher using external `claude` CLI (manual/opt-in)

Purpose:
- Read existing A-text draft:
    workspaces/scripts/{CH}/{NNN}/content/assembled_human.md (fallback: assembled.md)
- Polish/rewrite it for readability and narrative quality using external `claude` CLI.
- Enforce SSOT validator (sleep-framing contamination guard, format rules, etc.).
- Write back to canonical A-text:
    workspaces/scripts/{CH}/{NNN}/content/assembled_human.md
  and mirror to:
    workspaces/scripts/{CH}/{NNN}/content/assembled.md

Policy:
- This tool uses the user's Claude CLI session (subscription auth). It intentionally ignores ANTHROPIC_API_KEY
  to avoid unintended paid API usage.
- No LLM router usage.

Note:
- When Claude CLI hits a subscription rate limit, operators may choose `--engine gemini`.
- When both Claude/Gemini are rate-limited, operators may choose `--engine qwen` (repo shim).
  to continue polishing via Gemini CLI with the same SSOT validator and channel polish prompt.
"""

import argparse
import json
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
from script_pipeline.tools.channel_registry import find_channel_dir  # noqa: E402


# NOTE: This tool is for polishing A-text for narration.
# We keep the sleep-framing guard strict, but avoid single-kanji bans (e.g. "眠") to prevent
# false positives like "不眠" that are not "sleepy ambience" framing.
_SLEEP_MARKERS_STRICT = ("おやすみ", "寝落ち", "睡眠用", "安眠", "布団", "眠れ")
_TIMEJUMP_MARKERS = ("次の日", "翌日", "翌朝", "数日後", "数週間後", "数ヶ月後", "数年後", "翌週", "翌月", "来週", "来月")
_TIMEJUMP_REWRITE = {
    "次の日": "その後",
    "翌日": "その後",
    "翌朝": "その後",
    "数日後": "しばらくして",
    "数週間後": "しばらくして",
    "数ヶ月後": "しばらくして",
    "数年後": "やがて",
    "翌週": "その後",
    "翌月": "その後",
    "来週": "その後",
    "来月": "その後",
}


_CHANNEL_DEFAULTS: dict[str, dict[str, int]] = {
    # CH06 scripts are long-form by design (15k-20k) and already use 6-8 pause lines.
    "CH06": {"default_target_min": 15000, "default_target_max": 20000, "max_pause_lines": 8},
}


def _load_channel_polish_prompt(channel: str) -> str:
    """
    Load channel-global polish instructions (optional).

    Convention:
      packages/script_pipeline/channels/CHxx-*/polish_prompt.txt
    """
    ch = str(channel or "").strip().upper()
    try:
        ch_dir = find_channel_dir(ch)
    except Exception:
        ch_dir = None
    if not ch_dir:
        return ""
    p = ch_dir / "polish_prompt.txt"
    if not p.exists():
        return ""
    try:
        return _read_text(p).strip()
    except Exception:
        return ""


def _compose_instruction(*, channel: str, operator_instruction: str) -> Optional[str]:
    parts: List[str] = []
    ch_prompt = _load_channel_polish_prompt(channel)
    if ch_prompt:
        parts.append(ch_prompt)
    op = str(operator_instruction or "").strip()
    if op:
        parts.append(op)
    combined = "\n\n".join(parts).strip()
    return combined if combined else None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _scratch_dir() -> Path:
    return repo_paths.workspace_root() / "_scratch" / "claude_cli_polish"


def _draft_a_text_path(channel: str, video: str) -> Path:
    base = repo_paths.video_root(channel, video) / "content"
    human = base / "assembled_human.md"
    assembled = base / "assembled.md"
    return human if human.exists() else assembled


def _output_a_text_path(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "content" / "assembled_human.md"


def _mirror_a_text_path(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "content" / "assembled.md"


def _logs_dir(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "logs"


def _status_path(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "status.json"


def _blueprint_outline_path(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "content" / "outline.md"


def _blueprint_master_plan_path(channel: str, video: str) -> Path:
    return repo_paths.video_root(channel, video) / "content" / "analysis" / "master_plan.json"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    _ensure_dir(path.parent)
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    _ensure_dir(path.parent)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_status_metadata(channel: str, video: str) -> Dict[str, Any]:
    p = _status_path(channel, video)
    if not p.exists():
        return {}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    md = obj.get("metadata")
    return md if isinstance(md, dict) else {}


def _truncate_for_prompt(text: str, *, max_chars: int) -> str:
    s = str(text or "")
    if len(s) <= max_chars:
        return s
    # NOTE: Do NOT append markers like "[TRUNCATED]" inside prompt payloads.
    # Some LLM CLIs echo them into the output, causing validator failures.
    return s[:max_chars].rstrip() + "\n"


def _strip_code_fences(text: str) -> str:
    s = _normalize_newlines(text)
    s = re.sub(r"^\s*```[^\n]*\n", "", s)
    s = re.sub(r"\n```[\s]*$", "", s)
    return s


def _strip_edge_pause_lines(text: str) -> str:
    lines = _normalize_newlines(text).split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and lines[0].strip() == "---":
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    changed = True
    while changed:
        changed = False
        while lines and not lines[-1].strip():
            lines.pop()
            changed = True
        if lines and lines[-1].strip() == "---":
            lines.pop()
            changed = True
    return "\n".join(lines).rstrip() + "\n"


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


def _has_sleep_markers(text: str) -> Optional[str]:
    for m in _SLEEP_MARKERS_STRICT:
        if m in text:
            return m
    return None


def _has_timejump_markers(text: str) -> Optional[str]:
    for m in _TIMEJUMP_MARKERS:
        if m in text:
            return m
    return None


def _rewrite_timejump_markers(text: str) -> tuple[str, list[tuple[str, str]]]:
    """
    Soft-fix for models that keep emitting forbidden time-jump strings.
    Only rewrites the explicit banned markers; meaning stays "time passed" but less explicit.
    """
    out = str(text or "")
    applied: list[tuple[str, str]] = []
    for src, dst in _TIMEJUMP_REWRITE.items():
        if src in out:
            out = out.replace(src, dst)
            applied.append((src, dst))
    return out, applied


def _has_consecutive_pause(text: str) -> bool:
    return bool(re.search(r"\n---\n\s*\n---\n", _normalize_newlines(text)))


def _looks_like_meta_output(text: str) -> bool:
    s = _normalize_newlines(text)
    if "[TRUNCATED]" in s:
        return True
    if "<<<" in s or ">>>" in s:
        return True
    # Tool / filesystem leakage (e.g., `wc -m file` output) or absolute paths.
    if "/Users/" in s or "/home/" in s or "workspaces/_scratch/" in s:
        return True
    if re.search(r"(?m)^\s*\d+\s+/(?:Users|home|var)/\S+", s):
        return True

    # English/meta chatter: reject common assistant-style prefaces.
    if re.search(r"(?im)^\s*i['’](?:m|ve|ll)\b", s):
        return True
    if re.search(r"(?im)^\s*i\s+(?:need|want|will|have|am|can|should|must)\b", s):
        return True
    if re.search(r"(?im)^\s*let\s+me\b", s):
        return True
    if re.search(r"(?im)^\s*as\s+an\s+ai\b", s):
        return True
    if re.search(r"^\s*#{1,6}\s+\S", s, flags=re.MULTILINE):
        return True
    if re.search(r"^\s*(?:[-*•]|・)\s+", s, flags=re.MULTILINE):
        return True
    if re.search(r"^\s*\d+\s*[.)）:、]\s+", s, flags=re.MULTILINE):
        return True
    return False


def _strip_continuation_overlap(prev: str, chunk: str, *, max_window: int = 800, min_overlap: int = 80) -> str:
    """
    Remove accidental overlap where the continuation reprints the end of the previous text.
    Conservative: only trims when the previous tail ends with the chunk prefix.
    """
    a = _normalize_newlines(prev).rstrip()
    b = _normalize_newlines(chunk).lstrip()
    if not a or not b:
        return chunk
    tail = a[-max_window:] if len(a) > max_window else a
    limit = min(len(b), len(tail))
    for n in range(limit, max(min_overlap, 1) - 1, -1):
        if tail.endswith(b[:n]):
            return b[n:].lstrip()
    return chunk


def _continue_instruction(*, add_min: int, add_max: int, total_min: int, total_max: int) -> str:
    lo = int(max(0, add_min))
    hi = int(max(lo, add_max))
    return (
        "指示: <<<CURRENT_A_TEXT_START>>> の直後から、自然につながる『続きだけ』を書いてください。"
        "要約・言い換え連打・前文の繰り返し・同一段落の再掲は禁止。"
        "不足分は『同じ出来事の中での棘/場面/観察』を追加し、生活音/手元/距離感など具体で伸ばしてください。"
        "禁止: 時間ジャンプ（次の日/翌日/翌朝/来週/来月/数日後/数週間後/数ヶ月後/数年後/翌週/翌月）を一切出さない。"
        "結末/再定義/一手/余韻を二重に書かない（同じ締めを繰り返さない）。"
        f"今から書く追加分は必ず {lo}〜{hi} 字（全体は必ず {int(total_min)}〜{int(total_max)} 字）。"
        "最後は物語として完結し、句点などで確実に閉じてください。"
    )


def _build_continue_prompt(
    *,
    channel: str,
    video: str,
    current_a_text: str,
    outline: str,
    master_plan_json: str,
    target_min: int,
    target_max: int,
    max_pause_lines: int,
    add_min: int,
    add_max: int,
    extra_instruction: str | None,
) -> str:
    ch = str(channel).strip().upper()
    vv = _z3(video)
    rules: List[str] = [
        f"- チャンネル: {ch}",
        f"- 動画: {vv}",
        f"- 文字数: 必ず {int(target_min)}〜{int(target_max)} 字（本文のみ）",
        f"- 区切り: `---` は最大 {int(max_pause_lines)} 回、連続禁止、末尾に置かない",
        "- 禁止: 睡眠誘導フレーミング（寝落ち/睡眠用/安眠/布団/おやすみ/眠れ/眠る/眠り 等）",
        "- 禁止: 時間ジャンプ語（次の日/翌日/翌朝/数日後/数週間後/数ヶ月後/数年後/翌週/翌月/来週/来月）",
        "- 禁止: 見出し/箇条書き/手順/番号リスト/メタ言及/まとめ/ルール説明",
        "- 禁止: DRAFTにない新しい事件/設定/登場人物/固有名詞/数字を追加しない（増やさない）",
        "- 出力: 追加分（続き）だけ。本文の再掲禁止",
    ]
    inst = str(extra_instruction or "").strip()
    return "\n".join(
        [
            "あなたは日本語のYouTubeナレーション台本（Aテキスト）の編集者です。",
            "下のCURRENT_A_TEXTの続きだけを書いて、全体を指定字数まで伸ばしてください。",
            "",
            "【ルール】",
            *rules,
            "",
            "【Blueprint: outline.md（参照）】",
            _truncate_for_prompt(outline, max_chars=1200).rstrip(),
            "",
            "【Blueprint: master_plan.json（参照）】",
            _truncate_for_prompt(master_plan_json, max_chars=1200).rstrip(),
            "",
            "<<<CURRENT_A_TEXT_START>>>",
            _truncate_for_prompt(current_a_text, max_chars=28000).rstrip(),
            "<<<CURRENT_A_TEXT_END>>>",
            "",
            _continue_instruction(add_min=add_min, add_max=add_max, total_min=target_min, total_max=target_max),
            "",
            "【追加指示】" if inst else "【追加指示】（なし）",
            inst if inst else "",
            "",
            "出力は追加分（続き）の本文のみ。",
        ]
    ).strip() + "\n"


def _build_editor_prompt(
    *,
    channel: str,
    video: str,
    draft: str,
    outline: str,
    master_plan_json: str,
    target_min: int,
    target_max: int,
    max_pause_lines: int,
    extra_instruction: str | None,
    retry_hint: str | None,
) -> str:
    ch = str(channel).strip().upper()
    vv = _z3(video)

    rules: List[str] = [
        f"- チャンネル: {ch}",
        f"- 動画: {vv}",
        f"- 文字数: 必ず {int(target_min)}〜{int(target_max)} 字（本文のみ）",
        f"- 区切り: `---` は最大 {int(max_pause_lines)} 回、連続禁止、末尾に置かない",
        "- 禁止: 睡眠誘導フレーミング（寝落ち/睡眠用/安眠/布団/おやすみ/眠れ/眠る/眠り 等）",
        "- 禁止: 時間ジャンプ語（次の日/翌日/翌朝/数日後/数週間後/数ヶ月後/数年後/翌週/翌月/来週/来月）",
        "- 禁止: 見出し/箇条書き/手順/番号リスト/メタ言及/まとめ/ルール説明",
        "- 禁止: DRAFTにない新しい事件/設定/登場人物/固有名詞/数字を追加しない（増やさない）",
        "- 方針: 出来事は増やさず、重複/冗長/説教臭さを減らしつつ、削った分は同じ場面の具体描写で埋めて必ず指定字数を満たす（短縮禁止・視聴者満足度最優先）",
        "- 出力: リライト後の本文のみ（前置き無し）",
    ]

    inst_parts: List[str] = []
    if extra_instruction:
        inst_parts.append(str(extra_instruction).strip())
    if retry_hint:
        inst_parts.append(str(retry_hint).strip())
    inst = "\n\n".join([p for p in inst_parts if p]).strip()

    return "\n".join(
        [
            "あなたは日本語のYouTubeナレーション台本（Aテキスト）の編集者です。",
            "下のDRAFTを、Blueprintを守りつつ読みやすく自然な物語へ推敲してください。",
            "",
            "【編集ルール（厳守）】",
            *rules,
            "",
            "【Blueprint: outline.md（参照）】",
            _truncate_for_prompt(outline, max_chars=1800).rstrip(),
            "",
            "【Blueprint: master_plan.json（参照）】",
            _truncate_for_prompt(master_plan_json, max_chars=1800).rstrip(),
            "",
            "【DRAFT（この内容をベースに推敲）】",
            "<<<DRAFT_START>>>",
            _truncate_for_prompt(draft, max_chars=28000).rstrip(),
            "<<<DRAFT_END>>>",
            "",
            "【追加指示】" if inst else "【追加指示】（なし）",
            inst if inst else "",
            "",
            "出力は本文のみ。",
        ]
    ).strip() + "\n"


_CLAUDE_ALLOWED_ALIASES = {"sonnet", "opus"}
_CLAUDE_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"


def _env_truthy(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _assert_opus_allowed(model: str) -> None:
    low = str(model or "").strip().lower()
    if low == "opus" or low.startswith("claude-opus-4-5-"):
        if not _env_truthy("YTM_ALLOW_CLAUDE_OPUS"):
            raise SystemExit(
                "\n".join(
                    [
                        "[POLICY] Forbidden: Claude Opus is not allowed by default.",
                        f"- got --claude-model: {model}",
                        "- allow: set YTM_ALLOW_CLAUDE_OPUS=1 for THIS run (owner instruction required), then retry.",
                        f"- default: {_CLAUDE_DEFAULT_MODEL}",
                    ]
                )
            )


def _validate_claude_model(raw: str | None) -> str:
    low = str(raw or "").strip().lower()
    if not low:
        return _CLAUDE_DEFAULT_MODEL
    if low in _CLAUDE_ALLOWED_ALIASES:
        _assert_opus_allowed(low)
        return low
    if re.fullmatch(r"claude-(sonnet|opus)-4-5-\d{8}", low):
        _assert_opus_allowed(low)
        return low
    raise SystemExit(
        "\n".join(
            [
                "[POLICY] Forbidden --claude-model (unsupported).",
                "- allowed: sonnet | opus | claude-sonnet-4-5-YYYYMMDD | claude-opus-4-5-YYYYMMDD",
                f"- note: Opus requires YTM_ALLOW_CLAUDE_OPUS=1. Default is {_CLAUDE_DEFAULT_MODEL}.",
            ]
        )
    )


def _find_claude_bin(explicit: str | None) -> str:
    if explicit and str(explicit).strip():
        p = Path(str(explicit).strip())
        if p.exists() and os.access(str(p), os.X_OK):
            return str(p)
        raise SystemExit(f"claude not found at --claude-bin: {explicit}")
    found = shutil.which("claude")
    if found:
        return found
    raise SystemExit("claude CLI not found. Install `claude` and ensure it is on PATH.")


def _coerce_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_claude_cli(*, claude_bin: str, prompt: str, model: str, timeout_sec: int) -> tuple[int, str, str, float]:
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
    env.pop("ANTHROPIC_API_KEY", None)

    _ensure_dir(_scratch_dir())

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=str(prompt),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(_scratch_dir()),
            env=env,
            timeout=max(1, int(timeout_sec)),
        )
        elapsed = time.time() - start
        return int(proc.returncode), _coerce_text(proc.stdout), _coerce_text(proc.stderr), float(elapsed)
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - start
        return 124, _coerce_text(getattr(e, "stdout", "")), _coerce_text(getattr(e, "stderr", "")), float(elapsed)


def _find_gemini_bin(explicit: str | None) -> str:
    if explicit and str(explicit).strip():
        p = Path(str(explicit).strip())
        if p.exists() and os.access(str(p), os.X_OK):
            return str(p)
        raise SystemExit(f"gemini not found at --gemini-bin: {explicit}")
    found = shutil.which("gemini")
    if found:
        return found
    raise SystemExit("gemini CLI not found. Install `gemini` and ensure it is on PATH.")


def _find_qwen_bin(explicit: str | None) -> str:
    """
    Policy: in this repository, qwen must be invoked via the repo shim:
      scripts/bin/qwen
    The shim enforces qwen-oauth and blocks provider/model switching.
    """
    shim = repo_paths.repo_root() / "scripts" / "bin" / "qwen"
    if explicit and str(explicit).strip():
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
            ]
        )
    )


def _run_gemini_cli(
    *,
    gemini_bin: str,
    prompt: str,
    model: str,
    sandbox: bool,
    timeout_sec: int,
) -> tuple[int, str, str, float]:
    cmd: List[str] = [gemini_bin, "--output-format", "text"]
    if str(model or "").strip():
        cmd += ["--model", str(model).strip()]
    if bool(sandbox):
        cmd.append("--sandbox")

    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")

    _ensure_dir(_scratch_dir())

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=str(prompt),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(_scratch_dir()),
            env=env,
            timeout=max(1, int(timeout_sec)),
        )
        elapsed = time.time() - start
        return int(proc.returncode), _coerce_text(proc.stdout), _coerce_text(proc.stderr), float(elapsed)
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - start
        return 124, _coerce_text(getattr(e, "stdout", "")), _coerce_text(getattr(e, "stderr", "")), float(elapsed)


def _run_qwen_cli(
    *,
    qwen_bin: str,
    prompt: str,
    sandbox: bool,
    approval_mode: str,
    timeout_sec: int,
) -> tuple[int, str, str, float]:
    cmd: List[str] = [qwen_bin, "--output-format", "text", "--chat-recording", "false"]
    if bool(sandbox):
        cmd.append("--sandbox")
    am = str(approval_mode or "").strip()
    if am:
        cmd += ["--approval-mode", am]
    cmd += ["-p", str(prompt)]

    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")

    _ensure_dir(_scratch_dir())

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=str(_scratch_dir()),
            env=env,
            timeout=max(1, int(timeout_sec)),
        )
        elapsed = time.time() - start
        return int(proc.returncode), _coerce_text(proc.stdout), _coerce_text(proc.stderr), float(elapsed)
    except subprocess.TimeoutExpired as e:
        elapsed = time.time() - start
        return 124, _coerce_text(getattr(e, "stdout", "")), _coerce_text(getattr(e, "stderr", "")), float(elapsed)


def _run_external_cli(
    *,
    engine: str,
    bin_path: str,
    prompt: str,
    model: str,
    timeout_sec: int,
    gemini_sandbox: bool,
    qwen_sandbox: bool,
    qwen_approval_mode: str,
) -> tuple[int, str, str, float]:
    low = str(engine or "").strip().lower()
    if low == "claude":
        return _run_claude_cli(claude_bin=bin_path, prompt=prompt, model=str(model or ""), timeout_sec=int(timeout_sec))
    if low == "gemini":
        return _run_gemini_cli(
            gemini_bin=bin_path,
            prompt=prompt,
            model=str(model or ""),
            sandbox=bool(gemini_sandbox),
            timeout_sec=int(timeout_sec),
        )
    if low == "qwen":
        return _run_qwen_cli(
            qwen_bin=bin_path,
            prompt=prompt,
            sandbox=bool(qwen_sandbox),
            approval_mode=str(qwen_approval_mode or ""),
            timeout_sec=int(timeout_sec),
        )
    raise SystemExit(f"Invalid --engine: {engine!r} (allowed: claude | gemini | qwen)")


def _build_retry_hint(last_failure: str) -> str:
    lf = str(last_failure or "").strip()
    if not lf:
        return ""
    if lf.startswith("rejected_output=timejump_marker:"):
        hit = lf.split("rejected_output=timejump_marker:", 1)[-1].strip()
        return "\n".join(
            [
                f"再試行: 直前の出力に時間ジャンプ語（{hit}）が含まれて不合格。",
                f"- {hit} を含む言い回しを完全に避け、該当箇所を別の表現に言い換える（例: その後 / 間もなく / ほどなくして）",
                "- 禁止語: 次の日/翌日/翌朝/数日後/数週間後/数ヶ月後/数年後/翌週/翌月/来週/来月",
                "- それ以外は DRAFT の出来事/年号/固有名詞/数字/証拠の順序 を維持し、本文のみを出力。",
            ]
        )
    if lf.startswith("rejected_output=sleep_marker:"):
        hit = lf.split("rejected_output=sleep_marker:", 1)[-1].strip()
        return "\n".join(
            [
                f"再試行: 直前の出力に睡眠誘導語（{hit}）が含まれて不合格。",
                "- 睡眠用/安眠/寝落ち/布団/おやすみ 等の語彙や雰囲気を混入させない。",
                "- 本文のみを出力。",
            ]
        )
    if "sleep_framing_contamination" in lf:
        return "\n".join(
            [
                "再試行: 直前の出力が『睡眠用』系フレーミング混入で不合格。",
                "- 眠る/眠り/眠れ/安眠/寝落ち/布団/おやすみ 等の語彙や比喩を使わない。",
                "- 事件/記録/証拠の語りに集中し、本文のみを出力。",
            ]
        )
    if "rejected_output=meta_or_structure" in lf:
        return "再試行: 直前の出力にメタ/見出し/箇条書き等が混入して不合格。本文のみを出力。"
    if "incomplete_ending" in lf:
        return "\n".join(
            [
                "再試行: 直前の出力が未完で不合格（文が途中で切れている/末尾が閉じていない）。",
                "- 末尾は必ず句点（。など）で閉じて完結させる。",
                "- 途中で途切れた文を残さない。本文のみを出力。",
            ]
        )
    if lf.startswith("rejected_output=too_short"):
        return "\n".join(
            [
                f"再試行: 直前の出力が短すぎて不合格（{lf}）。",
                "- DRAFT の内容を省略しない。削った箇所があるなら戻す。",
                "- 新しい事件/設定/人物を増やさず、同じ出来事の中で『記録/数値/手順/矛盾/現場描写』を具体化して字数を満たす。",
                "- 本文のみを出力。",
            ]
        )
    if lf.startswith("rejected_output=too_long"):
        return "\n".join(
            [
                f"再試行: 直前の出力が長すぎて不合格（{lf}）。",
                "- 新規の追加はせず、重複・言い換え・同内容の繰り返しを削って規定内に収める。",
                "- 本文のみを出力。",
            ]
        )
    return f"再試行: 直前の出力が不合格（{lf}）。不合格原因を確実に解消して本文のみを出力。"


def _build_validator_metadata(*, channel: str, video: str, assembled_path: Path) -> Dict[str, Any]:
    md = dict(_load_status_metadata(channel, video))
    md["assembled_path"] = str(assembled_path)
    return md


def _pick_target_range(md: Dict[str, Any], *, default_min: int, default_max: int) -> tuple[int, int]:
    try:
        lo = int(str(md.get("target_chars_min") or "").strip() or default_min)
    except Exception:
        lo = int(default_min)
    try:
        hi = int(str(md.get("target_chars_max") or "").strip() or default_max)
    except Exception:
        hi = int(default_max)
    if lo <= 0:
        lo = int(default_min)
    if hi <= 0:
        hi = int(default_max)
    if hi < lo:
        lo, hi = hi, lo
    return lo, hi


def _validate_output(
    *,
    channel: str,
    video: str,
    a_text: str,
    out_path: Path,
    target_min: int,
    target_max: int,
    max_pause_lines: int,
    allow_too_short: bool,
    auto_fix_timejump: bool,
) -> tuple[bool, str]:
    cleaned = _strip_edge_pause_lines(_strip_code_fences(a_text))
    if _looks_like_meta_output(cleaned):
        return False, "rejected_output=meta_or_structure"

    sleep_hit = _has_sleep_markers(cleaned)
    if sleep_hit:
        return False, f"rejected_output=sleep_marker:{sleep_hit}"

    if auto_fix_timejump:
        cleaned, _applied = _rewrite_timejump_markers(cleaned)

    time_hit = _has_timejump_markers(cleaned)
    if time_hit:
        return False, f"rejected_output=timejump_marker:{time_hit}"

    if _has_consecutive_pause(cleaned):
        return False, "rejected_output=consecutive_pause_lines"

    if cleaned.rstrip().endswith("\n---"):
        return False, "rejected_output=trailing_pause_line"

    issues, stats = validate_a_text(cleaned, _build_validator_metadata(channel=channel, video=video, assembled_path=out_path))
    hard = [i for i in issues if i.get("severity") == "error"]
    if allow_too_short:
        hard = [i for i in hard if str(i.get("code") or "") != "length_too_short"]
    if hard:
        codes = ",".join([str(i.get("code") or "?") for i in hard])
        return False, f"rejected_output=validator_errors:{codes}"

    char_count = int(stats.get("char_count") or 0)
    pause_lines = int(stats.get("pause_lines") or 0)
    if (not allow_too_short) and char_count < int(target_min):
        return False, f"rejected_output=too_short char_count={char_count} < min={target_min}"
    if char_count > int(target_max):
        return False, f"rejected_output=too_long char_count={char_count} > max={target_max}"
    if pause_lines > int(max_pause_lines):
        return False, f"rejected_output=too_many_pause_lines pause_lines={pause_lines} > max={max_pause_lines}"

    return True, cleaned


def _extend_until_min(
    *,
    engine: str,
    bin_path: str,
    model: str,
    gemini_sandbox: bool,
    qwen_sandbox: bool,
    qwen_approval_mode: str,
    log_prefix: str,
    extra_instruction: str | None,
    channel: str,
    video: str,
    logs_dir: Path,
    outline: str,
    master_plan_json: str,
    current_a_text: str,
    target_min: int,
    target_max: int,
    max_pause_lines: int,
    timeout_sec: int,
    max_continue_rounds: int,
) -> tuple[str, Optional[str]]:
    combined = _normalize_newlines(current_a_text).rstrip() + "\n"
    for cont in range(1, max(0, int(max_continue_rounds)) + 1):
        spoken = _a_text_spoken_char_count(combined)
        if spoken >= int(target_min):
            return combined, None

        need_min = int(target_min) - spoken
        need_max = max(need_min, int(target_max) - spoken)
        # Keep a reasonable upper bound for "続き" to avoid drifting too far.
        need_max = min(need_max, need_min + 2000)

        base_cont_prompt = _build_continue_prompt(
            channel=channel,
            video=video,
            current_a_text=combined,
            outline=outline,
            master_plan_json=master_plan_json,
            target_min=target_min,
            target_max=target_max,
            max_pause_lines=max_pause_lines,
            add_min=need_min,
            add_max=need_max,
            extra_instruction=extra_instruction,
        )
        retry_hint: Optional[str] = None
        appended = False
        for retry in range(1, 4):
            suffix = "" if retry == 1 else f"__retry{retry:02d}"
            cont_prompt = base_cont_prompt
            if retry_hint:
                cont_prompt = cont_prompt.rstrip() + "\n\n" + retry_hint.strip() + "\n"

            cont_prompt_log = logs_dir / f"{log_prefix}_prompt__cont{cont:02d}{suffix}.txt"
            cont_stdout_log = logs_dir / f"{log_prefix}_stdout__cont{cont:02d}{suffix}.txt"
            cont_stderr_log = logs_dir / f"{log_prefix}_stderr__cont{cont:02d}{suffix}.txt"
            _write_text(cont_prompt_log, cont_prompt)

            rc, stdout, stderr, _elapsed = _run_external_cli(
                engine=engine,
                bin_path=bin_path,
                prompt=cont_prompt,
                model=model,
                timeout_sec=int(timeout_sec),
                gemini_sandbox=bool(gemini_sandbox),
                qwen_sandbox=bool(qwen_sandbox),
                qwen_approval_mode=str(qwen_approval_mode or ""),
            )
            _write_text(cont_stdout_log, stdout)
            _write_text(cont_stderr_log, stderr)
            if rc != 0:
                retry_hint = f"再試行: {engine}_exit={rc}。本文のみで、禁止語を入れず、続きだけを書き直すこと。"
                continue

            chunk = _strip_edge_pause_lines(_strip_code_fences(stdout))
            if _looks_like_meta_output(chunk):
                retry_hint = "再試行: 直前の出力に見出し/注釈/メタが混入した。本文のみで続きだけを書き直すこと。"
                continue
            sleep_hit = _has_sleep_markers(chunk)
            if sleep_hit:
                retry_hint = f"再試行: 直前の追加分に睡眠誘導語（{sleep_hit}）が含まれ不合格。禁止語を一切入れず続きだけを書くこと。"
                continue
            time_hit = _has_timejump_markers(chunk)
            if time_hit:
                retry_hint = f"再試行: 直前の追加分に時間ジャンプ語（{time_hit}）が含まれ不合格。同じ一日内で、禁止語を一切入れず続きだけを書くこと。"
                continue

            trimmed = _strip_continuation_overlap(combined, chunk)
            trimmed = _strip_edge_pause_lines(trimmed)
            if not trimmed.strip():
                retry_hint = "再試行: 追加分が空/重複だった。重複せずに『続きだけ』を追加すること。"
                continue

            combined = combined.rstrip() + "\n\n" + trimmed.strip() + "\n"
            appended = True
            break

        if not appended:
            return combined, f"rejected_output=invalid_continuation cont={cont:02d} (see {logs_dir})"

    return combined, f"rejected_output=too_short_after_continuations min={target_min} spoken={_a_text_spoken_char_count(combined)}"


def cmd_run(args: argparse.Namespace) -> int:
    channel = str(args.channel).strip().upper()
    if not re.fullmatch(r"CH\d{2}", channel):
        raise SystemExit(f"Invalid --channel: {channel!r}")

    # Apply channel-specific defaults only when the operator did not override.
    ch_defaults = _CHANNEL_DEFAULTS.get(channel)
    if ch_defaults:
        if int(getattr(args, "default_target_min", 0) or 0) == 6000 and int(getattr(args, "default_target_max", 0) or 0) == 8000:
            args.default_target_min = int(ch_defaults["default_target_min"])
            args.default_target_max = int(ch_defaults["default_target_max"])
        if int(getattr(args, "max_pause_lines", 0) or 0) == 5:
            args.max_pause_lines = int(ch_defaults["max_pause_lines"])

    videos: List[str] = []
    if args.video:
        videos = [_z3(args.video)]
    elif args.videos:
        videos = _parse_videos(args.videos)
    if not videos:
        raise SystemExit("No videos specified")

    engine = str(getattr(args, "engine", "claude") or "claude").strip().lower()
    if engine not in ("claude", "gemini", "qwen"):
        raise SystemExit("Invalid --engine (allowed: claude | gemini | qwen)")

    gemini_sandbox = bool(getattr(args, "gemini_sandbox", False))
    qwen_sandbox = bool(getattr(args, "qwen_sandbox", True))
    qwen_approval_mode = str(getattr(args, "qwen_approval_mode", "") or "").strip()

    if engine == "claude":
        bin_path = _find_claude_bin(args.claude_bin)
        model = _validate_claude_model(args.claude_model)
    elif engine == "gemini":
        bin_path = _find_gemini_bin(getattr(args, "gemini_bin", ""))
        model = str(getattr(args, "gemini_model", "") or "").strip()
    else:
        bin_path = _find_qwen_bin(getattr(args, "qwen_bin", ""))
        _validate_qwen_model(getattr(args, "qwen_model", ""))
        model = ""

    if engine == "claude":
        log_prefix = "claude_polish"
    elif engine == "gemini":
        log_prefix = "gemini_polish"
    else:
        log_prefix = "qwen_polish"

    failures: List[str] = []
    extra_instruction = _compose_instruction(channel=channel, operator_instruction=str(args.instruction or ""))
    for vv in videos:
        script_id = f"{channel}-{vv}"
        draft_path = _draft_a_text_path(channel, vv)
        out_path = _output_a_text_path(channel, vv)
        mirror_path = _mirror_a_text_path(channel, vv)
        logs_dir = _logs_dir(channel, vv)

        if not draft_path.exists():
            failures.append(f"{script_id}: missing_draft={draft_path}")
            continue

        md = _load_status_metadata(channel, vv)
        target_min, target_max = _pick_target_range(md, default_min=int(args.default_target_min), default_max=int(args.default_target_max))

        outline_path = _blueprint_outline_path(channel, vv)
        master_plan_path = _blueprint_master_plan_path(channel, vv)
        outline = _read_text(outline_path) if outline_path.exists() else ""
        master_plan_json = master_plan_path.read_text(encoding="utf-8") if master_plan_path.exists() else "{}"
        draft = _read_text(draft_path)

        if not args.run:
            print(f"[DRY-RUN] {script_id} -> {out_path} (draft={draft_path})")
            continue

        _ensure_dir(logs_dir)
        prompt_log = logs_dir / f"{log_prefix}_prompt.txt"
        stdout_log = logs_dir / f"{log_prefix}_stdout.txt"
        stderr_log = logs_dir / f"{log_prefix}_stderr.txt"
        meta_log = logs_dir / f"{log_prefix}_meta.json"

        last_failure: Optional[str] = None
        for attempt in range(1, max(1, int(args.max_attempts)) + 1):
            retry_hint = None
            if attempt > 1 and last_failure:
                retry_hint = _build_retry_hint(last_failure)

            prompt = _build_editor_prompt(
                channel=channel,
                video=vv,
                draft=draft,
                outline=outline,
                master_plan_json=master_plan_json,
                target_min=target_min,
                target_max=target_max,
                max_pause_lines=int(args.max_pause_lines),
                extra_instruction=extra_instruction,
                retry_hint=retry_hint,
            )

            attempt_prompt_log = logs_dir / f"{log_prefix}_prompt__attempt{attempt:02d}.txt"
            attempt_stdout_log = logs_dir / f"{log_prefix}_stdout__attempt{attempt:02d}.txt"
            attempt_stderr_log = logs_dir / f"{log_prefix}_stderr__attempt{attempt:02d}.txt"
            attempt_meta_log = logs_dir / f"{log_prefix}_meta__attempt{attempt:02d}.json"
            _write_text(prompt_log, prompt)
            _write_text(attempt_prompt_log, prompt)

            rc, stdout, stderr, elapsed = _run_external_cli(
                engine=engine,
                bin_path=bin_path,
                prompt=prompt,
                model=model,
                timeout_sec=int(args.timeout_sec),
                gemini_sandbox=bool(gemini_sandbox),
                qwen_sandbox=bool(qwen_sandbox),
                qwen_approval_mode=str(qwen_approval_mode or ""),
            )
            _write_text(stdout_log, stdout)
            _write_text(stderr_log, stderr)
            _write_text(attempt_stdout_log, stdout)
            _write_text(attempt_stderr_log, stderr)
            meta_payload: Dict[str, Any] = {
                "schema_version": 1,
                "tool": "claude_cli_polish_existing_a_text",
                "engine": engine,
                "at": _utc_now_iso(),
                "script_id": script_id,
                "draft_path": str(draft_path),
                "output_path": str(out_path),
                "timeout_sec": int(args.timeout_sec),
                "elapsed_sec": elapsed,
                "exit_code": rc,
                "attempt": attempt,
            }
            if engine == "claude":
                meta_payload["claude_bin"] = bin_path
                meta_payload["claude_model"] = model
            elif engine == "gemini":
                meta_payload["gemini_bin"] = bin_path
                meta_payload["gemini_model"] = model
                meta_payload["gemini_sandbox"] = bool(gemini_sandbox)
            else:
                meta_payload["qwen_bin"] = bin_path
                meta_payload["qwen_sandbox"] = bool(qwen_sandbox)
                meta_payload["qwen_approval_mode"] = str(qwen_approval_mode or "")
            _write_json(
                meta_log,
                meta_payload,
            )
            _write_json(
                attempt_meta_log,
                meta_payload,
            )

            if rc != 0:
                last_failure = f"{engine}_exit={rc}"
                continue

            allow_too_short = bool(engine == "claude") or bool(engine == "qwen" and int(args.max_continue_rounds) > 0)
            ok, result = _validate_output(
                channel=channel,
                video=vv,
                a_text=stdout,
                out_path=mirror_path,
                target_min=target_min,
                target_max=target_max,
                max_pause_lines=int(args.max_pause_lines),
                allow_too_short=bool(allow_too_short),
                auto_fix_timejump=bool(getattr(args, "auto_fix_timejump", False)),
            )
            if not ok:
                last_failure = result
                continue

            final_text = str(result)

            if allow_too_short and _a_text_spoken_char_count(final_text) < int(target_min) and int(args.max_continue_rounds) > 0:
                extended, err = _extend_until_min(
                    engine=engine,
                    bin_path=bin_path,
                    model=model,
                    gemini_sandbox=bool(gemini_sandbox),
                    qwen_sandbox=bool(qwen_sandbox),
                    qwen_approval_mode=str(qwen_approval_mode or ""),
                    log_prefix=log_prefix,
                    extra_instruction=extra_instruction,
                    channel=channel,
                    video=vv,
                    logs_dir=logs_dir,
                    outline=outline,
                    master_plan_json=master_plan_json,
                    current_a_text=final_text,
                    target_min=target_min,
                    target_max=target_max,
                    max_pause_lines=int(args.max_pause_lines),
                    timeout_sec=int(args.timeout_sec),
                    max_continue_rounds=int(args.max_continue_rounds),
                )
                if err:
                    last_failure = err
                    continue
                final_text = extended

            ok2, result2 = _validate_output(
                channel=channel,
                video=vv,
                a_text=final_text,
                out_path=mirror_path,
                target_min=target_min,
                target_max=target_max,
                max_pause_lines=int(args.max_pause_lines),
                allow_too_short=False,
                auto_fix_timejump=bool(getattr(args, "auto_fix_timejump", False)),
            )
            if not ok2:
                last_failure = result2
                continue
            final_text = str(result2)

            backup_note = ""
            if out_path.exists():
                bak = out_path.with_name(out_path.name + f".bak.{_utc_now_compact()}")
                out_path.replace(bak)
                backup_note = f" (backup: {bak.name})"

            _write_text(out_path, final_text)
            _write_text(mirror_path, final_text)
            print(f"[OK] {script_id} -> {out_path}{backup_note}")
            last_failure = None
            break

        if last_failure:
            failures.append(f"{script_id}: {last_failure} (see {logs_dir})")

    if failures:
        print("[ERROR] Some items failed:", file=sys.stderr)
        for msg in failures:
            print(f"- {msg}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="claude_cli_polish_existing_a_text.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("run", help="polish existing A-text via external CLI (dry-run by default)")
    sp.add_argument("--channel", required=True, help="e.g. CH28")
    mg = sp.add_mutually_exclusive_group(required=True)
    mg.add_argument("--video", help="e.g. 002")
    mg.add_argument("--videos", help="e.g. 001-030 or 1,2,3")
    sp.add_argument("--run", action="store_true", help="Execute external CLI and overwrite assembled_human.md (default: dry-run)")

    sp.add_argument("--instruction", default="", help="Optional operator instruction appended to the editor prompt")
    sp.add_argument("--max-attempts", type=int, default=3, help="Max attempts per episode (default: 3)")
    sp.add_argument(
        "--max-continue-rounds",
        type=int,
        default=2,
        help="If output is too short, ask the LLM CLI to continue up to N rounds (default: 2). Set 0 to disable.",
    )
    sp.add_argument("--timeout-sec", type=int, default=1800, help="Timeout seconds per episode (default: 1800)")

    sp.add_argument(
        "--engine",
        default="claude",
        choices=["claude", "gemini", "qwen"],
        help="Which external CLI to use (default: claude). Use gemini when Claude is rate-limited. Use qwen when both Claude/Gemini are rate-limited.",
    )

    sp.add_argument("--claude-bin", default="", help="Explicit claude binary path (optional)")
    sp.add_argument(
        "--claude-model",
        default=_CLAUDE_DEFAULT_MODEL,
        help=f"Claude model alias/name (default: {_CLAUDE_DEFAULT_MODEL}). Opus requires YTM_ALLOW_CLAUDE_OPUS=1 and explicit owner instruction.",
    )

    sp.add_argument("--gemini-bin", default="", help="Explicit gemini binary path (optional)")
    sp.add_argument("--gemini-model", default="", help="Gemini model passed to gemini --model (optional)")
    sp.add_argument("--gemini-sandbox", action="store_true", help="Run gemini CLI with --sandbox (recommended)")

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

    sp.add_argument(
        "--auto-fix-timejump",
        action="store_true",
        help="Auto-rewrite forbidden time-jump markers in output (useful for Gemini fallback).",
    )

    sp.add_argument("--max-pause-lines", type=int, default=5, help="Max `---` lines allowed in output (default: 5)")
    sp.add_argument("--default-target-min", type=int, default=6000, help="Fallback target_chars_min if status.json missing")
    sp.add_argument("--default-target-max", type=int, default=8000, help="Fallback target_chars_max if status.json missing")
    sp.set_defaults(func=cmd_run)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
