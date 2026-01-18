#!/usr/bin/env python3
from __future__ import annotations

"""
qwen_cli_generate_scripts.py — Qwen CLI script writer helper (manual/opt-in)

Purpose:
- Generate CH06 A-text (assembled_human.md + assembled.md) using `qwen -p` only.
- Keep artifacts consistent with SoT:
  - A-text SoT: workspaces/scripts/{CH}/{NNN}/content/assembled_human.md
  - Mirror:     workspaces/scripts/{CH}/{NNN}/content/assembled.md
  - Status:     workspaces/scripts/{CH}/{NNN}/status.json

Notes:
- This tool intentionally does NOT use OpenRouter / LLM API routing.
- Section budgets come from SSOT pattern: ssot/ops/OPS_SCRIPT_PATTERNS.yaml (CH06).
"""

import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402


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


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


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


def _strip_leading_labels(text: str) -> str:
    normalized = _normalize_newlines(text)
    lines = normalized.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    label_patterns = [
        re.compile(r"^Part\s*\d+\s*$", flags=re.IGNORECASE),
        re.compile(r"^CH\d{2}[-_].*$", flags=re.IGNORECASE),
        re.compile(r"^都市伝説.*ダーク図書館.*$", flags=re.IGNORECASE),
        re.compile(r"^都市伝説のダーク図書館\s*$"),
    ]
    while lines and len(lines[0].strip()) <= 40 and any(p.match(lines[0].strip()) for p in label_patterns):
        lines.pop(0)
        while lines and not lines[0].strip():
            lines.pop(0)
    return "\n".join(lines).strip()


def _sanitize_text(text: str) -> str:
    s = _strip_leading_labels(text)
    s = _normalize_newlines(s)

    # Remove accidental in-section pause markers
    kept: List[str] = []
    for line in s.split("\n"):
        if line.strip() == "---":
            continue
        kept.append(line)
    s = "\n".join(kept).strip()

    # Common accidental English tokens
    s = re.sub(r"\bhistory\b", "歴史", s, flags=re.IGNORECASE)
    s = re.sub(r"\bauthority\b", "権威", s, flags=re.IGNORECASE)
    s = re.sub(r"\bparanoia\b", "思い込み", s, flags=re.IGNORECASE)

    # Avoid list-like punctuation and ellipsis
    s = s.replace("・", "と")
    s = re.sub(r"\.{3,}", "。", s)
    s = re.sub(r"…{2,}", "。", s)

    # Hard-forbid chars (should be prevented by prompt)
    s = s.replace("（", "").replace("）", "").replace("(", "").replace(")", "")
    s = s.replace("「", "").replace("」", "").replace("『", "").replace("』", "")
    s = s.replace("?", "。").replace("？", "。")

    return s.strip()


def _split_paragraphs(text: str) -> List[str]:
    normalized = _normalize_newlines(text).strip()
    if not normalized:
        return []
    return [p.strip() for p in re.split(r"\n\s*\n+", normalized) if p.strip()]


def _max_consecutive_run(items: List[str]) -> int:
    best = 0
    cur = 0
    prev: Optional[str] = None
    for it in items:
        if prev is not None and it == prev:
            cur += 1
        else:
            cur = 1
            prev = it
        best = max(best, cur)
    return best


def _detect_excessive_repetition(text: str) -> Optional[Dict[str, Any]]:
    normalized = _normalize_newlines(text)
    paras = _split_paragraphs(normalized)
    if len(paras) >= 4:
        run = _max_consecutive_run(paras)
        if run >= 3:
            return {"kind": "paragraph_run", "run": run}
        counts = Counter(paras)
        most, n = counts.most_common(1)[0]
        if n >= 3:
            return {"kind": "paragraph_dupe", "count": n, "sample": most[:60]}

    lines = [ln.strip() for ln in normalized.split("\n") if ln.strip()]
    if len(lines) >= 10:
        run = _max_consecutive_run(lines)
        if run >= 6:
            return {"kind": "line_run", "run": run}
        counts = Counter(lines)
        most, n = counts.most_common(1)[0]
        if n >= 8 and len(most) >= 10:
            return {"kind": "line_dupe", "count": n, "sample": most[:60]}

    compact = re.sub(r"\s+", "", normalized)
    sentences = [s.strip() for s in compact.split("。") if s.strip()]
    if len(sentences) >= 18:
        counts = Counter(sentences)
        most, n = counts.most_common(1)[0]
        if n >= 8 and len(most) >= 14:
            return {"kind": "sentence_dupe", "count": n, "sample": most[:60]}

    return None


def _dedupe_consecutive_paragraphs(text: str) -> str:
    paras = _split_paragraphs(text)
    if not paras:
        return ""
    out: List[str] = []
    prev: Optional[str] = None
    for p in paras:
        if prev is not None and p == prev:
            continue
        out.append(p)
        prev = p
    return "\n\n".join(out).strip()


def _ensure_terminal_period(text: str) -> str:
    s = (text or "").strip()
    if not s:
        return s
    if s.endswith("。"):
        return s
    s = re.sub(r"[、。]+$", "", s).strip()
    return (s + "。").strip()


def _hard_slice_to_spoken_chars(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    count = 0
    out_chars: List[str] = []
    for ch in _normalize_newlines(text):
        if ch in {" ", "\t", "\n", "\r", "\u3000"}:
            out_chars.append(ch)
            continue
        count += 1
        if count > max_chars:
            break
        out_chars.append(ch)
    s = "".join(out_chars).strip()
    s = re.sub(r"[、。]+$", "", s).strip()
    if not s:
        return ""
    return _ensure_terminal_period(s)


def _truncate_paragraph_to_spoken_chars(paragraph: str, max_chars: int) -> str:
    para = _normalize_newlines(paragraph).strip()
    if not para or max_chars <= 0:
        return ""
    parts = [p.strip() for p in para.split("。") if p.strip()]
    kept: List[str] = []
    for part in parts:
        cand = "。".join(kept + [part]).strip()
        cand = _ensure_terminal_period(cand)
        if _a_text_spoken_char_count(cand) <= max_chars:
            kept.append(part)
            continue
        break
    if kept:
        return _ensure_terminal_period("。".join(kept).strip())
    return _hard_slice_to_spoken_chars(para, max_chars)


def _trim_to_max_chars(text: str, max_chars: int) -> str:
    s = _sanitize_text(text)
    paras = _split_paragraphs(s)
    if not paras:
        return ""

    kept: List[str] = []
    for para in paras:
        candidate = "\n\n".join(kept + [para]).strip()
        if _a_text_spoken_char_count(candidate) <= max_chars:
            kept.append(para)
            continue

        prefix = "\n\n".join(kept).strip()
        prefix_chars = _a_text_spoken_char_count(prefix) if prefix else 0
        remaining = max_chars - prefix_chars
        if remaining <= 0:
            break
        truncated = _truncate_paragraph_to_spoken_chars(para, remaining)
        if truncated:
            kept.append(truncated)
        break

    out = "\n\n".join([p.strip() for p in kept if p.strip()]).strip()
    return _ensure_terminal_period(out)


@dataclass(frozen=True)
class SectionPlan:
    index: int
    name: str
    char_budget: int
    goal: str
    content_notes: str


def _load_ch06_pattern_sections() -> List[SectionPlan]:
    pattern_path = repo_paths.repo_root() / "ssot" / "ops" / "OPS_SCRIPT_PATTERNS.yaml"
    obj = yaml.safe_load(pattern_path.read_text(encoding="utf-8"))
    patterns = obj.get("patterns") or []
    for p in patterns:
        if p.get("id") != "ch06_urban_legend_lab_v1":
            continue
        sections = (p.get("plan") or {}).get("sections") or []
        out: List[SectionPlan] = []
        for i, s in enumerate(sections, start=1):
            out.append(
                SectionPlan(
                    index=i,
                    name=str(s.get("name") or "").strip(),
                    char_budget=int(s.get("char_budget") or 0),
                    goal=str(s.get("goal") or "").strip(),
                    content_notes=str(s.get("content_notes") or "").strip(),
                )
            )
        if len(out) != 7:
            raise SystemExit(f"CH06 pattern expected 7 sections, got {len(out)}")
        return out
    raise SystemExit("CH06 pattern ch06_urban_legend_lab_v1 not found in ssot/ops/OPS_SCRIPT_PATTERNS.yaml")


def _planning_row(channel: str, video: str) -> Dict[str, str]:
    csv_path = repo_paths.planning_channels_dir() / f"{str(channel).strip().upper()}.csv"
    if not csv_path.exists():
        raise SystemExit(f"Planning CSV not found: {csv_path}")
    target = str(int(video))  # strip leading zeros
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if str(row.get("動画番号") or "").strip() == target:
                return {k: str(v or "") for k, v in row.items()}
    raise SystemExit(f"Video {video} not found in planning CSV: {csv_path}")


def _find_qwen_bin(explicit: str | None) -> str:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"qwen not found at --qwen-bin: {explicit}")
        return str(p)
    found = shutil.which("qwen")
    if found:
        return found
    raise SystemExit("qwen CLI not found. Install `qwen` and ensure it is on PATH.")


def _run_qwen_cli(*, qwen_bin: str, prompt: str, model: str | None, cwd: Path, timeout_sec: int) -> tuple[int, str, str, float]:
    cmd: List[str] = [qwen_bin, "--output-format", "text", "--chat-recording", "false"]
    if model:
        cmd.extend(["-m", model])
    cmd.extend(["-p", prompt])
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as e:
        stdout = e.stdout if isinstance(e.stdout, str) else ""
        stderr = e.stderr if isinstance(e.stderr, str) else ""
        return 124, stdout, stderr + "\n(timeout)", time.time() - t0
    return proc.returncode, proc.stdout or "", proc.stderr or "", time.time() - t0


def _section_min_chars(budget: int) -> int:
    return int(round(budget * 0.9))


def _section_max_chars(budget: int) -> int:
    return int(round(budget * 1.2))


def _build_section_prompt(*, row: Dict[str, str], section: SectionPlan, prev_tail: str | None, strict: bool = False) -> str:
    title = row.get("タイトル", "").strip()
    key_concept = row.get("キーコンセプト", "").strip()
    metaphor = row.get("たとえ話イメージ", "").strip()
    life_scene = row.get("ライフシーン", "").strip()
    benefit = row.get("ベネフィット一言", "").strip()
    plan_intent = row.get("企画意図", "").strip()

    min_chars = _section_min_chars(section.char_budget)
    max_chars = _section_max_chars(section.char_budget)

    prev_block = ""
    if prev_tail:
        tail = _sanitize_text(prev_tail)
        if tail:
            prev_block = f"\n直前の文脈（末尾の抜粋）。自然に接続して続ける。\n{tail}\n"

    section_specific = ""
    if section.index == 7:
        section_specific = (
            "\n終盤の追加条件。\n"
            "統合仮説を2案出す。\n"
            "一つ目は装置としての監視網。\n"
            "二つ目は封印としての黒塗りと削除。\n"
            "統合の要として 断片をつなぐと筋が見える を本文に1回だけ入れる。\n"
            "最後は断定せず、視聴者の解釈に委ねる余韻で完結させる。\n"
        )

    strict_block = ""
    if strict:
        strict_block = (
            "\n追加の厳格条件。\n"
            "同じ文を繰り返さない。同じ段落を繰り返さない。反復のループを起こさない。\n"
            f"本文は必ず{max_chars}字以内。\n"
        )

    return _normalize_newlines(
        f"""あなたはYouTubeチャンネル「都市伝説のダーク図書館」の専属脚本家です。
深夜ラジオのミステリーテラーのように、静かで重い興奮で語ります。断定しすぎません。

重要。
出力は必ず日本語。

出力の絶対条件。
出力は台本本文のみ。前置き、要約、自己評価、見出し、章番号、パート表記、チャンネル名の単独行などのラベルを絶対に出さない。
箇条書き、番号リスト、URL、コード、設定、ファイルパスの混入は禁止。
疑問符の記号を使わない。
丸括弧を使わない。半角丸括弧も使わない。
かぎ括弧と引用符を使わない。
英単語を混ぜない。
区切り記号---は出力しない。

題材。
{title}
キーコンセプトは{key_concept}。
生活の場面は{life_scene}。
たとえ話イメージは{metaphor}。
ベネフィットは{benefit}。

企画意図。
{plan_intent}

禁忌モジュール。
黒塗り、削除、沈黙、封印、一致 を物語の途中に薄く混ぜる。

不確かな固有名詞や数値を捏造しない。
確度が低い要素は 伝えられている、指摘されている、報告がある、など距離を置く。

この出力は全7セクションのうち第{section.index}セクション。
セクション名は{section.name}。
狙いは{section.goal}。
注意点は{section.content_notes}。
{section_specific}{strict_block}

長さ。
本文の分量は少なくとも{min_chars}字以上。
多くても{max_chars}字以内。
言い換えで水増ししない。
段落は適度に。{prev_block}
出力はここから本文のみ。
"""
    ).strip() + "\n"


def _build_expand_prompt(*, section_text: str, section: SectionPlan, needed_hint: int) -> str:
    tail = _sanitize_text(section_text[-700:])
    max_chars = _section_max_chars(section.char_budget)
    current = _a_text_spoken_char_count(section_text)
    remaining = max(0, int(max_chars - current))
    needed = min(int(needed_hint), 1200, remaining)
    needed = min(max(120, needed), remaining) if remaining > 0 else 0
    extra = ""
    if section.index == 7:
        extra = "\n注意。\nこの追記では指定フレーズは書かない。\n"
    return _normalize_newlines(
        f"""あなたはYouTubeチャンネル「都市伝説のダーク図書館」の専属脚本家です。

重要。
出力は追記の台本本文のみ。
前置き、要約、見出し、章番号、パート表記などのラベルは禁止。
箇条書き、番号リスト、URL、コード、設定の混入は禁止。
疑問符の記号を使わない。
丸括弧を使わない。半角丸括弧も使わない。
かぎ括弧と引用符を使わない。
英単語を混ぜない。
区切り記号---は出力しない。

目的。
第{section.index}セクション{section.name}の本文を厚くする。
言い換え水増しは禁止。観察や手続きや手触りの具体を足して密度を上げる。
黒塗り、削除、沈黙、封印、一致 のうち2語以上を薄く入れる。
追記はだいたい{needed}字前後。ただし合計が{max_chars}字を超えないようにする。
{extra}

直前の本文の末尾。
{tail}

出力はここから追記本文のみ。
"""
    ).strip() + "\n"


def _assemble_sections(sections: List[str]) -> str:
    return ("\n\n---\n\n".join([s.strip() for s in sections]).strip() + "\n")


def _status_payload(*, channel: str, video: str, title: str, assembled_chars: int) -> Dict[str, Any]:
    return {
        "script_id": f"{channel}-{video}",
        "channel": channel,
        "channel_code": channel,
        "video_number": video,
        "status": "script_validated",
        "stages": {},
        "metadata": {
            "assembled_path": f"workspaces/scripts/{channel}/{video}/content/assembled.md",
            "assembled_characters": int(assembled_chars),
            "sheet_title": title,
            "expected_title": title,
            "title": title,
            "title_sanitized": title,
        },
    }


def _write_text_pair(*, base_dir: Path, assembled: str) -> tuple[Path, Path]:
    content_dir = base_dir / "content"
    _ensure_dir(content_dir)
    assembled_human = content_dir / "assembled_human.md"
    assembled_mirror = content_dir / "assembled.md"
    assembled_human.write_text(assembled, encoding="utf-8")
    assembled_mirror.write_text(assembled, encoding="utf-8")
    return assembled_human, assembled_mirror


def _write_status(*, base_dir: Path, payload: Dict[str, Any]) -> Path:
    status_path = base_dir / "status.json"
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return status_path


def _log_write(path: Path, text: str) -> None:
    _ensure_dir(path.parent)
    path.write_text(_normalize_newlines(text), encoding="utf-8")


def run_for_video(
    *,
    channel: str,
    video: str,
    qwen_bin: str,
    qwen_model: str | None,
    scratch_dir: Path,
    timeout_sec: int,
    overwrite: bool,
    dry_run: bool,
) -> Dict[str, Any]:
    vv = _z3(video)
    base_dir = repo_paths.video_root(channel, vv)
    logs_dir = base_dir / "logs"
    content_dir = base_dir / "content"
    assembled_human = content_dir / "assembled_human.md"

    if assembled_human.exists() and not overwrite:
        return {"script_id": f"{channel}-{vv}", "skipped": True, "reason": "assembled_human_exists"}

    row = _planning_row(channel, vv)
    title = row.get("タイトル", "").strip() or f"{channel}-{vv}"
    sections_plan = _load_ch06_pattern_sections()

    _ensure_dir(scratch_dir)
    _ensure_dir(logs_dir)

    stamp = _utc_now_compact()
    meta: Dict[str, Any] = {
        "schema_version": 1,
        "tool": "qwen_cli_generate_scripts",
        "at": _utc_now_iso(),
        "script_id": f"{channel}-{vv}",
        "channel": channel,
        "video": vv,
        "title": title,
        "qwen_bin": qwen_bin,
        "qwen_model": qwen_model or "",
        "cwd": str(scratch_dir),
        "sections": [],
        "expansions": [],
        "regens": [],
    }

    generated_sections: List[str] = []
    prev_tail: Optional[str] = None

    for section in sections_plan:
        prompt = _build_section_prompt(row=row, section=section, prev_tail=prev_tail)

        prompt_path = logs_dir / f"qwen_cli_prompt_part{section.index:02d}_{stamp}.txt"
        stdout_path = logs_dir / f"qwen_cli_stdout_part{section.index:02d}_{stamp}.txt"
        stderr_path = logs_dir / f"qwen_cli_stderr_part{section.index:02d}_{stamp}.txt"
        _log_write(prompt_path, prompt)

        if dry_run:
            rc, out_text, err_text, elapsed = 0, "", "", 0.0
            _log_write(stdout_path, "[DRY_RUN]\n")
            _log_write(stderr_path, "")
        else:
            rc, out_text, err_text, elapsed = _run_qwen_cli(
                qwen_bin=qwen_bin,
                prompt=prompt,
                model=qwen_model,
                cwd=scratch_dir,
                timeout_sec=timeout_sec,
            )
            _log_write(stdout_path, out_text)
            _log_write(stderr_path, err_text)

        cleaned = _sanitize_text(out_text)
        cur_chars = _a_text_spoken_char_count(cleaned)
        min_chars = _section_min_chars(section.char_budget)
        max_chars = _section_max_chars(section.char_budget)

        meta["sections"].append(
            {
                "section": section.index,
                "name": section.name,
                "target_chars_hint": section.char_budget,
                "min_chars": min_chars,
                "max_chars": max_chars,
                "final_spoken_chars": cur_chars,
                "exit_code": rc,
                "elapsed_sec": elapsed,
                "prompt_log": str(prompt_path),
                "stdout_log": str(stdout_path),
                "stderr_log": str(stderr_path),
            }
        )

        # Expand if too short (skip in dry-run)
        expansions = 0
        while not dry_run and cur_chars < min_chars and expansions < 3:
            expansions += 1
            remaining = max_chars - cur_chars
            if remaining <= 0:
                break
            needed_hint = min(min_chars - cur_chars, remaining)
            if needed_hint <= 0:
                break
            exp_prompt = _build_expand_prompt(section_text=cleaned, section=section, needed_hint=needed_hint)
            exp_prompt_path = logs_dir / f"qwen_cli_prompt_part{section.index:02d}_ext{expansions:02d}_{stamp}.txt"
            exp_stdout_path = logs_dir / f"qwen_cli_stdout_part{section.index:02d}_ext{expansions:02d}_{stamp}.txt"
            exp_stderr_path = logs_dir / f"qwen_cli_stderr_part{section.index:02d}_ext{expansions:02d}_{stamp}.txt"
            _log_write(exp_prompt_path, exp_prompt)
            rc2, out2, err2, elapsed2 = _run_qwen_cli(
                qwen_bin=qwen_bin,
                prompt=exp_prompt,
                model=qwen_model,
                cwd=scratch_dir,
                timeout_sec=timeout_sec,
            )
            _log_write(exp_stdout_path, out2)
            _log_write(exp_stderr_path, err2)
            add = _sanitize_text(out2)
            if add:
                cleaned = (cleaned.rstrip("\n") + "\n\n" + add.lstrip("\n")).strip()
            cur_chars = _a_text_spoken_char_count(cleaned)
            meta["sections"][-1]["final_spoken_chars"] = cur_chars
            meta["expansions"].append(
                {
                    "section": section.index,
                    "iteration": expansions,
                    "needed_hint": needed_hint,
                    "exit_code": rc2,
                    "elapsed_sec": elapsed2,
                    "prompt_log": str(exp_prompt_path),
                    "stdout_log": str(exp_stdout_path),
                    "stderr_log": str(exp_stderr_path),
                    "final_spoken_chars": cur_chars,
                }
            )

        # Guard: prevent runaway repetition and enforce per-section max length.
        if not dry_run and cleaned:
            repetition_info = _detect_excessive_repetition(cleaned)
            regen_attempts = 0
            while (repetition_info is not None or cur_chars > max_chars) and regen_attempts < 2:
                regen_attempts += 1
                trigger = {"repetition": repetition_info, "over_max": bool(cur_chars > max_chars)}

                regen_prompt = _build_section_prompt(row=row, section=section, prev_tail=prev_tail, strict=True)
                regen_prompt_path = (
                    logs_dir / f"qwen_cli_prompt_part{section.index:02d}_regen{regen_attempts:02d}_{stamp}.txt"
                )
                regen_stdout_path = (
                    logs_dir / f"qwen_cli_stdout_part{section.index:02d}_regen{regen_attempts:02d}_{stamp}.txt"
                )
                regen_stderr_path = (
                    logs_dir / f"qwen_cli_stderr_part{section.index:02d}_regen{regen_attempts:02d}_{stamp}.txt"
                )
                _log_write(regen_prompt_path, regen_prompt)
                rc3, out3, err3, elapsed3 = _run_qwen_cli(
                    qwen_bin=qwen_bin,
                    prompt=regen_prompt,
                    model=qwen_model,
                    cwd=scratch_dir,
                    timeout_sec=timeout_sec,
                )
                _log_write(regen_stdout_path, out3)
                _log_write(regen_stderr_path, err3)

                cleaned = _sanitize_text(out3)
                cur_chars = _a_text_spoken_char_count(cleaned)
                meta["sections"][-1]["final_spoken_chars"] = cur_chars
                meta["regens"].append(
                    {
                        "section": section.index,
                        "iteration": regen_attempts,
                        "trigger": trigger,
                        "exit_code": rc3,
                        "elapsed_sec": elapsed3,
                        "prompt_log": str(regen_prompt_path),
                        "stdout_log": str(regen_stdout_path),
                        "stderr_log": str(regen_stderr_path),
                        "final_spoken_chars": cur_chars,
                    }
                )
                repetition_info = _detect_excessive_repetition(cleaned) if cleaned else None

            if repetition_info is not None and cleaned:
                before = cur_chars
                deduped = _dedupe_consecutive_paragraphs(cleaned)
                if deduped and deduped != cleaned:
                    cleaned = _ensure_terminal_period(deduped)
                    cur_chars = _a_text_spoken_char_count(cleaned)
                    meta["sections"][-1]["dedupe"] = {
                        "from_spoken_chars": before,
                        "to_spoken_chars": cur_chars,
                        "reason": repetition_info,
                    }
                    meta["sections"][-1]["final_spoken_chars"] = cur_chars
                repetition_info = _detect_excessive_repetition(cleaned) if cleaned else None

            if cleaned and cur_chars > max_chars:
                before = cur_chars
                cleaned = _trim_to_max_chars(cleaned, max_chars)
                cur_chars = _a_text_spoken_char_count(cleaned)
                meta["sections"][-1]["trim"] = {"from_spoken_chars": before, "to_spoken_chars": cur_chars}
                meta["sections"][-1]["final_spoken_chars"] = cur_chars

        # After guard operations, best-effort expand again if we became too short.
        post_expansions = 0
        while not dry_run and cur_chars < min_chars and post_expansions < 5:
            remaining = max_chars - cur_chars
            if remaining <= 0:
                break
            post_expansions += 1
            needed_hint = min(min_chars - cur_chars, remaining)
            if needed_hint <= 0:
                break
            exp_prompt = _build_expand_prompt(section_text=cleaned, section=section, needed_hint=needed_hint)
            exp_prompt_path = logs_dir / f"qwen_cli_prompt_part{section.index:02d}_postext{post_expansions:02d}_{stamp}.txt"
            exp_stdout_path = logs_dir / f"qwen_cli_stdout_part{section.index:02d}_postext{post_expansions:02d}_{stamp}.txt"
            exp_stderr_path = logs_dir / f"qwen_cli_stderr_part{section.index:02d}_postext{post_expansions:02d}_{stamp}.txt"
            _log_write(exp_prompt_path, exp_prompt)
            rc2, out2, err2, elapsed2 = _run_qwen_cli(
                qwen_bin=qwen_bin,
                prompt=exp_prompt,
                model=qwen_model,
                cwd=scratch_dir,
                timeout_sec=timeout_sec,
            )
            _log_write(exp_stdout_path, out2)
            _log_write(exp_stderr_path, err2)
            add = _sanitize_text(out2)
            if add:
                cleaned = (cleaned.rstrip("\n") + "\n\n" + add.lstrip("\n")).strip()
            cur_chars = _a_text_spoken_char_count(cleaned)
            meta["sections"][-1]["final_spoken_chars"] = cur_chars
            meta["expansions"].append(
                {
                    "section": section.index,
                    "phase": "post_guard",
                    "iteration": post_expansions,
                    "needed_hint": needed_hint,
                    "exit_code": rc2,
                    "elapsed_sec": elapsed2,
                    "prompt_log": str(exp_prompt_path),
                    "stdout_log": str(exp_stdout_path),
                    "stderr_log": str(exp_stderr_path),
                    "final_spoken_chars": cur_chars,
                }
            )

        # Post expansions can still overshoot; enforce max again.
        if not dry_run and cleaned and cur_chars > max_chars:
            before = cur_chars
            cleaned = _trim_to_max_chars(cleaned, max_chars)
            cur_chars = _a_text_spoken_char_count(cleaned)
            meta["sections"][-1]["post_trim"] = {"from_spoken_chars": before, "to_spoken_chars": cur_chars}
            meta["sections"][-1]["final_spoken_chars"] = cur_chars

        # Section 7: if phrase appears as standalone first line, fold it into prose.
        if section.index == 7 and cleaned:
            phrase = "断片をつなぐと筋が見える"
            lines = cleaned.split("\n")
            while lines and not lines[0].strip():
                lines.pop(0)
            if lines and lines[0].strip() == phrase:
                lines.pop(0)
                while lines and not lines[0].strip():
                    lines.pop(0)
                cleaned = (phrase + "。" + "\n\n" + "\n".join(lines)).strip()

        generated_sections.append(cleaned.strip())
        prev_tail = cleaned[-650:] if cleaned else None

    assembled = _assemble_sections(generated_sections)
    total_chars = _a_text_spoken_char_count(assembled)

    # Enforce the CH06 required phrase exactly once in the final section.
    phrase = "断片をつなぐと筋が見える"
    phrase_count = assembled.count(phrase)
    if phrase_count == 0 and generated_sections:
        generated_sections[-1] = (phrase + "。" + "\n\n" + generated_sections[-1].lstrip("\n")).strip()
        assembled = _assemble_sections(generated_sections)
        total_chars = _a_text_spoken_char_count(assembled)
    elif phrase_count > 1:
        first = assembled.find(phrase)
        if first >= 0:
            head = assembled[: first + len(phrase)]
            tail = assembled[first + len(phrase) :].replace(phrase, "筋が見える")
            assembled = head + tail
            total_chars = _a_text_spoken_char_count(assembled)

    meta["assembled_spoken_chars"] = total_chars
    meta_path = logs_dir / f"qwen_cli_meta_{stamp}.json"
    _log_write(meta_path, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    if not dry_run:
        _ensure_dir(content_dir)
        _write_text_pair(base_dir=base_dir, assembled=assembled)
        payload = _status_payload(channel=channel, video=vv, title=title, assembled_chars=total_chars)
        _write_status(base_dir=base_dir, payload=payload)

    return {
        "script_id": f"{channel}-{vv}",
        "skipped": False,
        "assembled_spoken_chars": total_chars,
        "meta_log": str(meta_path),
    }


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, help="e.g. CH06")
    parser.add_argument("--videos", required=True, help="Comma/ranges (e.g. 058-093,057)")
    parser.add_argument("--run", action="store_true", help="Actually run qwen and write outputs (default: dry-run)")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing assembled_human.md")
    parser.add_argument("--qwen-bin", default="", help="Path to qwen binary (default: resolve from PATH)")
    parser.add_argument("--qwen-model", default="", help="Optional qwen model name (passed to -m)")
    parser.add_argument("--timeout-sec", type=int, default=1800)
    parser.add_argument("--scratch-dir", default="/tmp/qwen_cli_scratch")

    args = parser.parse_args(argv)

    channel = str(args.channel).strip().upper()
    videos = _parse_videos(str(args.videos))
    if not videos:
        raise SystemExit("No videos to process. Provide --videos, e.g. 058-093")

    qwen_bin = _find_qwen_bin(args.qwen_bin or None)
    qwen_model = (str(args.qwen_model).strip() or None) if args.qwen_model else None
    scratch_dir = Path(str(args.scratch_dir))
    dry_run = not bool(args.run)

    failures: List[str] = []
    for vv in videos:
        try:
            res = run_for_video(
                channel=channel,
                video=vv,
                qwen_bin=qwen_bin,
                qwen_model=qwen_model,
                scratch_dir=scratch_dir,
                timeout_sec=int(args.timeout_sec),
                overwrite=bool(args.overwrite),
                dry_run=dry_run,
            )
            if res.get("skipped"):
                print(f"[skip] {res['script_id']}: {res.get('reason')}")
                continue
            print(
                f"[ok] {res['script_id']}: assembled_spoken_chars={res.get('assembled_spoken_chars')} meta={res.get('meta_log')}"
            )
        except Exception as e:
            failures.append(f"{channel}-{vv}: {e}")
            print(f"[fail] {channel}-{vv}: {e}", file=sys.stderr)

    if failures:
        print("\nFailures:", file=sys.stderr)
        for f in failures:
            print(f"- {f}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
