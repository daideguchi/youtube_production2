#!/usr/bin/env python3
"""
Marathon (longform) A-text composer.

Goal:
- Produce 2–3 hour class narration scripts without drift/repetition by design.
- Avoid "LLM reads full script" patterns that break at long context.

Default behavior:
- DRY-RUN: write candidates under `content/analysis/longform/` only.
- `--apply`: overwrite canonical script files (chapters + assembled).

Agent/think mode:
- This script intentionally does NOT abort on the first pending task.
- It enqueues a single prerequisite (plan) first; rerun to proceed step-by-step.
  (Sequential generation needs the previous chapter tail for coherence.)

Related SSOT:
- ssot/ops/OPS_LONGFORM_SCRIPT_SCALING.md
- ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md
"""

from __future__ import annotations

import argparse
from collections import Counter
import math
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=True)

from factory_common.agent_mode import (
    agent_mode_enabled_for_task,
    compute_task_id,
    ensure_pending_task,
    get_queue_dir,
    pending_path,
    read_result_content,
    results_path,
)
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock
from factory_common.paths import repo_root, script_data_root
from factory_common.llm_router import get_router
from packages.script_pipeline.runner import ensure_status
from packages.script_pipeline.sot import save_status
from packages.script_pipeline.validator import validate_a_text


_TAG_RE = re.compile(r"【([^】]+)】")
_EARLY_CLOSING_LINE_RE = re.compile(r"^(?:最後に|まとめると|結論として|おわりに|以上|最後は|最後です)[、。]")
_CTA_PHRASE_RE = re.compile(
    r"(?:ご視聴ありがとうございました|チャンネル登録|高評価|通知(?:を)?オン|通知設定|ベル(?:を)?(?:鳴ら|オン|押)|コメント(?:欄)?(?:で|に)?(?:教えて|書いて|残して)|コメントお願いします)"
)

_LONGFORM_BLOCK_TEMPLATES_PATH = repo_root() / "configs" / "longform_block_templates.json"

def _soften_premature_closing_phrases(text: str) -> str:
    """
    Deterministic micro-fix for common mid-script phrasing that triggers premature closing checks.
    This is intentionally conservative (no paraphrasing) to avoid "retry spam" and choppy scripts.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    out_lines: list[str] = []
    for line in normalized.split("\n"):
        stripped = line.lstrip()
        indent = line[: len(line) - len(stripped)]
        if stripped.startswith("最後に"):
            rest = stripped[len("最後に") :].lstrip()
            if rest.startswith("、") or rest.startswith(","):
                rest = rest[1:].lstrip()
            out_lines.append(indent + "もう一つは、" + rest)
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _normalize_channel(ch: str) -> str:
    s = (ch or "").strip().upper()
    if re.fullmatch(r"CH\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _normalize_video(no: str) -> str:
    s = (no or "").strip()
    try:
        return f"{int(s):03d}"
    except Exception:
        return s.zfill(3)


def _extract_bracket_tag(text: str | None) -> str:
    raw = str(text or "")
    m = _TAG_RE.search(raw)
    return (m.group(1) or "").strip() if m else ""


def _sanitize_context(text: str, *, max_chars: int) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
    # Reduce prompt bloat and accidental quote-count hallucinations.
    for ch in ("`", "「", "」", "『", "』", "（", "）", "(", ")"):
        raw = raw.replace(ch, "")
    raw = "\n".join([ln.strip() for ln in raw.split("\n") if ln.strip()])
    if len(raw) > max_chars:
        raw = raw[:max_chars].rstrip()
    return raw


def _extract_llm_text_content(result: Dict[str, Any]) -> str:
    content = result.get("content")
    if isinstance(content, list):
        parts: List[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")).strip())
        return " ".join([p for p in parts if p]).strip()
    return str(content or "").strip()


def _assert_not_locked(paths: list[Path]) -> None:
    locks = default_active_locks_for_mutation()
    for p in paths:
        lock = find_blocking_lock(p, locks)
        if lock:
            raise SystemExit(
                f"Blocked by lock {lock.lock_id} (mode={lock.mode}, by={lock.created_by}) for path: {p}"
            )


def _chars_spoken(text: str) -> int:
    """Match validate_a_text() char_count heuristic: exclude `---` lines + whitespace/newlines."""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines: List[str] = []
    for line in normalized.split("\n"):
        if line.strip() == "---":
            continue
        lines.append(line)
    compact = "".join(lines).replace(" ", "").replace("\t", "").replace("\u3000", "")
    return len(compact.strip())


def _derive_targets_from_duration(minutes: int, chars_per_minute: int) -> tuple[int, int]:
    aim = max(1, int(minutes) * int(chars_per_minute))
    # Keep a small band; longform tends to vary by speaking speed.
    return int(aim * 0.92), int(aim * 1.08)


def _derive_chapter_count(*, aim_chars: int, per_chapter_aim: int) -> int:
    per = max(600, int(per_chapter_aim))
    # Clamp so we don't create absurdly small/large chapter counts by default.
    return max(24, min(96, int(round(aim_chars / per))))


@dataclass(frozen=True)
class PlanChapter:
    chapter: int
    block: int
    block_title: str
    goal: str
    must_include: list[str]
    avoid: list[str]
    char_budget: int
    closing_allowed: bool


@dataclass(frozen=True)
class Plan:
    schema: str
    generated_at: str
    title: str
    channel: str
    video: str
    target_chars_min: int
    target_chars_max: int
    chapter_count: int
    blocks: list[dict[str, Any]]
    chapters: list[PlanChapter]
    core_message: str

    def as_json(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at": self.generated_at,
            "channel": self.channel,
            "video": self.video,
            "title": self.title,
            "target_chars_min": self.target_chars_min,
            "target_chars_max": self.target_chars_max,
            "chapter_count": self.chapter_count,
            "core_message": self.core_message,
            "blocks": self.blocks,
            "chapters": [
                {
                    "chapter": c.chapter,
                    "block": c.block,
                    "block_title": c.block_title,
                    "goal": c.goal,
                    "must_include": c.must_include,
                    "avoid": c.avoid,
                    "char_budget": c.char_budget,
                    "closing_allowed": c.closing_allowed,
                }
                for c in self.chapters
            ],
        }


_MEMORY_TOKEN_RE = re.compile(r"[一-龯]{2,}|[ぁ-ん]{2,}|[ァ-ヴー]{2,}|[A-Za-z0-9]{2,}")
_MEMORY_STOPWORDS = {
    "あなた",
    "人生",
    "本当",
    "方法",
    "理由",
    "今",
    "今日",
    "すぐ",
    "なぜ",
    "どう",
    "それ",
    "これ",
    "そして",
    "しかし",
    "ため",
    "人",
    "人間",
    "心",
    "世界",
    "自分",
    "自分自身",
    "私",
    "僕",
    "私たち",
    "結局",
    "大事",
    "重要",
    "知る",
    "知ら",
    "できる",
    "して",
    "いる",
    "ある",
    "ない",
}


def _tokenize_memory(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _MEMORY_TOKEN_RE.findall(text or ""):
        tok = raw.lower() if raw.isascii() else raw
        if tok in _MEMORY_STOPWORDS:
            continue
        tokens.append(tok)
    return tokens


def _top_keywords(texts: list[str], *, max_keywords: int) -> list[str]:
    limit = max(0, int(max_keywords))
    if limit <= 0:
        return []
    freq: Counter = Counter()
    for txt in texts:
        for tok in _tokenize_memory(txt):
            freq[tok] += 1
    items = sorted(freq.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [k for k, _ in items[:limit]]


def _build_memory_snapshot(
    plan: Plan,
    drafted: list[tuple[PlanChapter, str]],
    *,
    max_keywords: int,
    max_must: int,
) -> dict[str, Any]:
    must_flat: list[str] = []
    for ch, _txt in drafted:
        for raw in (ch.must_include or []):
            s = str(raw or "").strip()
            if s and s not in must_flat:
                must_flat.append(s)
    if int(max_must) > 0 and len(must_flat) > int(max_must):
        must_flat = must_flat[-int(max_must) :]

    snapshot = {
        "schema": "ytm.longform_memory.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": plan.channel,
        "video": plan.video,
        "title": plan.title,
        "chapter_count": int(plan.chapter_count),
        "covered_chapters": int(len(drafted)),
        "covered_blocks": sorted({int(ch.block) for ch, _ in drafted}),
        "core_message": plan.core_message,
        "covered_must_include": must_flat,
        "keywords": _top_keywords([txt for _ch, txt in drafted], max_keywords=max_keywords),
    }
    return snapshot


def _format_memory_for_prompt(snapshot: dict[str, Any], *, max_chars: int) -> str:
    if not snapshot:
        return ""
    covered = int(snapshot.get("covered_chapters") or 0)
    if covered <= 0:
        return ""
    total = int(snapshot.get("chapter_count") or 0)
    blocks = snapshot.get("covered_blocks") or []
    musts = snapshot.get("covered_must_include") or []
    keywords = snapshot.get("keywords") or []

    lines: list[str] = []
    if total > 0:
        lines.append(f"- 進捗: {covered}/{total}章")
    else:
        lines.append(f"- 進捗: {covered}章")
    if blocks:
        lines.append("- 完了ブロック: " + ", ".join(str(b) for b in blocks))
    if musts:
        lines.append("- 既出must_include(抜粋): " + " / ".join(str(m) for m in musts))
    if keywords:
        lines.append("- 既出キーワード(重複説明禁止): " + " / ".join(str(k) for k in keywords))
    lines.append("- 方針: 上の内容を“定義し直す/言い換えて水増しする”のは禁止。新しい理解を追加する。")

    out = "\n".join(lines).strip()
    if int(max_chars) > 0 and len(out) > int(max_chars):
        out = out[: int(max_chars)].rstrip() + "…"
    return out


def _chapter_summary_one_line(text: str, *, max_chars: int = 160) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return ""
    # Take the first sentence-like chunk for a cheap, deterministic summary.
    snippet = normalized[:800]
    for sep in ("。", "！", "?", "？", "!"):
        idx = snippet.find(sep)
        if idx != -1 and idx >= 24:
            snippet = snippet[: idx + 1]
            break
    if int(max_chars) > 0 and len(snippet) > int(max_chars):
        snippet = snippet[: int(max_chars)].rstrip() + "…"
    return snippet


def _excerpt_head_tail(text: str, *, head_chars: int, tail_chars: int) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return "", ""
    head = normalized[: max(0, int(head_chars))].strip() if int(head_chars) > 0 else ""
    tail = normalized[-max(0, int(tail_chars)) :].strip() if int(tail_chars) > 0 else ""
    if head and len(normalized) > int(head_chars) and not head.endswith(("。", "！", "?", "？", "!", "…")):
        head = head.rstrip() + "…"
    if tail and len(normalized) > int(tail_chars) and not tail.startswith(("…",)):
        tail = "…" + tail.lstrip()
    return head, tail


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty JSON")
    try:
        obj = json.loads(raw)
    except Exception:
        # Fallback: extract the first {...} block (guards against stray prose).
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                obj = json.loads(raw[start : end + 1])
            except Exception as exc:
                raise ValueError(f"invalid JSON: {exc}") from exc
        else:
            raise ValueError("invalid JSON: no object braces found")
    if not isinstance(obj, dict):
        raise ValueError("JSON is not an object")
    return obj


def _coerce_int_list(value: Any) -> list[int]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        return []
    out: list[int] = []
    for item in value:
        try:
            out.append(int(item))
        except Exception:
            continue
    return out


def _longform_block_judge_prompt(
    *,
    title: str,
    core_message: str,
    block: dict[str, Any],
    chapters: list[tuple[PlanChapter, str]],
    memory_note: str,
    max_fix_per_block: int,
) -> str:
    block_id = int(block.get("block") or 0) if isinstance(block, dict) else 0
    block_title = str(block.get("title") or "").strip() if isinstance(block, dict) else ""
    block_goal = str(block.get("goal") or "").strip() if isinstance(block, dict) else ""

    lines: list[str] = []
    lines.append("あなたは日本語のYouTubeナレーション台本（Aテキスト）の編集長です。")
    lines.append("超長尺（2〜3時間級）を“全文LLM禁止”で品質固定するため、章の要約＋抜粋だけで判定します。")
    lines.append("出力は JSON のみ（説明文禁止）。")
    lines.append("")
    lines.append(f"【企画タイトル】{title}")
    lines.append("")
    lines.append("【コアメッセージ（全章でブレない）】")
    lines.append(core_message.strip() or "(空)")
    lines.append("")
    lines.append(f"【今回のブロック】{block_id}: {block_title}".strip())
    if block_goal:
        lines.append(f"【ブロックの狙い】{block_goal}")
    lines.append("")
    lines.append("【Memory（既に扱った内容。繰り返し禁止）】")
    lines.append(memory_note.strip() if memory_note else "(なし)")
    lines.append("")
    lines.append("【入力（章ごとの要約＋抜粋）】")
    for ch, txt in chapters:
        head, tail = _excerpt_head_tail(txt, head_chars=360, tail_chars=360)
        must = [str(x).strip() for x in (ch.must_include or []) if str(x).strip()]
        avoid = [str(x).strip() for x in (ch.avoid or []) if str(x).strip()]
        lines.append("")
        lines.append(f"- chapter: {int(ch.chapter)}")
        lines.append(f"  goal: {str(ch.goal or '').strip()}")
        lines.append(f"  must_include: {must if must else []}")
        lines.append(f"  avoid: {avoid if avoid else []}")
        lines.append(f"  char_count: {_chars_spoken(txt)}")
        lines.append(f"  summary: {_chapter_summary_one_line(txt)}")
        lines.append(f"  excerpt_head: {head}")
        lines.append(f"  excerpt_tail: {tail}")

    lines.append("")
    lines.append("【判定基準（このブロック内で見る）】")
    lines.append("- 企画タイトル/コアメッセージから逸脱していないか")
    lines.append("- 同趣旨の言い換えで水増ししていないか（新しい理解が増えているか）")
    lines.append("- 章同士が似すぎていないか（同じ結論/同じ説明の繰り返し）")
    lines.append("- 途中章で締め/結論/まとめの雰囲気が出ていないか")
    lines.append("- 具体が“穴埋め”になっていないか（例の連打で中身が増えていない）")
    lines.append("- 根拠不明の統計/研究/固有名詞/数字断定など信頼を壊す要素がないか")
    lines.append("")
    lines.append("【制約（重要）】")
    lines.append(f"- rewrite を提案してよい章は最大 {int(max_fix_per_block)} 章まで（本当に悪い章だけ）。")
    lines.append("- rewrite は“主題を増やさず”、その章のゴールを達成するために“理解が増える具体”を追加する方針にする。")
    lines.append("- タイトル語句の機械一致（単語が出るか）は必須にしない。意味として回収できていればOK。")
    lines.append("")
    lines.append("【出力JSONスキーマ（厳守）】")
    lines.append('{')
    lines.append('  "schema": "ytm.longform_block_judge.v1",')
    lines.append(f'  "block": {block_id},')
    lines.append('  "verdict": "pass" | "fail",')
    lines.append('  "reasons": ["..."],')
    lines.append('  "must_fix_chapters": [12, 13],')
    lines.append('  "chapter_fixes": [')
    lines.append('    {')
    lines.append('      "chapter": 12,')
    lines.append('      "problem": "何が悪いか（短く）",')
    lines.append('      "rewrite_focus": ["何を増やす/どう直すか（最大4）"],')
    lines.append('      "must_keep": ["この章で維持すべき要素（最大3）"],')
    lines.append('      "avoid_more": ["追加で避ける表現/構造（最大3）"]')
    lines.append('    }')
    lines.append('  ]')
    lines.append('}')
    return "\n".join(lines).strip() + "\n"


def _chapter_quality_rewrite_prompt(
    *,
    title: str,
    chapter: PlanChapter,
    chapter_count: int,
    persona: str,
    channel_prompt: str,
    core_message: str,
    previous_tail: str,
    next_head: str,
    memory_note: str,
    quote_max: int,
    paren_max: int,
    quote_total_max: int,
    paren_total_max: int,
    target_min: int,
    target_max: int,
    previous_draft: str,
    judge_problem: str,
    rewrite_focus: list[str],
    must_keep: list[str],
    avoid_more: list[str],
) -> str:
    focus = "\n".join([f"- {x}" for x in (rewrite_focus or []) if str(x).strip()]) or "- (なし)"
    keep = "\n".join([f"- {x}" for x in (must_keep or []) if str(x).strip()]) or "- (なし)"
    avoid_extra = "\n".join([f"- {x}" for x in (avoid_more or []) if str(x).strip()]) or "- (なし)"

    quote_rule = (
        "この章では `「」`/`『』` を一切使わない（0個）。"
        if int(quote_max) <= 0
        else f"この章の `「」`/`『』` は合計 {int(quote_max)} 個以内（可能なら0）。"
    )
    paren_rule = (
        "この章では `（）`/`()` を一切使わない（0個）。"
        if int(paren_max) <= 0
        else f"この章の `（）`/`()` は合計 {int(paren_max)} 個以内（可能なら0）。"
    )

    return (
        "あなたは日本語のYouTubeナレーション台本（Aテキスト）作家です。\n"
        "超長尺（2〜3時間級）の章を、品質理由で“章だけ”書き直します。\n"
        "出力は本文のみ（説明禁止）。\n\n"
        f"【企画タイトル】\n{title}\n\n"
        f"【章】{chapter.chapter}/{chapter_count}（Block {chapter.block}: {chapter.block_title}）\n"
        f"【この章のゴール】\n{chapter.goal}\n\n"
        f"【目標文字数（改行/空白除外の目安）】{int(target_min)}〜{int(target_max)}字\n"
        f"【記号上限（全体→章予算）】全体:「」/『』<= {quote_total_max} / （）<= {paren_total_max} ｜ "
        f"この章:「」/『』<= {quote_max} / （）<= {paren_max}\n\n"
        "【コアメッセージ（全章でブレない）】\n"
        + (core_message.strip() or "(空)")
        + "\n\n"
        "【品質ゲートからの指摘（短く）】\n"
        + (str(judge_problem or "").strip() or "(なし)")
        + "\n\n"
        "【書き直し方針（必ず反映）】\n"
        + focus
        + "\n\n"
        "【維持すべき要素（崩さない）】\n"
        + keep
        + "\n\n"
        "【追加で避けること】\n"
        + avoid_extra
        + "\n\n"
        "【ペルソナ要点】\n"
        + _sanitize_context(persona, max_chars=700)
        + "\n\n"
        "【チャンネル指針要点】\n"
        + _sanitize_context(channel_prompt, max_chars=700)
        + "\n\n"
        "【直前章の末尾（文脈。コピー禁止）】\n"
        + (_sanitize_context(previous_tail, max_chars=320) if previous_tail else "(なし)")
        + "\n\n"
        "【次章の冒頭（文脈。コピー禁止）】\n"
        + (_sanitize_context(next_head, max_chars=240) if next_head else "(なし)")
        + "\n\n"
        "【Memory（既に扱った内容。繰り返し禁止）】\n"
        + (_sanitize_context(memory_note, max_chars=900) if memory_note else "(なし)")
        + "\n\n"
        "【執筆ルール（厳守）】\n"
        "- 出力は本文のみ（見出し/箇条書き/番号リスト/URL/脚注/参照番号/制作メタは禁止）。\n"
        "- 区切り記号は本文に入れない（`---` は後でブロック境界にだけ入れる）。\n"
        f"- {quote_rule} 引用や強調は地の文で言い換える。\n"
        f"- {paren_rule}\n"
        "- 同趣旨の言い換えで水増ししない。厚みは理解が増える具体で作る。\n"
        "- 各段落に「新しい理解」を最低1つ入れる（具体/見立て/手順/落とし穴のいずれか）。言い換えだけの段落は禁止。\n"
        "- 現代の人物例（年齢/職業/台詞の作り込み）は入れない（全体事故の原因）。\n"
        "- 根拠不明の統計/研究/固有名詞/数字断定で説得力を作らない。\n"
        "- 途中章でのまとめ/結論/最後に/締めの挨拶は禁止（最終章だけ例外）。\n"
        "\n"
        "【前回の章本文（参考。コピー禁止）】\n"
        + _sanitize_context(previous_draft, max_chars=3200)
        + "\n\n"
        "では、書き直した本文のみを出力してください。\n"
    )

def _write_longform_sidecars(
    *,
    analysis_dir: Path,
    plan: Plan,
    drafted: list[tuple[PlanChapter, str]],
    max_keywords: int,
    max_must: int,
) -> None:
    analysis_dir.mkdir(parents=True, exist_ok=True)
    memory = _build_memory_snapshot(plan, drafted, max_keywords=max_keywords, max_must=max_must)
    (analysis_dir / "memory.json").write_text(json.dumps(memory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    chapters_payload: list[dict[str, Any]] = []
    for ch, txt in drafted:
        chapters_payload.append(
            {
                "chapter": int(ch.chapter),
                "block": int(ch.block),
                "block_title": ch.block_title,
                "goal": ch.goal,
                "char_count": _chars_spoken(txt),
                "summary": _chapter_summary_one_line(txt),
                "must_include": list(ch.must_include or []),
                "avoid": list(ch.avoid or []),
            }
        )

    summaries = {
        "schema": "ytm.longform_chapter_summaries.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": plan.channel,
        "video": plan.video,
        "title": plan.title,
        "chapters": chapters_payload,
    }
    (analysis_dir / "chapter_summaries.json").write_text(
        json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _call_llm_text(
    *,
    task: str,
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    response_format: str | None = None,
) -> tuple[str | None, dict[str, Any]]:
    """
    Return (content_text or None, meta).

    - API mode: calls router and returns text.
    - agent/think mode: ensures pending task or returns completed result content.
      (Does NOT raise SystemExit; caller decides how to proceed.)
    """
    options: Dict[str, Any] = {"max_tokens": int(max_tokens), "temperature": float(temperature)}
    if response_format:
        options["response_format"] = str(response_format)

    if agent_mode_enabled_for_task(task):
        q = get_queue_dir()
        task_id = compute_task_id(task, messages, options)
        r_path = results_path(task_id, queue_dir=q)
        if r_path.exists():
            return read_result_content(task_id, queue_dir=q), {
                "provider": "agent",
                "model": "agent",
                "request_id": task_id,
                "result_path": str(r_path),
            }
        ensure_pending_task(
            task_id=task_id,
            task=task,
            messages=messages,
            options=options,
            response_format=response_format,
            queue_dir=q,
        )
        p_path = pending_path(task_id, queue_dir=q)
        return None, {
            "provider": "agent",
            "model": "agent",
            "request_id": task_id,
            "pending_path": str(p_path),
        }

    router = get_router()
    result = router.call_with_raw(
        task=task,
        messages=messages,
        max_tokens=int(max_tokens),
        temperature=float(temperature),
        response_format=response_format,
    )
    text = _extract_llm_text_content(result) or ""
    meta = {
        "provider": result.get("provider"),
        "model": result.get("model"),
        "request_id": result.get("request_id"),
        "latency_ms": result.get("latency_ms"),
        "usage": result.get("usage") or {},
        "finish_reason": result.get("finish_reason"),
    }
    return text.strip(), meta


def _default_block_template(block_count: int) -> list[dict[str, Any]]:
    # Keep names neutral so CH13–CH16 can share.
    base = [
        {"block": 1, "title": "導入と約束", "goal": "痛みを掴み、結論方向を先に出す"},
        {"block": 2, "title": "日常の摩耗", "goal": "生活シーンで刺さる具体を積む"},
        {"block": 3, "title": "問題の正体", "goal": "原因を一つに絞って説明する"},
        {"block": 4, "title": "見立て（翻訳）", "goal": "教え/視点を日常語に翻訳する"},
        {"block": 5, "title": "実践", "goal": "今日からできる形に落とす"},
        {"block": 6, "title": "落とし穴と回収", "goal": "誤解を潰し、静かに締める"},
    ]
    if block_count <= 6:
        return base[: max(2, int(block_count))]
    # For longer scripts, split practical block into two.
    extra = [
        {"block": 7, "title": "実践の深掘り", "goal": "つまずきやすい場面別に手当てする"},
        {"block": 8, "title": "最終回収", "goal": "重要点を1つに収束して終える"},
    ]
    return (base + extra)[: int(block_count)]


def _load_longform_block_templates() -> dict[str, Any]:
    try:
        path = _LONGFORM_BLOCK_TEMPLATES_PATH
        if not path.exists():
            return {}
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _select_blocks(*, channel: str, block_count: int, template_key: str) -> list[dict[str, Any]]:
    defaults = _default_block_template(block_count)
    by_block_default: dict[int, dict[str, Any]] = {}
    for b in defaults:
        try:
            n = int(b.get("block") or 0)
        except Exception:
            continue
        if n > 0:
            by_block_default[n] = dict(b)

    cfg = _load_longform_block_templates()
    templates = cfg.get("templates") if isinstance(cfg.get("templates"), dict) else {}
    overrides = cfg.get("channel_overrides") if isinstance(cfg.get("channel_overrides"), dict) else {}

    key = str(template_key or "").strip()
    if not key:
        key = str(overrides.get(channel) or "").strip()
    if not key:
        key = "default"

    tmpl = templates.get(key) if isinstance(templates, dict) else None
    blocks_raw = tmpl.get("blocks") if isinstance(tmpl, dict) else None
    if not isinstance(blocks_raw, list) and key != "default":
        tmpl = templates.get("default") if isinstance(templates, dict) else None
        blocks_raw = tmpl.get("blocks") if isinstance(tmpl, dict) else None

    if not isinstance(blocks_raw, list):
        return defaults

    override_map: dict[int, dict[str, Any]] = {}
    for it in blocks_raw:
        if not isinstance(it, dict):
            continue
        try:
            n = int(it.get("block") or 0)
        except Exception:
            continue
        if n <= 0 or n > int(block_count):
            continue
        title = str(it.get("title") or "").strip()
        goal = str(it.get("goal") or "").strip()
        if not title and not goal:
            continue
        base = dict(by_block_default.get(n) or {"block": n})
        if title:
            base["title"] = title
        if goal:
            base["goal"] = goal
        override_map[n] = base

    merged: list[dict[str, Any]] = []
    for n in range(1, int(block_count) + 1):
        merged.append(dict(override_map.get(n) or by_block_default.get(n) or {"block": n, "title": f"Block{n}", "goal": ""}))
    return merged


def _build_plan_prompt(
    *,
    title: str,
    target_min: int,
    target_max: int,
    chapter_count: int,
    blocks: list[dict[str, Any]],
    persona: str,
    channel_prompt: str,
    core_message_hint: str,
) -> list[dict[str, str]]:
    schema = {
        "core_message": "1〜2文。全章でブレない中核メッセージ",
        "blocks": [
            {"block": 1, "title": "短い見出し", "goal": "そのブロックで達成すること", "chapters": [1, 2, 3]}
        ],
        "chapters": [
            {
                "chapter": 1,
                "block": 1,
                "block_title": "導入と約束",
                "goal": "この章で増やす理解を一つに絞る",
                "must_include": ["必ず入れる観点（最大3）"],
                "avoid": ["避ける脱線/言い回し（最大3）"],
            }
        ],
    }

    system = (
        "あなたは日本語の長尺ナレーション台本を設計する編集者です。"
        "2〜3時間級でも破綻しないよう、章ごとに『扱う論点を一つ』に絞った設計を作ります。"
        "出力は JSON オブジェクトのみ。説明文は禁止。"
    )
    user = (
        f"【企画タイトル】\n{title}\n\n"
        f"【目標文字数（改行/空白/---除外）】min={target_min} / max={target_max}\n"
        f"【章数】{chapter_count}\n\n"
        "【ブロック骨格（固定）】\n"
        + json.dumps(blocks, ensure_ascii=False, indent=2)
        + "\n\n"
        "【ペルソナ要点】\n"
        + _sanitize_context(persona, max_chars=900)
        + "\n\n"
        "【チャンネル指針要点】\n"
        + _sanitize_context(channel_prompt, max_chars=900)
        + "\n\n"
        "【コアメッセージ候補（ヒント）】\n"
        + (core_message_hint.strip() if core_message_hint.strip() else "(空)")
        + "\n\n"
        "【設計ルール（厳守）】\n"
        f"- chapters は必ず {chapter_count} 件。chapter は 1..{chapter_count} の連番（欠番/重複/余剰は禁止）。\n"
        "- 章ごとに扱う論点は1つ。重複する章を作らない。\n"
        "- 中盤章は『まとめ/結論/最後に』のような締めにしない（終わり感を出さない）。\n"
        "- 研究/統計/出典/固有名詞の断定で説得力を作らない。\n"
        "- 現代の人物例（年齢/職業/台詞の作り込み）は全体で最大1件に抑える前提で設計する。\n"
        "- 出力は長文化させない（超長尺でも壊れないように短く設計する）:\n"
        "  - goal: 1文、最大30文字\n"
        "  - must_include/avoid: 各最大3要素、各要素は最大20文字\n"
        "\n"
        "【出力スキーマ（例）】\n"
        + json.dumps(schema, ensure_ascii=False, indent=2)
        + "\n\n"
        "JSONのみを返してください。\n"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_plan_json(obj: dict[str, Any], *, blocks: list[dict[str, Any]], chapter_count: int) -> tuple[str, list[PlanChapter]]:
    core_message = str(obj.get("core_message") or "").strip()
    chapters_raw = obj.get("chapters")
    if not isinstance(chapters_raw, list):
        raise ValueError("plan.chapters missing")

    by_block_title = {int(b.get("block")): str(b.get("title") or "").strip() for b in blocks if b.get("block")}

    chapters: list[PlanChapter] = []
    seen: set[int] = set()
    for it in chapters_raw:
        if not isinstance(it, dict):
            continue
        ch = int(it.get("chapter") or 0)
        if ch <= 0:
            continue
        if ch in seen:
            raise ValueError(f"duplicate chapter {ch}")
        seen.add(ch)
        block = int(it.get("block") or 0) or 1
        block_title = str(it.get("block_title") or by_block_title.get(block) or "").strip()
        goal = str(it.get("goal") or "").strip()
        must_include = it.get("must_include") or []
        avoid = it.get("avoid") or []
        if not isinstance(must_include, list):
            must_include = []
        if not isinstance(avoid, list):
            avoid = []
        must = [str(x).strip() for x in must_include if str(x or "").strip()][:3]
        av = [str(x).strip() for x in avoid if str(x or "").strip()][:3]
        if not goal:
            raise ValueError(f"missing goal for chapter {ch}")
        chapters.append(
            PlanChapter(
                chapter=ch,
                block=max(1, block),
                block_title=block_title or f"Block{block}",
                goal=goal,
                must_include=must,
                avoid=av,
                char_budget=0,  # filled later
                closing_allowed=False,  # filled later
            )
        )

    if len(seen) != int(chapter_count):
        raise ValueError(f"chapters count mismatch: got={len(seen)} expected={chapter_count}")
    chapters.sort(key=lambda c: c.chapter)
    return core_message, chapters


def _assign_budgets(plan_chapters: list[PlanChapter], *, target_min: int, target_max: int) -> list[PlanChapter]:
    # Use the midpoint for stable budgets; keep all chapters near per-chapter aim.
    aim_total = int(round((int(target_min) + int(target_max)) / 2))
    per = max(700, int(round(aim_total / max(1, len(plan_chapters)))))
    out: list[PlanChapter] = []
    for ch in plan_chapters:
        out.append(
            PlanChapter(
                chapter=ch.chapter,
                block=ch.block,
                block_title=ch.block_title,
                goal=ch.goal,
                must_include=list(ch.must_include),
                avoid=list(ch.avoid),
                char_budget=per,
                closing_allowed=(ch.chapter == len(plan_chapters)),
            )
        )
    return out


def _validate_chapter_text(
    text: str,
    *,
    meta: dict[str, Any],
    char_budget: int,
    min_ratio: float,
    max_ratio: float,
    closing_allowed: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = dict(meta or {})
    if char_budget > 0:
        meta["target_chars_min"] = int(round(char_budget * float(min_ratio)))
        meta["target_chars_max"] = int(round(char_budget * float(max_ratio)))
    issues, stats = validate_a_text(text, meta)

    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    # `---` is reserved for block boundaries inserted in assembly.
    for idx, line in enumerate(lines, start=1):
        if line.strip() == "---":
            issues.append(
                {
                    "code": "chapter_pause_not_allowed",
                    "message": "`---` must not appear inside chapter drafts (inserted at block boundaries only)",
                    "line": idx,
                    "severity": "error",
                }
            )

    if not closing_allowed:
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if _EARLY_CLOSING_LINE_RE.match(stripped) or _CTA_PHRASE_RE.search(stripped):
                issues.append(
                    {
                        "code": "premature_closing",
                        "message": "Non-final chapter contains a closing/CTA-like phrase",
                        "line": idx,
                        "severity": "error",
                    }
                )
                break
    return issues, stats


def _chapter_draft_prompt(
    *,
    title: str,
    chapter: PlanChapter,
    chapter_count: int,
    persona: str,
    channel_prompt: str,
    core_message: str,
    previous_tail: str,
    memory_note: str,
    quote_max: int,
    paren_max: int,
    quote_total_max: int,
    paren_total_max: int,
) -> str:
    must = "\n".join([f"- {m}" for m in (chapter.must_include or [])]) if chapter.must_include else "- (なし)"
    avoid = "\n".join([f"- {a}" for a in (chapter.avoid or [])]) if chapter.avoid else "- (なし)"

    closing_rule = (
        "この章は最終章なので、短く締めてよい（締めの言葉は1回だけ）。"
        if chapter.closing_allowed
        else "この章は途中章なので、まとめ/結論/最後に/締めの挨拶は禁止（終わり感を出さない）。"
    )

    quote_rule = (
        "この章では `「」`/`『』` を一切使わない（0個）。"
        if int(quote_max) <= 0
        else f"この章の `「」`/`『』` は合計 {int(quote_max)} 個以内（可能なら0）。"
    )
    paren_rule = (
        "この章では `（）`/`()` を一切使わない（0個）。"
        if int(paren_max) <= 0
        else f"この章の `（）`/`()` は合計 {int(paren_max)} 個以内（可能なら0）。"
    )

    return (
        "あなたは日本語のYouTubeナレーション台本（Aテキスト）作家です。\n"
        "超長尺（2〜3時間級）を章分割で安定して書きます。\n"
        "\n"
        f"【企画タイトル】\n{title}\n\n"
        f"【章】{chapter.chapter}/{chapter_count}（Block {chapter.block}: {chapter.block_title}）\n"
        f"【この章のゴール】\n{chapter.goal}\n\n"
        f"【目標文字数（改行/空白除外の目安）】約{chapter.char_budget}字\n"
        f"【記号上限（全体→章予算）】全体:「」/『』<= {quote_total_max} / （）<= {paren_total_max} ｜ "
        f"この章:「」/『』<= {quote_max} / （）<= {paren_max}\n\n"
        "【コアメッセージ（全章でブレない）】\n"
        + (core_message.strip() or "(空)")
        + "\n\n"
        "【必ず入れる観点（最大3）】\n"
        + must
        + "\n\n"
        "【避けること（最大3）】\n"
        + avoid
        + "\n\n"
        "【ペルソナ要点】\n"
        + _sanitize_context(persona, max_chars=700)
        + "\n\n"
        "【チャンネル指針要点】\n"
        + _sanitize_context(channel_prompt, max_chars=700)
        + "\n\n"
        "【直前章の末尾（文脈。コピー禁止）】\n"
        + (_sanitize_context(previous_tail, max_chars=320) if previous_tail else "(なし)")
        + "\n\n"
        "【Memory（既に扱った内容。繰り返し禁止）】\n"
        + (_sanitize_context(memory_note, max_chars=900) if memory_note else "(なし)")
        + "\n\n"
        "【執筆ルール（厳守）】\n"
        "- 出力は本文のみ（見出し/箇条書き/番号リスト/URL/脚注/参照番号/制作メタは禁止）。\n"
        "- 区切り記号は本文に入れない（`---` は後でブロック境界にだけ入れる）。\n"
        f"- {quote_rule} 引用や強調は地の文で言い換える。\n"
        f"- {paren_rule}\n"
        "- 同趣旨の言い換えで水増ししない。厚みは理解が増える具体で作る。\n"
        "- 各段落に「新しい理解」を最低1つ入れる（具体/見立て/手順/落とし穴のいずれか）。言い換えだけの段落は禁止。\n"
        "- 現代の人物例（年齢/職業/台詞の作り込み）は入れない（全体事故の原因）。\n"
        "- 根拠不明の統計/研究/固有名詞/数字断定で説得力を作らない。\n"
        f"- {closing_rule}\n"
        "\n"
        "では、完成した本文のみを出力してください。\n"
    )


def _chapter_rewrite_prompt(
    *,
    title: str,
    base_prompt: str,
    issues: list[dict[str, Any]],
    previous_draft: str,
    target_min: int | None,
    target_max: int | None,
    retry_round: int,
    retry_total: int,
) -> str:
    lines: list[str] = []
    for it in (issues or [])[:12]:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code") or "").strip() or "issue"
        msg = str(it.get("message") or "").strip()
        ln = it.get("line")
        loc = f" (line {ln})" if isinstance(ln, int) else ""
        lines.append(f"- {code}{loc}: {msg}")
    issues_txt = "\n".join(lines) if lines else "- (no details)"
    has_quotes_issue = any(isinstance(it, dict) and str(it.get("code") or "") == "too_many_quotes" for it in (issues or []))
    has_paren_issue = any(
        isinstance(it, dict) and str(it.get("code") or "") == "too_many_parentheses" for it in (issues or [])
    )
    has_too_long = any(isinstance(it, dict) and str(it.get("code") or "") == "length_too_long" for it in (issues or []))
    has_too_short = any(isinstance(it, dict) and str(it.get("code") or "") == "length_too_short" for it in (issues or []))
    extra_fix_rules: list[str] = []
    if has_quotes_issue:
        extra_fix_rules.append("- `「」`/`『』` を0個にする（強調は地の文へ）")
    if has_paren_issue:
        extra_fix_rules.append("- `（）`/`()` を0個にする（補足は地の文へ）")
    if has_too_long and target_max is not None:
        extra_fix_rules.append(f"- 文字数を {int(target_max)} 以下に収める（冗長な言い換え/導入を削る）")
    if has_too_short and target_min is not None:
        extra_fix_rules.append(f"- 文字数を {int(target_min)} 以上に増やす（具体を追加し、水増しはしない）")
    extra_rules = "\n".join(extra_fix_rules) if extra_fix_rules else "- (なし)"
    target_line = ""
    if target_min is not None or target_max is not None:
        target_line = f"{int(target_min or 0)}〜{int(target_max or 0)}字"
    return (
        "あなたは日本語のYouTubeナレーション台本（Aテキスト）作家です。\n"
        f"これはリトライ {retry_round}/{retry_total} です。\n"
        "先ほどの章草稿に機械的な禁則違反があるため、同じ章を本文だけで書き直してください。\n"
        "説明は禁止。本文のみ。\n\n"
        f"【企画タイトル】\n{title}\n\n"
        + (f"【目標文字数】{target_line}\n\n" if target_line else "")
        + "【検出された不備（修正必須）】\n"
        + issues_txt
        + "\n\n"
        "【追加の修正ルール（今回だけ強制）】\n"
        + extra_rules
        + "\n\n"
        "【前回の草稿（参考。コピー禁止。表現の焼き直し禁止）】\n"
        + _sanitize_context(previous_draft, max_chars=2600)
        + "\n\n"
        "【元の執筆指示（再掲）】\n"
        + base_prompt
        + "\n"
    )


def _assemble_with_block_pauses(chapters: list[tuple[PlanChapter, str]], *, pause_between_blocks: bool) -> str:
    out: list[str] = []
    prev_block: int | None = None
    for ch, txt in chapters:
        if prev_block is not None and pause_between_blocks and ch.block != prev_block:
            out.extend(["", "---", ""])
        if out and out[-1] != "":
            out.append("")
        out.append(txt.strip())
        out.append("")
        prev_block = ch.block
    return "\n".join(out).strip() + "\n"


def _chapter_shrink_prompt(
    *,
    title: str,
    chapter: PlanChapter,
    chapter_count: int,
    persona: str,
    channel_prompt: str,
    core_message: str,
    previous_tail: str,
    target_min: int,
    target_max: int,
    draft_text: str,
) -> str:
    return (
        "あなたは日本語のYouTubeナレーション台本（Aテキスト）作家です。\n"
        "章本文を「内容を変えずに短く」整形する役です。\n\n"
        f"【企画タイトル】\n{title}\n\n"
        f"【章】{chapter.chapter}/{chapter_count}（Block {chapter.block}: {chapter.block_title}）\n"
        f"【この章のゴール】\n{chapter.goal}\n\n"
        f"【目標文字数（改行/空白除外）】{target_min}〜{target_max}字\n\n"
        "【コアメッセージ（全章でブレない）】\n"
        + (core_message.strip() or "(空)")
        + "\n\n"
        "【直前章の末尾（文脈。コピー禁止）】\n"
        + (_sanitize_context(previous_tail, max_chars=320) if previous_tail else "(なし)")
        + "\n\n"
        "【短縮ルール（厳守）】\n"
        "- 新しい情報を増やさない（言い換え/重複/余談を削る）。\n"
        "- 意味は変えずに、文を短くし、同じ趣旨の繰り返しを消す。\n"
        "- `「」`/`『』` と `（）`/`()` は 0 個。\n"
        "- 見出し/箇条書き/番号リスト/URL/脚注/区切り `---` を入れない。\n"
        "- 途中章の締め言葉（まとめ/結論/最後に/挨拶）は入れない。\n"
        "\n"
        "【元の章本文】\n"
        + _sanitize_context(draft_text, max_chars=5200)
        + "\n\n"
        "短縮後の本文のみを出力してください。\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--title", default="", help="Override title (optional)")
    ap.add_argument("--duration-minutes", type=int, default=0, help="If set, derive target chars from duration")
    ap.add_argument("--chars-per-minute", type=int, default=320)
    ap.add_argument("--target-chars-min", type=int, default=0)
    ap.add_argument("--target-chars-max", type=int, default=0)
    ap.add_argument("--chapter-count", type=int, default=0)
    ap.add_argument("--block-count", type=int, default=8)
    ap.add_argument("--block-template", default="", help="Longform block template key (optional)")
    ap.add_argument("--per-chapter-aim", type=int, default=1200)
    ap.add_argument("--apply", action="store_true", help="Write canonical chapters + assembled.md")
    ap.add_argument("--plan-only", action="store_true", help="Only create plan.json (no drafting)")
    ap.add_argument("--force-plan", action="store_true", help="Re-generate plan even if plan.json already exists")
    memory_group = ap.add_mutually_exclusive_group()
    memory_group.add_argument(
        "--use-memory",
        dest="use_memory",
        action="store_true",
        default=True,
        help="Include a compact memory snapshot in chapter prompts (default)",
    )
    memory_group.add_argument(
        "--no-memory",
        dest="use_memory",
        action="store_false",
        help="Disable memory snapshot in chapter prompts (debug)",
    )
    ap.add_argument("--memory-max-keywords", type=int, default=24, help="Max keywords in memory snapshot (default: 24)")
    ap.add_argument("--memory-max-must", type=int, default=12, help="Max must_include items in memory snapshot (default: 12)")
    ap.add_argument("--memory-max-chars", type=int, default=900, help="Max chars for memory snapshot prompt section (default: 900)")
    ap.add_argument("--chapter-max-tries", type=int, default=2)
    ap.add_argument("--chapter-min-ratio", type=float, default=0.7)
    ap.add_argument("--chapter-max-ratio", type=float, default=1.6)
    ap.add_argument("--chapter-quote-max", type=int, default=0, help="Per-chapter max for 「」/『』 (default: 0)")
    ap.add_argument("--chapter-paren-max", type=int, default=0, help="Per-chapter max for （）/() (default: 0)")
    ap.add_argument("--plan-max-tries", type=int, default=2, help="Max attempts for plan JSON schema validation")
    quality_group = ap.add_mutually_exclusive_group()
    quality_group.add_argument(
        "--quality-gate",
        dest="quality_gate",
        action="store_true",
        default=True,
        help="Run block-level LLM quality gate (judge + chapter-only rewrite) after drafting (default)",
    )
    quality_group.add_argument(
        "--no-quality-gate",
        dest="quality_gate",
        action="store_false",
        help="Disable block-level LLM quality gate (debug)",
    )
    ap.add_argument("--quality-max-rounds", type=int, default=2, help="Max judge→rewrite rounds (default: 2)")
    ap.add_argument(
        "--quality-max-fix-per-block",
        type=int,
        default=2,
        help="Max chapters to rewrite per block based on judge output (default: 2)",
    )
    ap.add_argument(
        "--quality-max-fix-chapters-per-round",
        type=int,
        default=4,
        help="Max chapters to rewrite per round across all blocks (default: 4)",
    )
    ap.add_argument(
        "--quality-judge-max-tokens",
        type=int,
        default=1600,
        help="Max tokens for the block judge response (default: 1600)",
    )
    balance_group = ap.add_mutually_exclusive_group()
    balance_group.add_argument(
        "--balance-length",
        dest="balance_length",
        action="store_true",
        default=True,
        help="If assembled length is out of range, auto-shrink a few chapters (default)",
    )
    balance_group.add_argument(
        "--no-balance-length",
        dest="balance_length",
        action="store_false",
        help="Disable auto length balancing",
    )
    ap.add_argument("--balance-max-rounds", type=int, default=1)
    ap.add_argument("--balance-max-chapters", type=int, default=2)
    ap.add_argument("--balance-chapter-max-tries", type=int, default=2)
    ap.add_argument("--max-tokens", type=int, default=4096)
    ap.add_argument("--temperature", type=float, default=0.25)
    pause_group = ap.add_mutually_exclusive_group()
    pause_group.add_argument(
        "--pause-between-blocks",
        dest="pause_between_blocks",
        action="store_true",
        default=True,
        help="Insert `---` only between blocks (default)",
    )
    pause_group.add_argument(
        "--no-pause-between-blocks",
        dest="pause_between_blocks",
        action="store_false",
        help="Do not insert `---` between blocks",
    )
    args = ap.parse_args()

    ch = _normalize_channel(args.channel)
    no = _normalize_video(args.video)

    st = ensure_status(ch, no, args.title.strip() or None)
    base = script_data_root() / ch / no
    content_dir = base / "content"
    analysis_dir = content_dir / "analysis" / "longform"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    chapters_dir = analysis_dir / "chapters"
    chapters_dir.mkdir(parents=True, exist_ok=True)

    title = str(args.title or "").strip() or str(st.metadata.get("sheet_title") or st.metadata.get("title") or "").strip()
    if not title:
        title = f"{ch}-{no}"

    # If user explicitly overrides title and wants canonical apply, also persist it into status metadata.
    # (This avoids title/CSV mismatch from confusing downstream alignment checks.)
    if args.apply and str(args.title or "").strip():
        st.metadata["sheet_title"] = title
        st.metadata["expected_title"] = title
        st.metadata["title"] = title

    persona = str(st.metadata.get("persona") or "")
    channel_prompt = str(st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or "")

    # Targets
    tmin = int(args.target_chars_min or 0)
    tmax = int(args.target_chars_max or 0)
    if int(args.duration_minutes or 0) > 0:
        dmin, dmax = _derive_targets_from_duration(int(args.duration_minutes), int(args.chars_per_minute))
        tmin = tmin or dmin
        tmax = tmax or dmax
    if tmin <= 0 or tmax <= 0 or tmax < tmin:
        raise SystemExit("target chars must be set (use --duration-minutes or --target-chars-min/max)")

    chapter_count = int(args.chapter_count or 0)
    if chapter_count <= 0:
        aim = int(round((tmin + tmax) / 2))
        chapter_count = _derive_chapter_count(aim_chars=aim, per_chapter_aim=int(args.per_chapter_aim))

    block_count = max(2, int(args.block_count))
    blocks = _select_blocks(channel=ch, block_count=block_count, template_key=str(args.block_template or ""))

    # Use existing pattern core_message when possible (metadata may already carry it).
    core_message_hint = str(st.metadata.get("concept_intent") or st.metadata.get("key_concept") or "").strip()

    plan_path = analysis_dir / "plan.json"
    plan_latest = analysis_dir / "plan__latest.json"
    plan_meta_path = analysis_dir / "plan__llm_meta.json"

    # 1) Plan (LLM JSON) — prerequisite for stable chapter goals.
    reused_existing_plan = False
    existing_drafts = list(chapters_dir.glob("chapter_[0-9][0-9][0-9].md"))
    if plan_path.exists() and existing_drafts and (not args.plan_only) and (not args.force_plan):
        try:
            existing_obj = json.loads(plan_path.read_text(encoding="utf-8"))
            if not isinstance(existing_obj, dict):
                raise ValueError("plan.json is not an object")
            existing_blocks = existing_obj.get("blocks")
            if isinstance(existing_blocks, list) and existing_blocks:
                blocks = existing_blocks  # preserve previous block layout for resume
            existing_chapter_count = int(existing_obj.get("chapter_count") or 0)
            if existing_chapter_count != int(chapter_count):
                raise ValueError(f"plan chapter_count mismatch: {existing_chapter_count} != {chapter_count}")
            existing_tmin = int(existing_obj.get("target_chars_min") or 0)
            existing_tmax = int(existing_obj.get("target_chars_max") or 0)
            if existing_tmin and existing_tmax and (existing_tmin != tmin or existing_tmax != tmax):
                raise ValueError(f"plan target mismatch: {existing_tmin}-{existing_tmax} != {tmin}-{tmax}")
            core_message, plan_chapters_raw = _parse_plan_json(existing_obj, blocks=blocks, chapter_count=chapter_count)
            plan_chapters = _assign_budgets(plan_chapters_raw, target_min=tmin, target_max=tmax)
            plan = Plan(
                schema=str(existing_obj.get("schema") or "ytm.longform_plan.v1"),
                generated_at=str(existing_obj.get("generated_at") or datetime.now(timezone.utc).isoformat()),
                title=str(existing_obj.get("title") or title),
                channel=ch,
                video=no,
                target_chars_min=tmin,
                target_chars_max=tmax,
                chapter_count=chapter_count,
                blocks=blocks,
                chapters=plan_chapters,
                core_message=core_message or str(existing_obj.get("core_message") or core_message_hint),
            )
            reused_existing_plan = True
            print(f"[MARATHON] reusing existing plan: {plan_path}")
        except Exception as exc:
            print(f"[MARATHON] existing plan ignored (will re-generate): {exc}")

    if not reused_existing_plan:
        plan_messages = _build_plan_prompt(
            title=title,
            target_min=tmin,
            target_max=tmax,
            chapter_count=chapter_count,
            blocks=blocks,
            persona=persona,
            channel_prompt=channel_prompt,
            core_message_hint=core_message_hint,
        )
        plan_llm_meta: dict[str, Any] = {}
        plan_obj: dict[str, Any] | None = None
        core_message = ""
        plan_chapters_raw: list[PlanChapter] | None = None
        plan_max_tries = max(1, int(args.plan_max_tries))
        plan_tokens = max(2200, min(5200, 800 + int(chapter_count) * 60))

        for attempt in range(1, plan_max_tries + 1):
            plan_text, plan_llm_meta = _call_llm_text(
                task="script_a_text_rebuild_plan",
                messages=plan_messages,
                max_tokens=int(plan_tokens),
                temperature=0.2,
                response_format="json_object",
            )

            if plan_text is None:
                # Agent/think mode: pending plan task created.
                plan_meta_path.write_text(json.dumps(plan_llm_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                print("[MARATHON] plan is pending (agent/think mode).")
                print(f"- pending: {plan_llm_meta.get('pending_path')}")
                print("- next: rerun this command after completing the task via scripts/agent_runner.py")
                return 2

            raw_path = analysis_dir / f"plan_raw_attempt_{attempt:02d}.json"
            raw_path.write_text(plan_text.strip() + "\n", encoding="utf-8")

            try:
                obj = json.loads(plan_text)
                if not isinstance(obj, dict):
                    raise ValueError("plan JSON is not an object")
                cm, chapters = _parse_plan_json(obj, blocks=blocks, chapter_count=chapter_count)
                plan_obj = obj
                core_message = cm
                plan_chapters_raw = chapters
                break
            except Exception as exc:
                if attempt >= plan_max_tries:
                    raise SystemExit(f"Invalid plan schema: {exc}")
                # Strengthen with a short corrective message; avoid retry spam.
                plan_messages = plan_messages + [
                    {
                        "role": "user",
                        "content": (
                            "前回の出力が要件違反でした。次は必ず条件を満たす JSON オブジェクトのみを返してください。\n"
                            f"- chapters: {chapter_count} 件ちょうど\n"
                            f"- chapter: 1..{chapter_count} の連番（欠番/重複/余剰なし）\n"
                            "- 各章に block, block_title, goal, must_include(list), avoid(list) を必ず含める\n"
                            f"(違反理由: {exc})\n"
                        ),
                    }
                ]

        if plan_obj is None or plan_chapters_raw is None:
            raise SystemExit("Failed to obtain a valid plan")

        plan_chapters = _assign_budgets(plan_chapters_raw, target_min=tmin, target_max=tmax)
        plan = Plan(
            schema="ytm.longform_plan.v1",
            generated_at=datetime.now(timezone.utc).isoformat(),
            title=title,
            channel=ch,
            video=no,
            target_chars_min=tmin,
            target_chars_max=tmax,
            chapter_count=chapter_count,
            blocks=blocks,
            chapters=plan_chapters,
            core_message=core_message or core_message_hint,
        )

        plan_path.write_text(json.dumps(plan.as_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        plan_latest.write_text(json.dumps(plan.as_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        plan_meta_path.write_text(json.dumps(plan_llm_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.plan_only:
        print(f"[MARATHON] wrote plan: {plan_path}")
        return 0

    # 2) Draft chapters sequentially (uses previous tail for coherence).
    quote_total_max = int(st.metadata.get("a_text_quote_marks_max") or 20)
    paren_total_max = int(st.metadata.get("a_text_paren_marks_max") or 10)

    base_meta = dict(st.metadata or {})
    base_meta["a_text_quote_marks_max"] = quote_total_max
    base_meta["a_text_paren_marks_max"] = paren_total_max

    drafted: list[tuple[PlanChapter, str]] = []
    previous_tail = ""
    quotes_remaining = int(quote_total_max)
    paren_remaining = int(paren_total_max)

    forced_quote_max = max(0, int(args.chapter_quote_max))
    forced_paren_max = max(0, int(args.chapter_paren_max))

    for idx, chapter in enumerate(plan.chapters, start=1):
        remaining_chapters = max(1, int(plan.chapter_count) - idx + 1)
        chapter_quote_budget = max(0, int(quotes_remaining) // remaining_chapters)
        chapter_paren_budget = max(0, int(paren_remaining) // remaining_chapters)
        if forced_quote_max >= 0:
            chapter_quote_budget = min(int(chapter_quote_budget), int(forced_quote_max))
        if forced_paren_max >= 0:
            chapter_paren_budget = min(int(chapter_paren_budget), int(forced_paren_max))

        chapter_meta = dict(base_meta)
        chapter_meta["a_text_quote_marks_max"] = int(chapter_quote_budget)
        chapter_meta["a_text_paren_marks_max"] = int(chapter_paren_budget)

        out_path = chapters_dir / f"chapter_{chapter.chapter:03d}.md"
        if out_path.exists() and out_path.stat().st_size > 0:
            text = out_path.read_text(encoding="utf-8").strip()
            if not chapter.closing_allowed:
                softened = _soften_premature_closing_phrases(text).strip()
                if softened and softened != text:
                    out_path.write_text(softened + "\n", encoding="utf-8")
                    text = softened
            issues, stats = _validate_chapter_text(
                text,
                meta=chapter_meta,
                char_budget=chapter.char_budget,
                min_ratio=float(args.chapter_min_ratio),
                max_ratio=float(args.chapter_max_ratio),
                closing_allowed=chapter.closing_allowed,
            )
            hard = [it for it in issues if str(it.get("severity") or "error").lower() != "warning"]
            if not hard:
                drafted.append((chapter, text))
                previous_tail = text[-320:] if text else previous_tail
                quotes_remaining = max(0, int(quotes_remaining) - int(stats.get("quote_marks") or 0))
                paren_remaining = max(0, int(paren_remaining) - int(stats.get("paren_marks") or 0))
                continue

            # Preserve the previous draft for diff/audit, then re-generate this chapter.
            replaced_suffix = _utc_now_compact()
            replaced_path = chapters_dir / f"chapter_{chapter.chapter:03d}__replaced_{replaced_suffix}.md"
            out_path.rename(replaced_path)
            (chapters_dir / f"chapter_{chapter.chapter:03d}__replaced_{replaced_suffix}__validation.json").write_text(
                json.dumps(
                    {
                        "schema": "ytm.longform_chapter_validation.v1",
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "channel": ch,
                        "video": no,
                        "chapter": chapter.chapter,
                        "attempt": 0,
                        "ok": False,
                        "stats": stats,
                        "issues": issues,
                        "note": "existing draft did not meet current constraints; replaced for regeneration",
                        "replaced_path": str(replaced_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

        memory_note = ""
        if bool(args.use_memory) and drafted:
            snapshot = _build_memory_snapshot(
                plan,
                drafted,
                max_keywords=int(args.memory_max_keywords),
                max_must=int(args.memory_max_must),
            )
            memory_note = _format_memory_for_prompt(snapshot, max_chars=int(args.memory_max_chars))

        prompt_base = _chapter_draft_prompt(
            title=title,
            chapter=chapter,
            chapter_count=plan.chapter_count,
            persona=persona,
            channel_prompt=channel_prompt,
            core_message=plan.core_message,
            previous_tail=previous_tail,
            memory_note=memory_note,
            quote_max=chapter_quote_budget,
            paren_max=chapter_paren_budget,
            quote_total_max=quote_total_max,
            paren_total_max=paren_total_max,
        )
        messages = [{"role": "user", "content": prompt_base}]

        text: str | None = None
        llm_meta: dict[str, Any] | None = None
        accepted_stats: dict[str, Any] | None = None
        for attempt in range(1, max(1, int(args.chapter_max_tries)) + 1):
            chapter_text, meta = _call_llm_text(
                task="script_chapter_draft",
                messages=messages,
                max_tokens=int(args.max_tokens),
                temperature=float(args.temperature),
            )
            llm_meta = meta
            if chapter_text is None:
                # Agent/think mode: pending task created. Stop here (sequential dependency).
                try:
                    _write_longform_sidecars(
                        analysis_dir=analysis_dir,
                        plan=plan,
                        drafted=drafted,
                        max_keywords=int(args.memory_max_keywords),
                        max_must=int(args.memory_max_must),
                    )
                except Exception:
                    pass
                (analysis_dir / "pending.json").write_text(
                    json.dumps(
                        {
                            "schema": "ytm.longform_pending.v1",
                            "generated_at": datetime.now(timezone.utc).isoformat(),
                            "pending_reason": "chapter_draft_pending",
                            "chapter": chapter.chapter,
                            "pending_task": meta,
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                print("[MARATHON] chapter draft pending (agent/think mode).")
                print(f"- chapter: {chapter.chapter:03d}")
                print(f"- pending: {meta.get('pending_path')}")
                print("- next: complete it via scripts/agent_runner.py, then rerun this command")
                return 2

            if not chapter.closing_allowed:
                chapter_text = _soften_premature_closing_phrases(chapter_text)

            issues, stats = _validate_chapter_text(
                chapter_text,
                meta=chapter_meta,
                char_budget=chapter.char_budget,
                min_ratio=float(args.chapter_min_ratio),
                max_ratio=float(args.chapter_max_ratio),
                closing_allowed=chapter.closing_allowed,
            )
            hard = [it for it in issues if str(it.get("severity") or "error").lower() != "warning"]
            if not hard:
                text = chapter_text.strip()
                accepted_stats = stats
                break

            # Record invalid attempt for debugging (analysis-only; does not touch canonical SoT).
            invalid_text_path = chapters_dir / f"chapter_{chapter.chapter:03d}__attempt_{attempt:02d}__invalid.md"
            invalid_report_path = chapters_dir / f"chapter_{chapter.chapter:03d}__attempt_{attempt:02d}__validation.json"
            invalid_text_path.write_text(chapter_text.strip() + "\n", encoding="utf-8")
            invalid_report_path.write_text(
                json.dumps(
                    {
                        "schema": "ytm.longform_chapter_validation.v1",
                        "generated_at": datetime.now(timezone.utc).isoformat(),
                        "channel": ch,
                        "video": no,
                        "chapter": chapter.chapter,
                        "attempt": attempt,
                        "ok": False,
                        "stats": stats,
                        "issues": issues,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            # Rewrite with explicit issues (same task, new prompt).
            retry_round = min(attempt + 1, max(1, int(args.chapter_max_tries)))
            messages = [
                {
                    "role": "user",
                    "content": _chapter_rewrite_prompt(
                        title=title,
                        base_prompt=prompt_base,
                        issues=hard,
                        previous_draft=chapter_text,
                        target_min=stats.get("target_chars_min"),
                        target_max=stats.get("target_chars_max"),
                        retry_round=retry_round,
                        retry_total=max(1, int(args.chapter_max_tries)),
                    ),
                }
            ]

        if not text:
            raise SystemExit(
                f"Failed to draft chapter {chapter.chapter:03d} within max tries. "
                f"See invalid attempts under: {chapters_dir}"
            )

        out_path.write_text(text.strip() + "\n", encoding="utf-8")
        if llm_meta:
            (chapters_dir / f"chapter_{chapter.chapter:03d}__llm_meta.json").write_text(
                json.dumps(llm_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

        drafted.append((chapter, text.strip()))
        previous_tail = text.strip()[-320:]
        if accepted_stats is None:
            # Fallback (should not happen): keep budgets conservative.
            used_quotes = sum(text.count(ch) for ch in ("「", "」", "『", "』"))
            used_paren = sum(text.count(ch) for ch in ("（", "）"))
        else:
            used_quotes = int(accepted_stats.get("quote_marks") or 0)
            used_paren = int(accepted_stats.get("paren_marks") or 0)
        quotes_remaining = max(0, int(quotes_remaining) - used_quotes)
        paren_remaining = max(0, int(paren_remaining) - used_paren)

    # 2.5) Optional: block-level quality gate (LLM judge + chapter-only rewrite).
    if bool(args.quality_gate) and drafted:
        judge_task = os.getenv("MARATHON_QUALITY_JUDGE_TASK", "script_a_text_quality_judge").strip()
        if not judge_task:
            judge_task = "script_a_text_quality_judge"
        rewrite_task = os.getenv("MARATHON_QUALITY_REWRITE_TASK", "script_a_text_quality_fix").strip()
        if not rewrite_task:
            rewrite_task = "script_a_text_quality_fix"

        max_rounds = max(0, int(args.quality_max_rounds))
        max_fix_per_block = max(0, int(args.quality_max_fix_per_block))
        max_fix_per_round = max(0, int(args.quality_max_fix_chapters_per_round))
        judge_max_tokens = max(600, int(args.quality_judge_max_tokens))

        quality_dir = analysis_dir / "quality_gate"
        quality_dir.mkdir(parents=True, exist_ok=True)

        fix_counts: dict[int, int] = {}
        for round_idx in range(1, max_rounds + 1):
            round_dir = quality_dir / f"round_{round_idx:02d}"
            round_dir.mkdir(parents=True, exist_ok=True)

            fix_requests: list[dict[str, Any]] = []
            for block in plan.blocks:
                if not isinstance(block, dict):
                    continue
                block_id = int(block.get("block") or 0)
                if block_id <= 0:
                    continue
                block_chapters = [(c, t) for (c, t) in drafted if int(c.block) == int(block_id)]
                if not block_chapters:
                    continue

                # Memory note: only content before this block (prevents repetition across blocks).
                first_chapter_num = min(int(c.chapter) for c, _ in block_chapters)
                prev_drafted = [(c, t) for (c, t) in drafted if int(c.chapter) < int(first_chapter_num)]
                memory_note = ""
                if bool(args.use_memory) and prev_drafted:
                    snapshot = _build_memory_snapshot(
                        plan,
                        prev_drafted,
                        max_keywords=int(args.memory_max_keywords),
                        max_must=int(args.memory_max_must),
                    )
                    memory_note = _format_memory_for_prompt(snapshot, max_chars=int(args.memory_max_chars))

                prompt = _longform_block_judge_prompt(
                    title=title,
                    core_message=plan.core_message,
                    block=block,
                    chapters=block_chapters,
                    memory_note=memory_note,
                    max_fix_per_block=max_fix_per_block,
                )
                judge_text, judge_meta = _call_llm_text(
                    task=judge_task,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=int(judge_max_tokens),
                    temperature=0.0,
                    response_format="json_object",
                )

                if judge_text is None:
                    try:
                        _write_longform_sidecars(
                            analysis_dir=analysis_dir,
                            plan=plan,
                            drafted=drafted,
                            max_keywords=int(args.memory_max_keywords),
                            max_must=int(args.memory_max_must),
                        )
                    except Exception:
                        pass
                    (analysis_dir / "pending.json").write_text(
                        json.dumps(
                            {
                                "schema": "ytm.longform_pending.v1",
                                "generated_at": datetime.now(timezone.utc).isoformat(),
                                "pending_reason": "quality_gate_block_judge_pending",
                                "block": block_id,
                                "pending_task": judge_meta,
                            },
                            ensure_ascii=False,
                            indent=2,
                        )
                        + "\n",
                        encoding="utf-8",
                    )
                    print("[MARATHON] quality gate judge pending (agent/think mode).")
                    print(f"- block: {block_id:02d}")
                    print(f"- pending: {judge_meta.get('pending_path')}")
                    print("- next: complete it via scripts/agent_runner.py, then rerun this command")
                    return 2

                raw_path = round_dir / f"block_{block_id:02d}__judge_raw.json"
                raw_path.write_text(judge_text.strip() + "\n", encoding="utf-8")
                (round_dir / f"block_{block_id:02d}__judge__llm_meta.json").write_text(
                    json.dumps(judge_meta, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                judge_obj = _parse_json_object(judge_text)
                (round_dir / f"block_{block_id:02d}__judge.json").write_text(
                    json.dumps(judge_obj, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

                verdict = str(judge_obj.get("verdict") or "").strip().lower()
                if verdict not in {"pass", "fail"}:
                    verdict = "fail" if (judge_obj.get("must_fix_chapters") or judge_obj.get("chapter_fixes")) else "pass"

                if verdict != "fail" or max_fix_per_block <= 0:
                    continue

                allowed_chapters = {int(c.chapter) for c, _ in block_chapters}
                must_fix = [c for c in _coerce_int_list(judge_obj.get("must_fix_chapters")) if int(c) in allowed_chapters]

                fix_map: dict[int, dict[str, Any]] = {}
                raw_fixes = judge_obj.get("chapter_fixes")
                if isinstance(raw_fixes, list):
                    for item in raw_fixes:
                        if not isinstance(item, dict):
                            continue
                        try:
                            chap_num = int(item.get("chapter") or 0)
                        except Exception:
                            chap_num = 0
                        if chap_num in allowed_chapters:
                            fix_map[chap_num] = item

                candidates: list[int] = []
                for chap_num in must_fix:
                    if chap_num not in candidates:
                        candidates.append(chap_num)
                for chap_num in sorted(fix_map.keys()):
                    if chap_num not in candidates:
                        candidates.append(chap_num)

                for chap_num in candidates[: max_fix_per_block]:
                    fix = fix_map.get(int(chap_num), {}) if isinstance(fix_map.get(int(chap_num)), dict) else {}
                    fix_requests.append(
                        {
                            "block": int(block_id),
                            "chapter": int(chap_num),
                            "judge_problem": str(fix.get("problem") or "").strip(),
                            "rewrite_focus": [str(x).strip() for x in (fix.get("rewrite_focus") or []) if str(x).strip()],
                            "must_keep": [str(x).strip() for x in (fix.get("must_keep") or []) if str(x).strip()],
                            "avoid_more": [str(x).strip() for x in (fix.get("avoid_more") or []) if str(x).strip()],
                        }
                    )

            # Dedupe and cap per round.
            selected: list[dict[str, Any]] = []
            seen_chapters: set[int] = set()
            for fr in sorted(fix_requests, key=lambda x: (int(x.get("block") or 0), int(x.get("chapter") or 0))):
                chap_num = int(fr.get("chapter") or 0)
                if chap_num <= 0 or chap_num in seen_chapters:
                    continue
                if int(fix_counts.get(chap_num) or 0) >= int(max_rounds):
                    continue
                seen_chapters.add(chap_num)
                selected.append(fr)
                if max_fix_per_round > 0 and len(selected) >= int(max_fix_per_round):
                    break

            if not selected:
                break

            for fr in sorted(selected, key=lambda x: int(x.get("chapter") or 0)):
                chap_num = int(fr.get("chapter") or 0)
                idx = next((i for i, (c, _) in enumerate(drafted) if int(c.chapter) == chap_num), None)
                if idx is None:
                    continue

                chapter_obj, prev_text = drafted[int(idx)]
                prev_text = (prev_text or "").strip()

                # Recompute per-chapter symbol budgets based on current drafts (keeps global budgets stable).
                used_quotes_before = sum(
                    sum(drafted[j][1].count(mark) for mark in ("「", "」", "『", "』")) for j in range(0, int(idx))
                )
                used_paren_before = sum(
                    sum(drafted[j][1].count(mark) for mark in ("（", "）", "(", ")")) for j in range(0, int(idx))
                )
                q_remaining = max(0, int(quote_total_max) - int(used_quotes_before))
                p_remaining = max(0, int(paren_total_max) - int(used_paren_before))
                remaining_chapters = max(1, int(plan.chapter_count) - int(idx))
                chapter_quote_budget = max(0, int(q_remaining) // remaining_chapters)
                chapter_paren_budget = max(0, int(p_remaining) // remaining_chapters)
                if forced_quote_max >= 0:
                    chapter_quote_budget = min(int(chapter_quote_budget), int(forced_quote_max))
                if forced_paren_max >= 0:
                    chapter_paren_budget = min(int(chapter_paren_budget), int(forced_paren_max))

                chapter_meta = dict(base_meta)
                chapter_meta["a_text_quote_marks_max"] = int(chapter_quote_budget)
                chapter_meta["a_text_paren_marks_max"] = int(chapter_paren_budget)

                _, stats = _validate_chapter_text(
                    prev_text,
                    meta=chapter_meta,
                    char_budget=chapter_obj.char_budget,
                    min_ratio=float(args.chapter_min_ratio),
                    max_ratio=float(args.chapter_max_ratio),
                    closing_allowed=chapter_obj.closing_allowed,
                )
                target_min = int(stats.get("target_chars_min") or 0) or 600
                target_max = int(stats.get("target_chars_max") or 0) or max(target_min + 200, 900)

                prev_tail = drafted[int(idx) - 1][1].strip()[-320:] if int(idx) > 0 else ""
                next_head = ""
                if int(idx) + 1 < len(drafted):
                    next_head, _ = _excerpt_head_tail(drafted[int(idx) + 1][1], head_chars=240, tail_chars=0)

                memory_note = ""
                if bool(args.use_memory) and int(idx) > 0:
                    snapshot = _build_memory_snapshot(
                        plan,
                        drafted[: int(idx)],
                        max_keywords=int(args.memory_max_keywords),
                        max_must=int(args.memory_max_must),
                    )
                    memory_note = _format_memory_for_prompt(snapshot, max_chars=int(args.memory_max_chars))

                prompt = _chapter_quality_rewrite_prompt(
                    title=title,
                    chapter=chapter_obj,
                    chapter_count=plan.chapter_count,
                    persona=persona,
                    channel_prompt=channel_prompt,
                    core_message=plan.core_message,
                    previous_tail=prev_tail,
                    next_head=next_head,
                    memory_note=memory_note,
                    quote_max=int(chapter_quote_budget),
                    paren_max=int(chapter_paren_budget),
                    quote_total_max=int(quote_total_max),
                    paren_total_max=int(paren_total_max),
                    target_min=int(target_min),
                    target_max=int(target_max),
                    previous_draft=prev_text,
                    judge_problem=str(fr.get("judge_problem") or "").strip(),
                    rewrite_focus=list(fr.get("rewrite_focus") or []),
                    must_keep=list(fr.get("must_keep") or []),
                    avoid_more=list(fr.get("avoid_more") or []),
                )

                messages = [{"role": "user", "content": prompt}]
                updated_text: str | None = None
                llm_meta: dict[str, Any] | None = None
                for attempt in range(1, max(1, int(args.chapter_max_tries)) + 1):
                    rewritten, meta = _call_llm_text(
                        task=rewrite_task,
                        messages=messages,
                        max_tokens=int(args.max_tokens),
                        temperature=float(args.temperature),
                    )
                    llm_meta = meta
                    if rewritten is None:
                        try:
                            _write_longform_sidecars(
                                analysis_dir=analysis_dir,
                                plan=plan,
                                drafted=drafted,
                                max_keywords=int(args.memory_max_keywords),
                                max_must=int(args.memory_max_must),
                            )
                        except Exception:
                            pass
                        (analysis_dir / "pending.json").write_text(
                            json.dumps(
                                {
                                    "schema": "ytm.longform_pending.v1",
                                    "generated_at": datetime.now(timezone.utc).isoformat(),
                                    "pending_reason": "quality_gate_chapter_rewrite_pending",
                                    "block": int(fr.get("block") or 0),
                                    "chapter": chap_num,
                                    "pending_task": meta,
                                },
                                ensure_ascii=False,
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        print("[MARATHON] quality gate chapter rewrite pending (agent/think mode).")
                        print(f"- chapter: {chap_num:03d}")
                        print(f"- pending: {meta.get('pending_path')}")
                        print("- next: complete it via scripts/agent_runner.py, then rerun this command")
                        return 2

                    if not chapter_obj.closing_allowed:
                        rewritten = _soften_premature_closing_phrases(rewritten)

                    issues, rewrite_stats = _validate_chapter_text(
                        rewritten,
                        meta=chapter_meta,
                        char_budget=chapter_obj.char_budget,
                        min_ratio=float(args.chapter_min_ratio),
                        max_ratio=float(args.chapter_max_ratio),
                        closing_allowed=chapter_obj.closing_allowed,
                    )
                    hard = [it for it in issues if str(it.get("severity") or "error").lower() != "warning"]
                    if not hard:
                        updated_text = rewritten.strip()
                        break

                    retry_round = min(attempt + 1, max(1, int(args.chapter_max_tries)))
                    messages = [
                        {
                            "role": "user",
                            "content": _chapter_rewrite_prompt(
                                title=title,
                                base_prompt=prompt,
                                issues=hard,
                                previous_draft=rewritten,
                                target_min=rewrite_stats.get("target_chars_min"),
                                target_max=rewrite_stats.get("target_chars_max"),
                                retry_round=retry_round,
                                retry_total=max(1, int(args.chapter_max_tries)),
                            ),
                        }
                    ]

                if not updated_text:
                    print("[MARATHON] quality gate: failed to rewrite chapter within max tries.")
                    print(f"- chapter: {chap_num:03d}")
                    return 2

                replaced_suffix = _utc_now_compact()
                out_path = chapters_dir / f"chapter_{chap_num:03d}.md"
                replaced_path: Path | None = None
                if out_path.exists():
                    replaced_path = chapters_dir / f"chapter_{chap_num:03d}__replaced_quality_{replaced_suffix}.md"
                    out_path.rename(replaced_path)

                out_path.write_text(updated_text.strip() + "\n", encoding="utf-8")
                (chapters_dir / f"chapter_{chap_num:03d}__quality_fix_{replaced_suffix}__judge.json").write_text(
                    json.dumps(fr, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                if llm_meta:
                    (chapters_dir / f"chapter_{chap_num:03d}__quality_fix_{replaced_suffix}__llm_meta.json").write_text(
                        json.dumps(llm_meta, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                if replaced_path:
                    (chapters_dir / f"chapter_{chap_num:03d}__quality_fix_{replaced_suffix}__replaced_path.txt").write_text(
                        str(replaced_path) + "\n", encoding="utf-8"
                    )

                drafted[int(idx)] = (chapter_obj, updated_text.strip())
                fix_counts[chap_num] = int(fix_counts.get(chap_num) or 0) + 1

    # 3) Assemble and validate whole script (still in analysis dir).
    assembled = _assemble_with_block_pauses(drafted, pause_between_blocks=bool(args.pause_between_blocks))
    assembled_path = analysis_dir / "assembled_candidate.md"
    assembled_latest = analysis_dir / "assembled_candidate__latest.md"
    assembled_path.write_text(assembled, encoding="utf-8")
    assembled_latest.write_text(assembled, encoding="utf-8")

    full_meta = dict(base_meta)
    full_meta["target_chars_min"] = int(tmin)
    full_meta["target_chars_max"] = int(tmax)
    full_issues, full_stats = validate_a_text(assembled, full_meta)
    report = {
        "schema": "ytm.longform_validation.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": ch,
        "video": no,
        "ok": not any(str(i.get("severity") or "error").lower() != "warning" for i in full_issues),
        "stats": full_stats,
        "issues": full_issues,
    }
    (analysis_dir / "validation__latest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if not report["ok"] and bool(args.balance_length):
        hard_issues = [it for it in (full_issues or []) if str(it.get("severity") or "error").lower() != "warning"]
        if len(hard_issues) == 1 and str(hard_issues[0].get("code") or "") == "length_too_long":
            # Auto-balance by shrinking a few longest chapters (chapter-local; no full-text LLM).
            rounds = max(0, int(args.balance_max_rounds))
            for round_idx in range(1, rounds + 1):
                excess = int(full_stats.get("char_count") or 0) - int(tmax)
                if excess <= 0:
                    break
                candidates = [(idx, ch, txt, _chars_spoken(txt)) for idx, (ch, txt) in enumerate(drafted)]
                # Avoid touching the final chapter unless absolutely necessary.
                non_final = [c for c in candidates if c[1].chapter != int(plan.chapter_count)]
                pool = non_final if non_final else candidates
                pool.sort(key=lambda x: x[3], reverse=True)
                k = max(1, min(int(args.balance_max_chapters), len(pool)))
                per_reduce = int(math.ceil(excess / k))

                for idx, ch_obj, txt, cur_len in pool[:k]:
                    # Target: reduce this chapter by ~per_reduce chars, but keep it readable.
                    tgt_max = max(600, int(cur_len) - int(per_reduce))
                    tgt_min = max(450, int(round(tgt_max * 0.8)))
                    shrink_meta = dict(base_meta)
                    shrink_meta["a_text_quote_marks_max"] = int(forced_quote_max)
                    shrink_meta["a_text_paren_marks_max"] = int(forced_paren_max)
                    shrink_meta["target_chars_min"] = int(tgt_min)
                    shrink_meta["target_chars_max"] = int(tgt_max)

                    prev_tail = drafted[idx - 1][1].strip()[-320:] if idx > 0 else ""
                    prompt = _chapter_shrink_prompt(
                        title=title,
                        chapter=ch_obj,
                        chapter_count=plan.chapter_count,
                        persona=persona,
                        channel_prompt=channel_prompt,
                        core_message=plan.core_message,
                        previous_tail=prev_tail,
                        target_min=tgt_min,
                        target_max=tgt_max,
                        draft_text=txt,
                    )
                    messages = [{"role": "user", "content": prompt}]

                    updated_text: str | None = None
                    for attempt in range(1, max(1, int(args.balance_chapter_max_tries)) + 1):
                        shrink_text, meta = _call_llm_text(
                            task="script_chapter_draft",
                            messages=messages,
                            max_tokens=int(args.max_tokens),
                            temperature=float(args.temperature),
                        )
                        if shrink_text is None:
                            (analysis_dir / "pending.json").write_text(
                                json.dumps(
                                    {
                                        "schema": "ytm.longform_pending.v1",
                                        "generated_at": datetime.now(timezone.utc).isoformat(),
                                        "pending_reason": "balance_length_chapter_pending",
                                        "chapter": ch_obj.chapter,
                                        "pending_task": meta,
                                    },
                                    ensure_ascii=False,
                                    indent=2,
                                )
                                + "\n",
                                encoding="utf-8",
                            )
                            print("[MARATHON] chapter shrink pending (agent/think mode).")
                            print(f"- chapter: {ch_obj.chapter:03d}")
                            print(f"- pending: {meta.get('pending_path')}")
                            print("- next: complete it via scripts/agent_runner.py, then rerun this command")
                            return 2

                        if not ch_obj.closing_allowed:
                            shrink_text = _soften_premature_closing_phrases(shrink_text)

                        issues, stats = _validate_chapter_text(
                            shrink_text,
                            meta=shrink_meta,
                            char_budget=0,
                            min_ratio=float(args.chapter_min_ratio),
                            max_ratio=float(args.chapter_max_ratio),
                            closing_allowed=ch_obj.closing_allowed,
                        )
                        hard = [it for it in issues if str(it.get("severity") or "error").lower() != "warning"]
                        if not hard:
                            updated_text = shrink_text.strip()
                            break

                        retry_round = min(attempt + 1, max(1, int(args.balance_chapter_max_tries)))
                        messages = [
                            {
                                "role": "user",
                                "content": _chapter_rewrite_prompt(
                                    title=title,
                                    base_prompt=prompt,
                                    issues=hard,
                                    previous_draft=shrink_text,
                                    target_min=shrink_meta.get("target_chars_min"),
                                    target_max=shrink_meta.get("target_chars_max"),
                                    retry_round=retry_round,
                                    retry_total=max(1, int(args.balance_chapter_max_tries)),
                                ),
                            }
                        ]

                    if not updated_text:
                        print("[MARATHON] balance_length: failed to shrink chapter within max tries.")
                        print(f"- chapter: {ch_obj.chapter:03d}")
                        print(f"- report: {analysis_dir / 'validation__latest.json'}")
                        return 2

                    # Persist new chapter draft (analysis-only) and update in-memory.
                    (chapters_dir / f"chapter_{ch_obj.chapter:03d}.md").write_text(updated_text + "\n", encoding="utf-8")
                    drafted[idx] = (ch_obj, updated_text)

                # Re-assemble and re-validate after this round.
                assembled = _assemble_with_block_pauses(drafted, pause_between_blocks=bool(args.pause_between_blocks))
                assembled_path.write_text(assembled, encoding="utf-8")
                assembled_latest.write_text(assembled, encoding="utf-8")
                full_issues, full_stats = validate_a_text(assembled, full_meta)
                report = {
                    "schema": "ytm.longform_validation.v1",
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "channel": ch,
                    "video": no,
                    "ok": not any(str(i.get("severity") or "error").lower() != "warning" for i in full_issues),
                    "stats": full_stats,
                    "issues": full_issues,
                }
                (analysis_dir / "validation__latest.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                if report["ok"]:
                    break

    try:
        _write_longform_sidecars(
            analysis_dir=analysis_dir,
            plan=plan,
            drafted=drafted,
            max_keywords=int(args.memory_max_keywords),
            max_must=int(args.memory_max_must),
        )
    except Exception:
        pass

    if not report["ok"]:
        print("[MARATHON] assembled_candidate has hard issues. Fix chapters and rerun.")
        print(f"- report: {analysis_dir / 'validation__latest.json'}")
        return 2

    if not args.apply:
        print("[MARATHON] dry-run complete.")
        print(f"- plan: {plan_path}")
        print(f"- chapters: {chapters_dir}")
        print(f"- assembled: {assembled_path}")
        return 0

    # 4) Apply: overwrite canonical chapters + assembled.md
    canonical_chapters = content_dir / "chapters"
    canonical_chapters.mkdir(parents=True, exist_ok=True)
    canonical_assembled = content_dir / "assembled.md"
    canonical_human = content_dir / "assembled_human.md"

    write_paths = [canonical_chapters, canonical_assembled, canonical_human, base / "status.json"]
    _assert_not_locked(write_paths)

    for chapter, txt in drafted:
        (canonical_chapters / f"chapter_{chapter.chapter}.md").write_text(txt.strip() + "\n", encoding="utf-8")

    canonical_assembled.write_text(assembled, encoding="utf-8")
    canonical_human.write_text(assembled, encoding="utf-8")

    # Update metadata so deterministic validators/ops know this is longform.
    st.metadata["chapter_count"] = int(plan.chapter_count)
    st.metadata["target_chars_min"] = int(tmin)
    st.metadata["target_chars_max"] = int(tmax)
    st.metadata["target_word_count"] = int(tmax)
    st.metadata["longform_mode"] = "marathon_v1"
    save_status(st)

    print("[MARATHON] applied to canonical script.")
    print(f"- assembled: {canonical_assembled}")
    print(f"- status: {base / 'status.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
