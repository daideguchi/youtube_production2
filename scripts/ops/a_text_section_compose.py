#!/usr/bin/env python3
"""
Section-based A-text composer (deterministic plan + LLM section drafts + LLM assembly).

Why:
- One-shot long-form generation tends to drift, repeat, or bloat.
- For long scripts, best practice is:
  1) Deterministic structure plan (SSOT patterns)
  2) Draft each section with tight local constraints
  3) Assemble with reasoning (smooth transitions + global consistency)
  4) Run existing script_validation (Judge/Fix) only if needed

Safety:
- Default is dry-run: writes candidates under content/analysis/section_compose/ only.
- Use --apply to overwrite canonical A-text (assembled_human.md/assembled.md).
- Respects active coordination locks (agent_org locks).

Usage:
  python scripts/ops/a_text_section_compose.py --channel CH07 --video 009
  python scripts/ops/a_text_section_compose.py --channel CH07 --video 009 --apply --run-validation
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

from factory_common.artifacts.llm_text_output import (
    SourceFile,
    artifact_path_for_output,
    build_ready_artifact,
    write_llm_text_artifact,
)
from factory_common.artifacts.utils import atomic_write_json, utc_now_iso
from factory_common.llm_router import get_router
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock
from factory_common.paths import repo_root, script_data_root
from factory_common.timeline_manifest import sha1_file
from factory_common.alignment import ALIGNMENT_SCHEMA, alignment_suspect_reason, build_alignment_stamp

from packages.script_pipeline.runner import ensure_status
from packages.script_pipeline.sot import load_status, save_status
from packages.script_pipeline.validator import validate_a_text


_TAG_RE = re.compile(r"【([^】]+)】")
_PUNCT_FOR_OVERLAP_RE = re.compile(r"[\s\u3000、。．，・…！？!?,.\"'「」『』（）()【】\[\]<>＜＞:：;；/／\\\\-—–―]+")
_EARLY_CLOSING_LINE_RE = re.compile(r"^(?:最後に|まとめると|結論として|おわりに|以上|最後は|最後です)[、。]")
_CTA_PHRASE_RE = re.compile(r"(?:ご視聴ありがとうございました|チャンネル登録|高評価|通知|コメント)")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _normalize_channel(ch: str) -> str:
    s = (ch or "").strip().upper()
    if re.fullmatch(r"CH\\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\\d+)", s)
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


def _slug(text: str) -> str:
    s = re.sub(r"\\s+", "_", (text or "").strip())
    s = re.sub(r"[^0-9A-Za-z_\\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:40] or "section"


def _load_yaml(path: Path) -> Dict[str, Any]:
    import yaml

    if not path.exists():
        return {}
    obj = yaml.safe_load(path.read_text(encoding="utf-8"))
    return obj if isinstance(obj, dict) else {}


def _load_patterns_doc() -> Dict[str, Any]:
    # Prefer configured SoT path, fallback to SSOT default.
    sources_path = repo_root() / "configs" / "sources.yaml"
    sources = _load_yaml(sources_path)
    script_globals = sources.get("script_globals") if isinstance(sources, dict) else None
    patterns_path = None
    if isinstance(script_globals, dict):
        patterns_path = script_globals.get("a_text_patterns")
    if isinstance(patterns_path, str) and patterns_path.strip():
        p = Path(patterns_path.strip())
        if not p.is_absolute():
            p = repo_root() / p
        doc = _load_yaml(p)
        if doc:
            return doc
    # fallback
    return _load_yaml(repo_root() / "ssot" / "OPS_SCRIPT_PATTERNS.yaml")


def _pattern_channel_applies(channels: Any, channel: str) -> bool:
    if not isinstance(channels, list) or not channels:
        return False
    norm = str(channel or "").strip().upper()
    for it in channels:
        val = str(it or "").strip()
        if not val:
            continue
        if val == "*":
            return True
        if val.strip().upper() == norm:
            return True
    return False


def _pattern_triggers_match(triggers: Any, title: str) -> Tuple[bool, int]:
    if not isinstance(triggers, dict):
        triggers = {}
    any_tokens = triggers.get("any") or []
    all_tokens = triggers.get("all") or []
    none_tokens = triggers.get("none") or []
    if not isinstance(any_tokens, list):
        any_tokens = []
    if not isinstance(all_tokens, list):
        all_tokens = []
    if not isinstance(none_tokens, list):
        none_tokens = []

    raw = str(title or "")
    raw_lower = raw.lower()

    def _has(token: Any) -> bool:
        t = str(token or "").strip()
        if not t:
            return False
        return (t in raw) or (t.lower() in raw_lower)

    if none_tokens and any(_has(t) for t in none_tokens):
        return False, 0
    if all_tokens and not all(_has(t) for t in all_tokens):
        return False, 0
    if any_tokens and not any(_has(t) for t in any_tokens):
        return False, 0

    score = 0
    score += len([t for t in any_tokens if _has(t)])
    score += 2 * len([t for t in all_tokens if _has(t)])
    return True, score


def _select_pattern(patterns_doc: Dict[str, Any], channel: str, title: str) -> Dict[str, Any]:
    patterns = patterns_doc.get("patterns")
    if not isinstance(patterns, list):
        return {}

    best: Dict[str, Any] = {}
    best_score = -1
    norm_channel = str(channel or "").strip().upper()
    for pat in patterns:
        if not isinstance(pat, dict):
            continue
        chans = pat.get("channels")
        if not _pattern_channel_applies(chans, norm_channel):
            continue
        ok, score = _pattern_triggers_match(pat.get("triggers"), title)
        if not ok:
            continue
        if score == best_score and isinstance(best.get("channels"), list):
            best_is_wild = "*" in [str(x or "").strip() for x in (best.get("channels") or [])]
            cur_is_wild = "*" in [str(x or "").strip() for x in (chans or [])]
            if best_is_wild and not cur_is_wild:
                best = pat
                best_score = score
                continue
        if score > best_score:
            best = pat
            best_score = score
    if best:
        return best

    # fallback: first applicable wildcard
    for pat in patterns:
        if not isinstance(pat, dict):
            continue
        chans = pat.get("channels")
        if _pattern_channel_applies(chans, norm_channel) and "*" in [str(x or "").strip() for x in (chans or [])]:
            return pat
    return {}


def _scale_section_budgets(
    sections: list[dict[str, Any]], target_min: int | None, target_max: int | None
) -> list[dict[str, Any]]:
    if not sections:
        return sections
    budgets: list[int] = []
    for s in sections:
        try:
            budgets.append(int(s.get("char_budget") or 0))
        except Exception:
            budgets.append(0)
    total = sum(budgets)
    if total <= 0:
        return sections

    desired = total
    if isinstance(target_min, int) and desired < target_min:
        if isinstance(target_max, int) and target_max >= target_min:
            desired = int(round((target_min + target_max) / 2))
        else:
            desired = target_min
    if isinstance(target_max, int) and desired > target_max:
        if isinstance(target_min, int) and target_min <= target_max:
            desired = int(round((target_min + target_max) / 2))
        else:
            desired = target_max
    if desired == total:
        return sections

    scale = desired / total
    scaled = [max(1, int(round(b * scale))) for b in budgets]
    diff = desired - sum(scaled)
    order = sorted(range(len(scaled)), key=lambda i: scaled[i], reverse=True)
    idx = 0
    while diff != 0 and order:
        i = order[idx % len(order)]
        if diff > 0:
            scaled[i] += 1
            diff -= 1
        else:
            if scaled[i] > 1:
                scaled[i] -= 1
                diff += 1
        idx += 1

    out: list[dict[str, Any]] = []
    for i, s in enumerate(sections):
        ss = dict(s)
        ss["char_budget"] = int(scaled[i])
        out.append(ss)
    return out


def _pick_core_episode(candidates: Any, title: str) -> Dict[str, Any]:
    if not isinstance(candidates, list) or not candidates:
        return {}
    raw = str(title or "")
    raw_lower = raw.lower()

    def _has(token: Any) -> bool:
        t = str(token or "").strip()
        if not t:
            return False
        return (t in raw) or (t.lower() in raw_lower)

    best: Dict[str, Any] = {}
    best_score = -1
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        keywords = cand.get("keywords") or []
        if not isinstance(keywords, list):
            keywords = []
        score = len([k for k in keywords if _has(k)])
        if score > best_score:
            best = cand
            best_score = score
    return best or (candidates[0] if isinstance(candidates[0], dict) else {})


def _a_text_rules_summary(meta: dict[str, Any]) -> str:
    quote_max = None
    paren_max = None
    try:
        quote_max = int((meta or {}).get("a_text_quote_marks_max") or 0) or None
    except Exception:
        quote_max = None
    try:
        paren_max = int((meta or {}).get("a_text_paren_marks_max") or 0) or None
    except Exception:
        paren_max = None

    limit_hint = ""
    if quote_max is not None or paren_max is not None:
        parts: list[str] = []
        if quote_max is not None:
            parts.append(f"鉤括弧<= {quote_max}")
        if paren_max is not None:
            parts.append(f"丸括弧<= {paren_max}")
        if parts:
            limit_hint = " 上限目安: " + " / ".join(parts)
    return "\n".join(
        [
            "- ポーズ記号は --- のみ。1行単独。ほかの区切り記号は禁止",
            "- 見出し/箇条書き/番号リスト/URL/脚注/参照番号/制作メタは禁止",
            "- 鉤括弧と丸括弧は最小限。直接話法の連打を避ける。" + limit_hint,
            "- 根拠不明の統計/研究/固有名詞/数字断定はしない（一般化する）",
            "- 水増し禁止: 同趣旨の言い換え連打、抽象語の連打、雰囲気だけの段落",
            "- 作り話感の強い現代ストーリー（年齢/職業/台詞の作り込み）を避ける",
            "- 深く狭く: タイトルの主題を1つ深掘りして収束させる",
        ]
    )


def _sanitize_context(text: str, *, max_chars: int) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""
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


@dataclass(frozen=True)
class LlmMeta:
    provider: Any = None
    model: Any = None
    request_id: Any = None
    chain: Any = None
    latency_ms: Any = None
    usage: Any = None
    finish_reason: Any = None
    routing: Any = None
    cache: Any = None

    @classmethod
    def from_result(cls, result: Dict[str, Any]) -> "LlmMeta":
        return cls(
            provider=result.get("provider"),
            model=result.get("model"),
            request_id=result.get("request_id"),
            chain=result.get("chain"),
            latency_ms=result.get("latency_ms"),
            usage=result.get("usage") or {},
            finish_reason=result.get("finish_reason"),
            routing=result.get("routing"),
            cache=result.get("cache"),
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "request_id": self.request_id,
            "chain": self.chain,
            "latency_ms": self.latency_ms,
            "usage": self.usage,
            "finish_reason": self.finish_reason,
            "routing": self.routing,
            "cache": self.cache,
        }


def _assert_not_locked(paths: list[Path]) -> None:
    locks = default_active_locks_for_mutation()
    for p in paths:
        lock = find_blocking_lock(p, locks)
        if lock:
            raise SystemExit(
                f"Blocked by lock {lock.lock_id} (mode={lock.mode}, by={lock.created_by}) for path: {p}"
            )


def _hard_errors(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        it
        for it in (issues or [])
        if str((it or {}).get("severity") or "error").lower() != "warning"
    ]


def _dedupe_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uniq: dict[tuple[str, Optional[int]], dict[str, Any]] = {}
    for item in issues or []:
        code = str((item or {}).get("code") or "")
        line = item.get("line") if isinstance(item, dict) else None
        try:
            line_i = int(line) if line is not None else None
        except Exception:
            line_i = None
        key = (code, line_i)
        if key not in uniq:
            uniq[key] = item
    return list(uniq.values())


def _normalize_for_overlap(text: str) -> str:
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = _PUNCT_FOR_OVERLAP_RE.sub("", raw)
    return raw.strip().lower()


def _has_long_overlap(prev_tail: str, current: str, *, min_chars: int) -> bool:
    if min_chars <= 0:
        return False
    a = _normalize_for_overlap(prev_tail)
    b = _normalize_for_overlap(current)
    if len(a) < min_chars or len(b) < min_chars:
        return False
    # Check a few windows from the tail to avoid false positives while catching copy-paste.
    windows: list[str] = []
    windows.append(a[-min_chars:])
    if len(a) >= min_chars * 2:
        windows.append(a[-min_chars * 2 : -min_chars])
    if len(a) >= min_chars * 3:
        windows.append(a[-min_chars * 3 : -min_chars * 2])
    for w in windows:
        if w and w in b:
            return True
    return False


def _validate_section_draft(
    text: str,
    *,
    base_metadata: dict[str, Any],
    char_budget: int,
    min_ratio: float,
    max_ratio: float,
    section_index: int,
    section_total: int,
    previous_section_tail: str,
    overlap_min_chars: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta = dict(base_metadata or {})
    if char_budget > 0:
        meta["target_chars_min"] = int(round(char_budget * float(min_ratio)))
        meta["target_chars_max"] = int(round(char_budget * float(max_ratio)))
    issues, stats = validate_a_text(text, meta)

    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    for idx, line in enumerate(lines, start=1):
        if line.strip() == "---":
            issues.append(
                {
                    "code": "section_pause_not_allowed",
                    "message": "`---` must not appear inside section drafts (added in assembly only)",
                    "line": idx,
                    "severity": "error",
                }
            )

    # Non-final sections must not look like an ending (reduces repetition of conclusions).
    if section_index < section_total:
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if _EARLY_CLOSING_LINE_RE.match(stripped) or _CTA_PHRASE_RE.search(stripped):
                issues.append(
                    {
                        "code": "premature_closing",
                        "message": "Non-final section contains a closing/CTA-like phrase",
                        "line": idx,
                        "severity": "error",
                    }
                )
                break

    if previous_section_tail and overlap_min_chars > 0:
        if _has_long_overlap(previous_section_tail, text, min_chars=int(overlap_min_chars)):
            issues.append(
                {
                    "code": "section_overlap_with_previous",
                    "message": f"Detected long overlap with previous section tail (min_chars={overlap_min_chars})",
                    "severity": "error",
                }
            )

    return _dedupe_issues(issues), stats


def _format_issues_for_prompt(issues: list[dict[str, Any]], *, max_items: int = 12) -> str:
    out: list[str] = []
    for it in (issues or [])[: max(0, int(max_items))]:
        code = str((it or {}).get("code") or "").strip() or "issue"
        msg = str((it or {}).get("message") or "").strip()
        line = (it or {}).get("line")
        line_txt = ""
        if line not in (None, ""):
            line_txt = f" (line {line})"
        out.append(f"- {code}{line_txt}: {msg}")
    return "\n".join(out) if out else "- (no details)"


def _planning_l1(meta: Dict[str, Any]) -> Dict[str, str]:
    planning = meta.get("planning") if isinstance(meta.get("planning"), dict) else {}
    if not isinstance(planning, dict):
        planning = {}
    out: Dict[str, str] = {}
    for key in ("concept_intent", "target_audience", "outline_notes"):
        v = planning.get(key) or meta.get(key)
        if isinstance(v, str) and v.strip():
            out[key] = v.strip()
    # Optional key concept is allowed when present (and not dropped by contract).
    kc = planning.get("key_concept") or meta.get("key_concept")
    if isinstance(kc, str) and kc.strip():
        out["key_concept"] = kc.strip()
    return out


def build_plan(*, channel: str, title: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    patterns_doc = _load_patterns_doc()
    pat = _select_pattern(patterns_doc, channel, title) if patterns_doc else {}
    plan_cfg = pat.get("plan") if isinstance(pat, dict) else None
    if not isinstance(plan_cfg, dict):
        plan_cfg = {}

    target_min = None
    target_max = None
    try:
        if meta.get("target_chars_min") not in (None, ""):
            target_min = int(meta.get("target_chars_min"))
    except Exception:
        target_min = None
    try:
        if meta.get("target_chars_max") not in (None, ""):
            target_max = int(meta.get("target_chars_max"))
    except Exception:
        target_max = None

    sections_raw = plan_cfg.get("sections") or []
    if isinstance(sections_raw, list):
        section_dicts = [s for s in sections_raw if isinstance(s, dict)]
    else:
        section_dicts = []
    section_dicts = _scale_section_budgets(section_dicts, target_min, target_max)

    core_episode: Dict[str, Any] = {}
    candidates = plan_cfg.get("core_episode_candidates") or plan_cfg.get("buddhist_episode_candidates")
    picked = _pick_core_episode(candidates, title)
    if isinstance(picked, dict) and picked:
        core_episode = {
            "topic": str(picked.get("topic") or "").strip(),
            "must_include": picked.get("must_include") if isinstance(picked.get("must_include"), list) else [],
            "avoid_claims": picked.get("avoid_claims") if isinstance(picked.get("avoid_claims"), list) else [],
            "safe_retelling": str(picked.get("safe_retelling") or "").strip(),
        }

    modern_policy = plan_cfg.get("modern_example_policy") if isinstance(plan_cfg, dict) else None
    if not isinstance(modern_policy, dict):
        modern_policy = {}
    max_examples = modern_policy.get("max_examples")
    try:
        max_examples_i = int(max_examples) if max_examples is not None else 1
    except Exception:
        max_examples_i = 1
    example_hint = str(modern_policy.get("example_1_hint") or "").strip()
    if not example_hint:
        example_hint = str(meta.get("life_scene") or "").strip()

    plan_obj: Dict[str, Any] = {
        "pattern_id": str((pat or {}).get("id") or "").strip(),
        "core_message": str(plan_cfg.get("core_message_template") or "").strip(),
        "sections": [
            {
                "name": str(s.get("name") or "").strip(),
                "char_budget": int(s.get("char_budget") or 0),
                "goal": str(s.get("goal") or "").strip(),
                "content_notes": str(s.get("content_notes") or "").strip(),
            }
            for s in section_dicts
            if str(s.get("name") or "").strip()
        ],
        "modern_examples_policy": {
            "max_examples": max(0, int(max_examples_i)),
            "example_1": example_hint,
        },
        "style_guardrails": [
            "同趣旨の言い換えで水増ししない",
            "終盤でまとめを連打しない（『最後に』は1回まで）",
            "タイトルの主題から寄り道しない（主題は1つ）",
            "現代の人物例は最大1つ（年齢/職業/台詞の作り込み禁止）",
            "`---` は話題の切れ目にだけ置く（等間隔は禁止）",
        ],
    }
    if core_episode:
        plan_obj["core_episode"] = core_episode
    return plan_obj


def _section_draft_prompt(
    *,
    title: str,
    section_index: int,
    section_total: int,
    section: Dict[str, Any],
    plan_obj: Dict[str, Any],
    planning_l1: Dict[str, str],
    persona: str,
    channel_prompt: str,
    a_text_rules: str,
    previous_section_tail: str,
) -> str:
    name = str(section.get("name") or "").strip()
    goal = str(section.get("goal") or "").strip()
    notes = str(section.get("content_notes") or "").strip()
    budget = int(section.get("char_budget") or 0)
    is_last = section_index == section_total

    return (
        "あなたは日本語のYouTubeナレーション台本（Aテキスト）作家です。\n"
        "これから『長尺台本の一部（1セクション）だけ』を書いてください。\n"
        "出力は本文のみ。見出し/箇条書き/番号リスト/URL/脚注/参照番号/制作メタは禁止。\n"
        "このセクション内では `---` を使わないでください（全体の区切りは後工程で付与します）。\n"
        "\n"
        f"【企画タイトル】\n{title}\n\n"
        f"【セクション】({section_index}/{section_total}) {name}\n"
        f"- 目標文字数（改行除外・目安）: {budget}\n"
        f"- このセクションの目的: {goal}\n"
        + (f"- メモ: {notes}\n" if notes else "")
        + "\n"
        "【全体設計（要約）】\n"
        + _truncate(json.dumps(plan_obj, ensure_ascii=False, indent=2), 1800)
        + "\n\n"
        "【企画メモ（L1）】\n"
        + _truncate("\n".join([f"- {k}: {v}" for k, v in planning_l1.items()]), 800)
        + "\n\n"
        "【ペルソナ（要点）】\n"
        + _truncate(persona, 900)
        + "\n\n"
        "【チャンネル指針（要点）】\n"
        + _truncate(channel_prompt, 900)
        + "\n\n"
        "【全体ルール（要点）】\n"
        + a_text_rules
        + "\n\n"
        "【直前セクションの末尾（文脈）】\n"
        + (_truncate(previous_section_tail, 320) if previous_section_tail else "(なし)")
        + "\n\n"
        "【重要】\n"
        "- 主題は増やさない。タイトルの痛み/問いに直結する内容だけを書く。\n"
        "- 同じ結論の言い換えで水増ししない。新しい理解が増える具体で厚みを作る。\n"
        "- 終盤っぽい『最後に』『もう一度』の連打は禁止（最後のセクションだけ短く締める）。\n"
        + ("- 最終セクションなので、短く締めてよい（締めの言葉は1回だけ）。\n" if is_last else "")
        + "\n"
        "では、このセクション本文を書いてください。\n"
    )


def _section_rewrite_prompt(
    *,
    title: str,
    section_index: int,
    section_total: int,
    section: Dict[str, Any],
    plan_obj: Dict[str, Any],
    planning_l1: Dict[str, str],
    persona: str,
    channel_prompt: str,
    a_text_rules: str,
    previous_section_tail: str,
    previous_draft: str,
    detected_issues: list[dict[str, Any]],
    min_chars: int | None,
    max_chars: int | None,
    attempt: int,
) -> str:
    name = str(section.get("name") or "").strip()
    goal = str(section.get("goal") or "").strip()
    notes = str(section.get("content_notes") or "").strip()
    budget = int(section.get("char_budget") or 0)
    is_last = section_index == section_total
    issues_txt = _format_issues_for_prompt(detected_issues)
    range_txt = ""
    if min_chars is not None or max_chars is not None:
        range_txt = f"（許容レンジ: {min_chars}〜{max_chars}）"

    return (
        "あなたは日本語のYouTubeナレーション台本（Aテキスト）作家です。\n"
        "前回のセクション草稿に不備があったため、同じセクションを丸ごと書き直してください。\n"
        "出力は本文のみ。見出し/箇条書き/番号リスト/URL/脚注/参照番号/制作メタは禁止。\n"
        "このセクション内では `---` を使わないでください（全体の区切りは後工程で付与します）。\n"
        "\n"
        f"【企画タイトル】\n{title}\n\n"
        f"【セクション】({section_index}/{section_total}) {name}\n"
        f"- 目標文字数（改行除外・目安）: {budget}{range_txt}\n"
        f"- このセクションの目的: {goal}\n"
        + (f"- メモ: {notes}\n" if notes else "")
        + f"- これは再生成 attempt={attempt} です。必ず不備を解消してください。\n"
        "\n"
        "【検出された不備（修正必須）】\n"
        + issues_txt
        + "\n\n"
        "【全体設計（要約）】\n"
        + _truncate(json.dumps(plan_obj, ensure_ascii=False, indent=2), 1600)
        + "\n\n"
        "【企画メモ（L1）】\n"
        + _truncate("\n".join([f"- {k}: {v}" for k, v in planning_l1.items()]), 700)
        + "\n\n"
        "【ペルソナ（要点）】\n"
        + _truncate(persona, 900)
        + "\n\n"
        "【チャンネル指針（要点）】\n"
        + _truncate(channel_prompt, 900)
        + "\n\n"
        "【全体ルール（要点）】\n"
        + a_text_rules
        + "\n\n"
        "【直前セクションの末尾（文脈）】\n"
        + (_truncate(previous_section_tail, 320) if previous_section_tail else "(なし)")
        + "\n\n"
        "【前回草稿（参考。言い回しをコピーしない）】\n"
        + _truncate(previous_draft, 1400)
        + "\n\n"
        "【重要】\n"
        "- 主題は増やさない。タイトルの痛み/問いに直結する内容だけを書く。\n"
        "- 同じ結論の言い換えで水増ししない。新しい理解が増える具体で厚みを作る。\n"
        "- 終盤っぽい『最後に』『まとめると』などは最後のセクションだけ。\n"
        + ("- 最終セクションなので、短く締めてよい（締めの言葉は1回だけ）。\n" if is_last else "")
        + "\n"
        "では、修正版のセクション本文のみを出力してください。\n"
    )


def _assemble_prompt(
    *,
    title: str,
    plan_obj: Dict[str, Any],
    planning_l1: Dict[str, str],
    persona: str,
    channel_prompt: str,
    a_text_rules: str,
    target_min: str,
    target_max: str,
    section_texts: List[Tuple[str, str]],
) -> str:
    # section_texts: [(section_name, text)]
    parts: list[str] = []
    for name, txt in section_texts:
        parts.append(f"--- {name} ---\n{txt.strip()}\n")
    joined = "\n".join(parts).strip()

    return (
        "あなたは日本語のYouTubeナレーション台本（Aテキスト）の編集者です。\n"
        "以下の『セクション草稿』を、一本の長尺Aテキストとして組み上げてください。\n"
        "目的は『一貫した主題』『自然な繋ぎ』『水増し/反復の排除』『字数レンジ遵守』です。\n"
        "\n"
        "出力は完成した台本本文のみ。説明・見出し・箇条書き・番号リストは禁止。\n"
        "区切りは `---` のみ（1行単独）。セクション間にだけ入れ、乱発しない。\n"
        "\n"
        f"【企画タイトル】\n{title}\n\n"
        f"【目標文字数（改行除外）】min={target_min} / max={target_max}\n\n"
        "【全体設計（plan JSON）】\n"
        + _truncate(json.dumps(plan_obj, ensure_ascii=False, indent=2), 2400)
        + "\n\n"
        "【企画メモ（L1）】\n"
        + _truncate("\n".join([f"- {k}: {v}" for k, v in planning_l1.items()]), 900)
        + "\n\n"
        "【ペルソナ（要点）】\n"
        + _truncate(persona, 900)
        + "\n\n"
        "【チャンネル指針（要点）】\n"
        + _truncate(channel_prompt, 900)
        + "\n\n"
        "【全体ルール（要点）】\n"
        + a_text_rules
        + "\n\n"
        "【セクション草稿】\n"
        + _truncate(joined, 12000)
        + "\n\n"
        "【編集ルール】\n"
        "- タイトルの主題から絶対に逸れない。主題は1つだけ。\n"
        "- 同趣旨の言い換え連打/まとめ重複を削る（『最後に』は1回まで）。\n"
        "- セクション間に自然な繋ぎを1〜2文だけ付ける（長い橋渡しは禁止）。\n"
        "- 現代の人物例は最大1つ。作り話感の強いディテールの作り込みは禁止。\n"
        "- 結びは短く、締めの言葉は1回だけ。\n"
        "\n"
        "では、完成したAテキスト本文のみを出力してください。\n"
    )


def _assemble_retry_prompt(
    *,
    title: str,
    target_min: str,
    target_max: str,
    section_texts: List[Tuple[str, str]],
    previous_draft: str,
    detected_issues: list[dict[str, Any]],
    attempt: int,
) -> str:
    parts: list[str] = []
    for name, txt in section_texts:
        parts.append(f"--- {name} ---\n{txt.strip()}\n")
    joined = "\n".join(parts).strip()

    return (
        "あなたは日本語のYouTubeナレーション台本（Aテキスト）の編集者です。\n"
        "前回の組み上げ結果に『機械的な禁則違反』があるため、本文を丸ごと書き直してください。\n"
        "出力は修正版の本文のみ。説明は禁止。\n"
        "\n"
        f"【企画タイトル】\n{title}\n\n"
        f"【目標文字数（改行除外）】min={target_min} / max={target_max}\n"
        f"- これは再生成 attempt={attempt} です。必ず不備を解消してください。\n\n"
        "【検出された不備（修正必須）】\n"
        + _format_issues_for_prompt(detected_issues)
        + "\n\n"
        "【元のセクション草稿】\n"
        + _truncate(joined, 11000)
        + "\n\n"
        "【前回の組み上げ本文（参考。禁則を直しつつ意味は維持）】\n"
        + _truncate(previous_draft, 12000)
        + "\n\n"
        "【編集ルール（厳守）】\n"
        "- 出力は本文のみ（見出し/箇条書き/番号リスト/URL/脚注/参照番号/制作メタは禁止）。\n"
        "- 区切りは `---` のみ（1行単独）。セクション間にだけ入れ、乱発しない。\n"
        "- 同趣旨の言い換え連打/まとめ重複を削る（『最後に』は1回まで）。\n"
        "- タイトルの主題から絶対に逸れない（主題は1つ）。\n"
        "\n"
        "では、修正版のAテキスト本文のみを出力してください。\n"
    )


def _truncate(text: str, max_chars: int) -> str:
    raw = str(text or "")
    if len(raw) <= max_chars:
        return raw
    return raw[:max_chars].rstrip() + "…"


def _tail(text: str, max_chars: int = 260) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    return raw[-max_chars:]


def _call_llm(*, task: str, prompt: str, routing_key: str, max_tokens: int, temperature: float) -> Tuple[str, LlmMeta]:
    router = get_router()
    prev = os.environ.get("LLM_ROUTING_KEY")
    os.environ["LLM_ROUTING_KEY"] = routing_key
    try:
        result = router.call_with_raw(
            task=task,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=int(max_tokens),
            temperature=float(temperature),
        )
    finally:
        if prev is None:
            os.environ.pop("LLM_ROUTING_KEY", None)
        else:
            os.environ["LLM_ROUTING_KEY"] = prev
    text = _extract_llm_text_content(result) or ""
    return text.strip(), LlmMeta.from_result(result)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", required=True, help="Channel code like CH07")
    ap.add_argument("--video", required=True, help="Video number like 009")
    ap.add_argument("--title", default="", help="Override title (optional; Planning SoT title is preferred)")
    ap.add_argument("--apply", action="store_true", help="Overwrite canonical A-text (assembled_human.md/assembled.md)")
    ap.add_argument(
        "--run-validation",
        action="store_true",
        help="After --apply, run script_validation stage (LLM Judge/Fix) to converge quality",
    )
    ap.add_argument("--max-tokens", type=int, default=16384)
    ap.add_argument("--temperature", type=float, default=0.25)
    ap.add_argument("--section-max-tries", type=int, default=3, help="Max LLM tries per section when validation fails")
    ap.add_argument("--section-min-ratio", type=float, default=0.70, help="Min ratio for section char_budget length gate")
    ap.add_argument("--section-max-ratio", type=float, default=1.45, help="Max ratio for section char_budget length gate")
    ap.add_argument(
        "--section-overlap-min-chars",
        type=int,
        default=160,
        help="Min normalized chars overlap with previous section tail to treat as duplication",
    )
    ap.add_argument(
        "--assemble-max-tries",
        type=int,
        default=1,
        help="Retry assembly when deterministic validation has hard errors (max tries)",
    )
    args = ap.parse_args()

    ch = _normalize_channel(args.channel)
    no = _normalize_video(args.video)

    # Ensure status exists and planning contract is applied.
    st = ensure_status(ch, no, args.title or None)
    st = load_status(ch, no)

    base = script_data_root() / ch / no
    content_dir = base / "content"
    analysis_dir = content_dir / "analysis" / "section_compose"
    sections_dir = analysis_dir / "sections"
    _assert_not_locked([analysis_dir, sections_dir])
    analysis_dir.mkdir(parents=True, exist_ok=True)
    sections_dir.mkdir(parents=True, exist_ok=True)

    title = str(st.metadata.get("sheet_title") or st.metadata.get("expected_title") or st.metadata.get("title") or "").strip()
    if args.title.strip():
        title = args.title.strip()
    if not title:
        raise SystemExit("Title is empty (set in Planning CSV or pass --title)")

    planning_l1 = _planning_l1(st.metadata or {})
    persona = _sanitize_context(str(st.metadata.get("persona") or ""), max_chars=1200)
    channel_prompt = _sanitize_context(str(st.metadata.get("a_text_channel_prompt") or st.metadata.get("script_prompt") or ""), max_chars=1200)
    a_text_rules = _a_text_rules_summary(st.metadata or {})

    plan_obj = build_plan(channel=ch, title=title, meta=st.metadata or {})
    plan_latest_path = analysis_dir / "plan_latest.json"
    _assert_not_locked([plan_latest_path])
    atomic_write_json(
        plan_latest_path,
        {
            "schema": "ytm.a_text_section_compose_plan.v1",
            "generated_at": utc_now_iso(),
            "episode": {"channel": ch, "video": no},
            "title": title,
            "planning_l1": planning_l1,
            "plan": plan_obj,
        },
    )
    try:
        plan_sha1 = sha1_file(plan_latest_path)
    except Exception:
        plan_sha1 = ""

    sections = plan_obj.get("sections") if isinstance(plan_obj, dict) else None
    if not isinstance(sections, list) or not sections:
        raise SystemExit("No sections found in plan (patterns misconfigured?)")

    routing_key = f"{ch}-{no}"
    draft_task = os.getenv("SCRIPT_VALIDATION_QUALITY_REBUILD_DRAFT_TASK", "script_a_text_rebuild_draft").strip()

    # Draft each section with local constraints.
    section_texts: list[Tuple[str, str]] = []
    sections_report: list[dict[str, Any]] = []
    prev_tail = ""
    for i, sec in enumerate(sections, start=1):
        sec_name = str(sec.get("name") or f"section_{i}").strip()
        base_name = f"section_{i:02d}_{_slug(sec_name)}"
        final_path = sections_dir / f"{base_name}.md"
        attempts: list[dict[str, Any]] = []

        budget = int(sec.get("char_budget") or 0)
        min_chars = int(round(budget * float(args.section_min_ratio))) if budget > 0 else None
        max_chars = int(round(budget * float(args.section_max_ratio))) if budget > 0 else None

        previous_draft = ""
        detected: list[dict[str, Any]] = []
        accepted_text = ""
        accepted_meta: Optional[LlmMeta] = None

        for attempt in range(1, max(1, int(args.section_max_tries)) + 1):
            if attempt == 1:
                prompt = _section_draft_prompt(
                    title=title,
                    section_index=i,
                    section_total=len(sections),
                    section=sec,
                    plan_obj=plan_obj,
                    planning_l1=planning_l1,
                    persona=persona,
                    channel_prompt=channel_prompt,
                    a_text_rules=a_text_rules,
                    previous_section_tail=prev_tail,
                )
            else:
                prompt = _section_rewrite_prompt(
                    title=title,
                    section_index=i,
                    section_total=len(sections),
                    section=sec,
                    plan_obj=plan_obj,
                    planning_l1=planning_l1,
                    persona=persona,
                    channel_prompt=channel_prompt,
                    a_text_rules=a_text_rules,
                    previous_section_tail=prev_tail,
                    previous_draft=previous_draft,
                    detected_issues=detected,
                    min_chars=min_chars,
                    max_chars=max_chars,
                    attempt=attempt,
                )

            txt, llm_meta = _call_llm(
                task=draft_task,
                prompt=prompt,
                routing_key=routing_key,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
            )
            if not txt.strip():
                raise SystemExit(f"Empty section draft for {sec_name} (attempt {attempt})")

            attempt_path = sections_dir / f"{base_name}__try{attempt:02d}.md"
            _assert_not_locked([attempt_path])
            attempt_path.write_text(txt.strip() + "\n", encoding="utf-8")

            # Record artifact for reproducibility.
            try:
                art_path = artifact_path_for_output(
                    base_dir=base,
                    stage="a_text_section_draft",
                    output_path=attempt_path,
                    log_suffix=f"__{i:02d}__try{attempt:02d}",
                )
                sources: list[SourceFile] = []
                try:
                    sources.append(SourceFile(path=str(plan_latest_path), sha1=str(plan_sha1 or "")))
                except Exception:
                    sources = []
                art = build_ready_artifact(
                    stage="a_text_section_draft",
                    task=draft_task,
                    channel=ch,
                    video=no,
                    output_path=attempt_path,
                    content=txt.strip(),
                    sources=sources,
                    llm_meta=llm_meta.as_dict(),
                    notes=f"section={sec_name}, attempt={attempt}",
                )
                write_llm_text_artifact(art_path, art)
            except Exception:
                pass

            issues, stats = _validate_section_draft(
                txt,
                base_metadata=st.metadata or {},
                char_budget=budget,
                min_ratio=float(args.section_min_ratio),
                max_ratio=float(args.section_max_ratio),
                section_index=i,
                section_total=len(sections),
                previous_section_tail=prev_tail,
                overlap_min_chars=int(args.section_overlap_min_chars),
            )
            hard = _hard_errors(issues)
            attempts.append(
                {
                    "attempt": attempt,
                    "path": str(attempt_path.relative_to(repo_root())),
                    "char_count": stats.get("char_count"),
                    "validation": {"issues": issues, "stats": stats},
                    "hard_error_codes": [str((x or {}).get("code") or "") for x in hard],
                }
            )

            if not hard:
                accepted_text = txt.strip()
                accepted_meta = llm_meta
                break

            previous_draft = txt.strip()
            detected = hard

        if not accepted_text.strip():
            # Keep attempts for debugging and fail fast (do not assemble broken sections).
            fail_report = analysis_dir / "report_latest.json"
            try:
                atomic_write_json(
                    fail_report,
                    {
                        "schema": "ytm.a_text_section_compose_report.v1",
                        "generated_at": utc_now_iso(),
                        "episode": {"channel": ch, "video": no},
                        "title": title,
                        "tasks": {"draft_task": draft_task},
                        "outputs": {"plan": str(plan_latest_path.relative_to(repo_root()))},
                        "sections": sections_report
                        + [
                            {
                                "section_index": i,
                                "section_name": sec_name,
                                "char_budget": budget,
                                "attempts": attempts,
                                "status": "failed",
                            }
                        ],
                    },
                )
            except Exception:
                pass
            raise SystemExit(
                f"Section draft failed after {int(args.section_max_tries)} tries: ({i}/{len(sections)}) {sec_name}"
            )

        _assert_not_locked([final_path])
        final_path.write_text(accepted_text.strip() + "\n", encoding="utf-8")
        sections_report.append(
            {
                "section_index": i,
                "section_name": sec_name,
                "char_budget": budget,
                "accepted": {"path": str(final_path.relative_to(repo_root())), "attempt": attempts[-1]["attempt"]},
                "attempts": attempts,
                "llm_meta": (accepted_meta.as_dict() if accepted_meta else None),
            }
        )

        section_texts.append((sec_name, accepted_text.strip()))
        prev_tail = _tail(accepted_text, 260)

    # Assemble with reasoning: smooth transitions + global coherence.
    target_min = str(st.metadata.get("target_chars_min") or "")
    target_max = str(st.metadata.get("target_chars_max") or "")
    assemble_prompt = _assemble_prompt(
        title=title,
        plan_obj=plan_obj,
        planning_l1=planning_l1,
        persona=persona,
        channel_prompt=channel_prompt,
        a_text_rules=a_text_rules,
        target_min=target_min,
        target_max=target_max,
        section_texts=section_texts,
    )
    candidate_latest_path = analysis_dir / "assembled_candidate_latest.md"
    report_latest_path = analysis_dir / "report_latest.json"
    _assert_not_locked([candidate_latest_path, report_latest_path])

    assemble_attempts: list[dict[str, Any]] = []
    base_ts = _utc_now_compact()
    candidate_path: Optional[Path] = None
    assembled_text = ""
    assembled_meta: Optional[LlmMeta] = None
    issues: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}
    hard_errors: list[dict[str, Any]] = []

    prev_draft = ""
    prev_hard: list[dict[str, Any]] = []
    for attempt in range(1, max(1, int(args.assemble_max_tries)) + 1):
        prompt = (
            assemble_prompt
            if attempt == 1
            else _assemble_retry_prompt(
                title=title,
                target_min=target_min,
                target_max=target_max,
                section_texts=section_texts,
                previous_draft=prev_draft,
                detected_issues=prev_hard,
                attempt=attempt,
            )
        )
        assembled_text, assembled_meta = _call_llm(
            task=draft_task,
            prompt=prompt,
            routing_key=routing_key,
            max_tokens=args.max_tokens,
            temperature=max(0.1, float(args.temperature)),
        )
        if not assembled_text.strip():
            raise SystemExit(f"Empty assembled draft (attempt {attempt})")

        candidate_path = (
            analysis_dir / f"assembled_candidate__{base_ts}.md"
            if attempt == 1
            else analysis_dir / f"assembled_candidate__{base_ts}__try{attempt:02d}.md"
        )
        _assert_not_locked([candidate_path])
        candidate_path.write_text(assembled_text.strip() + "\n", encoding="utf-8")
        candidate_latest_path.write_text(assembled_text.strip() + "\n", encoding="utf-8")

        # Record artifact for reproducibility.
        try:
            art_path = artifact_path_for_output(
                base_dir=base,
                stage="a_text_section_assemble",
                output_path=candidate_path,
                log_suffix=f"__assemble__try{attempt:02d}",
            )
            sources: list[SourceFile] = []
            try:
                sources.append(SourceFile(path=str(plan_latest_path), sha1=str(plan_sha1 or "")))
            except Exception:
                sources = []
            art = build_ready_artifact(
                stage="a_text_section_assemble",
                task=draft_task,
                channel=ch,
                video=no,
                output_path=candidate_path,
                content=assembled_text.strip(),
                sources=sources,
                llm_meta=(assembled_meta.as_dict() if assembled_meta else None),
                notes=f"attempt={attempt}",
            )
            write_llm_text_artifact(art_path, art)
        except Exception:
            pass

        # Deterministic validation (mechanical rules + length).
        issues, stats = validate_a_text(assembled_text, st.metadata or {})
        hard_errors = _hard_errors(issues)
        assemble_attempts.append(
            {
                "attempt": attempt,
                "path": str(candidate_path.relative_to(repo_root())) if candidate_path else "",
                "llm_meta": (assembled_meta.as_dict() if assembled_meta else None),
                "validation": {"issues": issues, "stats": stats},
                "hard_error_codes": [str((x or {}).get("code") or "") for x in hard_errors],
            }
        )
        if not hard_errors:
            break
        prev_draft = assembled_text.strip()
        prev_hard = hard_errors

    report = {
        "schema": "ytm.a_text_section_compose_report.v1",
        "generated_at": utc_now_iso(),
        "episode": {"channel": ch, "video": no},
        "title": title,
        "tasks": {"draft_task": draft_task},
        "outputs": {
            "plan": str((analysis_dir / "plan_latest.json").relative_to(repo_root())),
            "candidate": str(candidate_path.relative_to(repo_root())) if candidate_path else "",
            "candidate_latest": str(candidate_latest_path.relative_to(repo_root())),
        },
        "sections": sections_report,
        "assembly": {"attempts": assemble_attempts},
        "llm_meta": {"assembled": (assembled_meta.as_dict() if assembled_meta else None)},
        "validation": {"issues": issues, "stats": stats},
    }
    atomic_write_json(report_latest_path, report)

    print(f"Wrote plan: {plan_latest_path}")
    if candidate_path:
        print(f"Wrote candidate: {candidate_path}")
    print(f"Wrote report: {report_latest_path}")

    if not args.apply:
        print("dry-run: not overwriting canonical A-text (use --apply)")
        # Exit non-zero when mechanical errors exist, so CI/ops can detect.
        return 1 if hard_errors else 0

    # Safety: do not write an already-invalid script into the canonical SoT unless
    # we are also running the LLM quality gate to converge.
    if hard_errors and not args.run_validation:
        print("Refusing to --apply because deterministic validation has hard errors.")
        print("Re-run with --run-validation (recommended) or fix the candidate manually.")
        return 1

    canonical_human = content_dir / "assembled_human.md"
    canonical_assembled = content_dir / "assembled.md"
    legacy_final = content_dir / "final" / "assembled.md"
    _assert_not_locked([canonical_human, canonical_assembled, legacy_final])
    canonical_human.write_text(assembled_text.strip() + "\n", encoding="utf-8")
    canonical_assembled.write_text(assembled_text.strip() + "\n", encoding="utf-8")
    if legacy_final.exists():
        try:
            legacy_final.write_text(assembled_text.strip() + "\n", encoding="utf-8")
        except Exception:
            pass

    # Update status metadata to record provenance (non-destructive).
    try:
        st.metadata.setdefault("a_text_compose", {})
        if isinstance(st.metadata.get("a_text_compose"), dict):
            st.metadata["a_text_compose"]["last_method"] = "section_compose"
            st.metadata["a_text_compose"]["last_candidate"] = str(candidate_path.relative_to(base))
            st.metadata["a_text_compose"]["updated_at"] = utc_now_iso()
        # Mark validation pending (script changed) and force audio redo (same semantics as a-text-rebuild).
        if isinstance(st.stages, dict) and "script_validation" in st.stages:
            st.stages["script_validation"].status = "pending"
            try:
                st.stages["script_validation"].details.pop("error", None)
                st.stages["script_validation"].details.pop("issues", None)
                st.stages["script_validation"].details.pop("error_codes", None)
                st.stages["script_validation"].details.pop("fix_hints", None)
            except Exception:
                pass
        st.status = "script_in_progress"
        note = str(st.metadata.get("redo_note") or "").strip()
        msg = "Aテキストをセクション分割で再構成しました (section_compose)"
        if not note:
            st.metadata["redo_note"] = msg
        elif msg not in note:
            st.metadata["redo_note"] = f"{note} / {msg}"
        st.metadata["redo_audio"] = True

        # Stamp (or mark suspect) alignment against Planning SoT so downstream guards are consistent.
        # This is deterministic and does not require running script_review.
        try:
            from factory_common.paths import channels_csv_path
            from packages.script_pipeline.runner import _load_csv_row

            csv_row = _load_csv_row(Path(channels_csv_path(ch)), no)
            if csv_row:
                preview = assembled_text[:6000]
                suspect_reason = alignment_suspect_reason(csv_row, preview)
                if suspect_reason:
                    st.metadata["alignment"] = {
                        "schema": ALIGNMENT_SCHEMA,
                        "computed_at": utc_now_iso(),
                        "suspect": True,
                        "suspect_reason": suspect_reason,
                    }
                else:
                    stamp = build_alignment_stamp(planning_row=csv_row, script_path=canonical_human)
                    st.metadata["alignment"] = stamp.as_dict()
                    pt = str(stamp.planning.get("title") or "").strip()
                    if pt:
                        st.metadata["sheet_title"] = pt
        except Exception:
            pass

        save_status(st)
    except Exception:
        pass

    print(f"Applied canonical A-text: {canonical_human}")

    if args.run_validation:
        from packages.script_pipeline.runner import run_stage

        run_stage(ch, no, "script_validation", title=title)
        print("Ran stage: script_validation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
