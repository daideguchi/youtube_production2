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
- ssot/OPS_LONGFORM_SCRIPT_SCALING.md
- ssot/OPS_A_TEXT_GLOBAL_RULES.md
"""

from __future__ import annotations

import argparse
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
_CTA_PHRASE_RE = re.compile(r"(?:ご視聴ありがとうございました|チャンネル登録|高評価|通知|コメント)")


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
        "  - must_include/avoid: 各最大2要素、各要素は最大20文字\n"
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
    base_meta: dict[str, Any],
    char_budget: int,
    min_ratio: float,
    max_ratio: float,
    closing_allowed: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = dict(base_meta or {})
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
    quote_max: int,
    paren_max: int,
) -> str:
    must = "\n".join([f"- {m}" for m in (chapter.must_include or [])]) if chapter.must_include else "- (なし)"
    avoid = "\n".join([f"- {a}" for a in (chapter.avoid or [])]) if chapter.avoid else "- (なし)"

    closing_rule = (
        "この章は最終章なので、短く締めてよい（締めの言葉は1回だけ）。"
        if chapter.closing_allowed
        else "この章は途中章なので、まとめ/結論/最後に/締めの挨拶は禁止（終わり感を出さない）。"
    )

    return (
        "あなたは日本語のYouTubeナレーション台本（Aテキスト）作家です。\n"
        "超長尺（2〜3時間級）を章分割で安定して書きます。\n"
        "\n"
        f"【企画タイトル】\n{title}\n\n"
        f"【章】{chapter.chapter}/{chapter_count}（Block {chapter.block}: {chapter.block_title}）\n"
        f"【この章のゴール】\n{chapter.goal}\n\n"
        f"【目標文字数（改行/空白除外の目安）】約{chapter.char_budget}字\n"
        f"【記号上限（ハード）】「」/『』 合計<= {quote_max} / （） 合計<= {paren_max}\n\n"
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
        "【執筆ルール（厳守）】\n"
        "- 出力は本文のみ（見出し/箇条書き/番号リスト/URL/脚注/参照番号/制作メタは禁止）。\n"
        "- 区切り記号は本文に入れない（`---` は後でブロック境界にだけ入れる）。\n"
        "- 同趣旨の言い換えで水増ししない。厚みは理解が増える具体で作る。\n"
        "- 現代の人物例（年齢/職業/台詞の作り込み）は入れない（全体事故の原因）。\n"
        "- 根拠不明の統計/研究/固有名詞/数字断定で説得力を作らない。\n"
        f"- {closing_rule}\n"
        "\n"
        "では、完成した本文のみを出力してください。\n"
    )


def _chapter_rewrite_prompt(*, title: str, base_prompt: str, issues: list[dict[str, Any]]) -> str:
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
    return (
        "あなたは日本語のYouTubeナレーション台本（Aテキスト）作家です。\n"
        "先ほどの章草稿に機械的な禁則違反があるため、同じ章を本文だけで書き直してください。\n"
        "説明は禁止。本文のみ。\n\n"
        f"【企画タイトル】\n{title}\n\n"
        "【検出された不備（修正必須）】\n"
        + issues_txt
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
    ap.add_argument("--per-chapter-aim", type=int, default=1200)
    ap.add_argument("--apply", action="store_true", help="Write canonical chapters + assembled.md")
    ap.add_argument("--plan-only", action="store_true", help="Only create plan.json (no drafting)")
    ap.add_argument("--chapter-max-tries", type=int, default=2)
    ap.add_argument("--chapter-min-ratio", type=float, default=0.75)
    ap.add_argument("--chapter-max-ratio", type=float, default=1.35)
    ap.add_argument("--plan-max-tries", type=int, default=2, help="Max attempts for plan JSON schema validation")
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
    blocks = _default_block_template(block_count)

    # Use existing pattern core_message when possible (metadata may already carry it).
    core_message_hint = str(st.metadata.get("concept_intent") or st.metadata.get("key_concept") or "").strip()

    plan_path = analysis_dir / "plan.json"
    plan_latest = analysis_dir / "plan__latest.json"
    plan_meta_path = analysis_dir / "plan__llm_meta.json"

    # 1) Plan (LLM JSON) — prerequisite for stable chapter goals.
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
    quote_max = int(st.metadata.get("a_text_quote_marks_max") or 20)
    paren_max = int(st.metadata.get("a_text_paren_marks_max") or 10)

    base_meta = dict(st.metadata or {})
    base_meta["a_text_quote_marks_max"] = quote_max
    base_meta["a_text_paren_marks_max"] = paren_max

    drafted: list[tuple[PlanChapter, str]] = []
    previous_tail = ""

    for chapter in plan.chapters:
        out_path = chapters_dir / f"chapter_{chapter.chapter:03d}.md"
        if out_path.exists() and out_path.stat().st_size > 0:
            text = out_path.read_text(encoding="utf-8").strip()
            drafted.append((chapter, text))
            previous_tail = text[-320:] if text else previous_tail
            continue

        prompt_base = _chapter_draft_prompt(
            title=title,
            chapter=chapter,
            chapter_count=plan.chapter_count,
            persona=persona,
            channel_prompt=channel_prompt,
            core_message=plan.core_message,
            previous_tail=previous_tail,
            quote_max=quote_max,
            paren_max=paren_max,
        )
        messages = [{"role": "user", "content": prompt_base}]

        text: str | None = None
        llm_meta: dict[str, Any] | None = None
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

            issues, stats = _validate_chapter_text(
                chapter_text,
                base_meta=base_meta,
                char_budget=chapter.char_budget,
                min_ratio=float(args.chapter_min_ratio),
                max_ratio=float(args.chapter_max_ratio),
                closing_allowed=chapter.closing_allowed,
            )
            hard = [it for it in issues if str(it.get("severity") or "error").lower() != "warning"]
            if not hard:
                text = chapter_text.strip()
                break

            # Rewrite with explicit issues (same task, new prompt).
            messages = [{"role": "user", "content": _chapter_rewrite_prompt(title=title, base_prompt=prompt_base, issues=hard)}]

        if not text:
            raise SystemExit(f"Failed to draft chapter {chapter.chapter:03d} within max tries")

        out_path.write_text(text.strip() + "\n", encoding="utf-8")
        if llm_meta:
            (chapters_dir / f"chapter_{chapter.chapter:03d}__llm_meta.json").write_text(
                json.dumps(llm_meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

        drafted.append((chapter, text.strip()))
        previous_tail = text.strip()[-320:]

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
