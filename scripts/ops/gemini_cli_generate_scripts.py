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


_SENTENCE_END_RE = re.compile(r"[。！？]")


def _tail_cut_by_sentences(text: str, *, sentences: int = 3) -> tuple[str, str, str]:
    """
    Return (prefix, tail, context_end) where tail is the last N sentences.
    We keep all whitespace/newlines between prefix and tail in the prefix so the
    replacement tail can start with content immediately.
    """
    normalized = _normalize_newlines(text)
    ends = [m.end() for m in _SENTENCE_END_RE.finditer(normalized)]
    n = max(1, int(sentences))
    if len(ends) <= n:
        cut = 0
    else:
        cut = int(ends[-(n + 1)])
    # Keep boundary whitespace in prefix.
    while cut < len(normalized) and normalized[cut].isspace():
        cut += 1
    prefix = normalized[:cut]
    tail = normalized[cut:]
    context_end = prefix[-500:] if len(prefix) > 500 else prefix
    return prefix, tail, context_end


_ENDING_POLISH_TRIGGER_MARKERS = (
    "呼吸",
    "深く息",
    "息を吸",
    "息を吐",
    "闇",
    "暗闇",
    "夜の闇",
    "夜の帳",
    "夜の空気",
    "夜が更け",
    "今夜",
    "眠る",
    "寝る",
    "翌日",
    "次の日",
    "翌朝",
    "次の朝",
    "明日の朝",
    "明日",
    "昨日",
    "大切なのは",
    "ポイント",
    "まとめ",
    "コツ",
    "静かな夜",
    "安らぎ",
    "誓",
    "味方",
)


def _tail_cut_for_ending_polish(text: str) -> tuple[str, str, str]:
    """
    Tail cut tuned for "ending polish":
    - default: last 3 sentences
    - if common closing-cliché markers appear near the end, cut from the sentence
      that contains the earliest such marker within a tail window so we can
      replace the whole cliché closing segment at once.
    """
    normalized = _normalize_newlines(text)
    # Default to the last 3 sentences, but expand when the tail is too tiny to rewrite cleanly.
    default_sentences = 3
    default_prefix, default_tail, default_ctx = _tail_cut_by_sentences(normalized, sentences=default_sentences)
    while default_sentences < 6 and _a_text_spoken_char_count(default_tail) < 160:
        default_sentences += 1
        default_prefix, default_tail, default_ctx = _tail_cut_by_sentences(normalized, sentences=default_sentences)
    default_cut = len(default_prefix)

    # Keep the replacement scope tight: we only want to rewrite the closing segment.
    # Too-wide windows can delete too much and risk falling below target_chars_min.
    # The last ~800 chars typically covers the final paragraph while avoiding mid-tail
    # markers that appear earlier as normal narration.
    window_start = max(0, len(normalized) - 800)
    marker_positions: List[int] = []
    for marker in _ENDING_POLISH_TRIGGER_MARKERS:
        # Find the earliest occurrence within the tail window (not the last).
        pos = normalized.find(marker, window_start)
        if pos >= 0:
            marker_positions.append(pos)
    if not marker_positions:
        return default_prefix, default_tail, default_ctx

    marker_pos = min(marker_positions)
    ends = [m.end() for m in _SENTENCE_END_RE.finditer(normalized)]
    cut = 0
    for e in ends:
        if e <= marker_pos:
            cut = e
            continue
        break
    cut = min(default_cut, cut)
    while cut < len(normalized) and normalized[cut].isspace():
        cut += 1
    prefix = normalized[:cut]
    tail = normalized[cut:]
    context_end = prefix[-500:] if len(prefix) > 500 else prefix
    return prefix, tail, context_end


_SYMBOL_STOPWORDS = {
    "私",
    "自分",
    "あなた",
    "彼",
    "彼女",
    "娘",
    "息子",
    "母",
    "父",
    "夫",
    "妻",
    "孫",
    "友達",
    "友人",
    "相手",
    "人",
    "心",
    "気持ち",
    "呼吸",
    "夜",
    "闇",
    "一日",
    "今日",
    "明日",
}


def _extract_symbol_candidates(text: str, *, max_items: int = 8) -> List[str]:
    """
    Heuristic: pick short concrete-looking tokens near the end (objects/places),
    so the model can reuse an already-present symbol item when rewriting the tail.
    """
    snippet = _normalize_newlines(text)[-1800:]
    out: List[str] = []
    for m in re.finditer(r"([一-龠々ぁ-んァ-ヶー]{1,14})(?:を|に|へ|で|と|から|まで|の)", snippet):
        w = str(m.group(1) or "").strip()
        if not w or w in _SYMBOL_STOPWORDS:
            continue
        if len(w) < 1 or len(w) > 10:
            continue
        if any(ch.isdigit() for ch in w):
            continue
        if w not in out:
            out.append(w)
        if len(out) >= int(max_items):
            break
    return out


def _count_sentences(text: str) -> int:
    return len(_SENTENCE_END_RE.findall(str(text or "")))


def _tail_only_retry_hint(last_failure: str) -> str:
    lf = str(last_failure or "")
    if "tail_contains_banned_marker" in lf or "ending_cliche_or_banned_leftover" in lf:
        m = re.search(r"banned=([^\\s]+)", lf)
        banned = f"（禁止語: {m.group(1)}）" if m else ""
        return f"前回: 禁止語/常套句/時間ジャンプが残った{banned}。禁止語は一切出さず、必ず言い換える。"
    if "tail_sentence_count_invalid" in lf:
        return "前回: 文数が範囲外。3〜8文に収め、短い文を分割しすぎない（名詞だけの短文も作らない）。"
    if "tail_not_ending_period" in lf:
        return "前回: 末尾が「。」で終わっていない。最後は必ず「。」で終える。"
    if "tail_too_short_relative" in lf or "tail_too_short" in lf:
        m = re.search(r"min_tail=(\\d+)", lf)
        need = f"（最低{m.group(1)}字以上）" if m else ""
        return f"前回: 末尾が短すぎた{need}。出来事を増やさず、同じ結末のまま具体描写と行動を足して字数を満たす。"
    if "tail_too_long_relative" in lf:
        m = re.search(r"max_tail=(\\d+)", lf)
        limit = f"（最大{m.group(1)}字まで）" if m else ""
        return f"前回: 末尾が長すぎた{limit}。出来事を増やさず、具体描写を残しつつ簡潔に詰める。"
    if "tail_contains_list_or_heading" in lf:
        return "前回: 箇条書き/見出しっぽい形が混じった。地の文だけで書く。"
    return "前回: ルール違反があった。制約を厳守して書き直す。"


def _compact_tail_to_max_sentences(text: str, *, max_sentences: int) -> str:
    """
    Heuristic: when the model over-splits into many short sentences (e.g. 「財布、鍵、ハンカチ。」),
    merge the shortest sentences into the previous one until we fit within max_sentences.
    """
    t = _normalize_newlines(str(text or "")).strip()
    if not t:
        return ""

    parts = re.split(r"([。！？])", t)
    sents: List[str] = []
    for i in range(0, len(parts) - 1, 2):
        frag = (str(parts[i] or "") + str(parts[i + 1] or "")).strip()
        if frag:
            sents.append(frag)
    tail = str(parts[-1] or "").strip()
    if tail:
        sents.append(tail)

    max_n = max(1, int(max_sentences))
    if len(sents) <= max_n:
        return t

    def _score_shortness(x: str) -> int:
        try:
            return int(_a_text_spoken_char_count(x))
        except Exception:
            return len(str(x or ""))

    while len(sents) > max_n and len(sents) >= 2:
        idx = min(range(1, len(sents)), key=lambda i: _score_shortness(sents[i]))
        prev = str(sents[idx - 1] or "").strip()
        cur = str(sents[idx] or "").strip()
        if not prev:
            sents[idx - 1] = cur
            del sents[idx]
            continue
        if not cur:
            del sents[idx]
            continue

        prev_end = prev[-1] if prev else ""
        cur_end = cur[-1] if cur else ""
        if prev_end in "。！？":
            prev_body = prev[:-1]
        else:
            prev_body = prev
        if cur_end in "。！？":
            cur_body = cur[:-1]
            end = cur_end
        else:
            cur_body = cur
            end = "。"

        # A tiny bit of Japanese glue for common verb endings.
        if prev_body.endswith("する"):
            prev_body = prev_body[:-2] + "すると"
        elif prev_body.endswith("した"):
            prev_body = prev_body[:-2] + "すると"
        elif prev_body.endswith("なる"):
            prev_body = prev_body[:-2] + "なると"

        merged = (prev_body.rstrip() + "、" + cur_body.lstrip() + end).strip()
        sents[idx - 1] = merged
        del sents[idx]

    out = "".join([x.strip() for x in sents if str(x or "").strip()]).strip()
    if out and not out.endswith("。"):
        out += "。"
    return out


def _sanitize_tail_only_output(text: str) -> str:
    """
    Last-resort cleanup for tail-only polish when the model echoes banned cliché words.
    This does NOT add new events; it only swaps obvious markers to safer phrasing.
    """
    out = _normalize_newlines(str(text or "")).strip()
    if not out:
        return ""

    # Longer phrases first (avoid partial replacements like 呼吸 inside 深呼吸).
    subs = (
        ("夜の闇", "暗がり"),
        ("夜の帳", "暗がり"),
        ("夜の空気", "空気"),
        ("静かな夜", "静かな時間"),
        ("良い夜", "落ち着いた時間"),
        ("優しい夜", "落ち着いた時間"),
        ("夜が更け", "時間がたち"),
        ("明日の朝", "朝"),
        ("昨日よりも", "前よりも"),
        ("深呼吸", "深く息を吸って"),
        ("呼吸", "息"),
        ("今夜", "その夜"),
        ("昨日", "前"),
        ("味方", "支え"),
        ("のように", ""),
        ("みたいに", ""),
    )
    for src, dst in subs:
        out = out.replace(src, dst)

    out = re.sub(r"誓い", "決めたこと", out)
    out = re.sub(r"誓った", "決めた", out)
    out = re.sub(r"誓う", "決める", out)
    out = re.sub(r"闇", "暗がり", out)
    out = re.sub(r"、、+", "、", out)
    out = re.sub(r"、。", "。", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


_TAIL_ONLY_BANNED_SUBSTRINGS = (
    "---",
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
    "眠り",
    "眠る",
    "眠れ",
    "寝る",
    "翌日",
    "次の日",
    "翌朝",
    "次の朝",
    "明日の朝",
    "昨日",
    "朝食",
    "来週",
    "来月",
    "数日後",
    "数週間後",
    "数ヶ月後",
    "数年後",
    "それから数",
    "ポイント",
    "まとめ",
    "コツ",
    "大切なのは",
    "呼吸",
    "闇",
    "誓",
    "味方",
    "静かな夜",
    "夜の闇",
    "夜の帳",
    "夜の空気",
    "夜の中",
    "夜が更け",
    "今夜",
    "良い夜",
    "優しい夜",
)


# CH04: CTA is required by the operator, but the model tends to leak CTA words mid-body.
# We keep the prompt "CTAなし" and append a fixed CTA after generation.
_CH04_CTA_LINES = (
    "この話が面白かったら、高評価とチャンネル登録で応援してもらえると嬉しいです。",
    "あなたの体験や考えも、コメントで教えてください。",
)
_CH04_CTA_WORDS = ("高評価", "チャンネル登録", "コメント", "シェア")
_CH04_TARGET_CHARS_MAX_EXTRA = 220
_CH04_POSTPROCESS_MIN_SPOKEN_CHARS_FLOOR = 5000
_CH04_CLOSING_TEXT = (
    "ここまでの話は、あなたを責めるためのものではありません。\n"
    "気づいた瞬間に、少しだけ選び直せるようにするための道具です。\n"
    "\n"
    "次に似た場面が来たら、反応を一行だけ残してみてください。\n"
    "何が引っかかったか。\n"
    "体がどう反応したか。\n"
    "一行メモがあると、思い込みの動きが早く見えてきます。\n"
    "見えてきたら、小さく修正できます。\n"
    "今日はそれだけで十分です。"
)
_CH04_CLOSING_PAD = (
    "メモを見返すと、同じ引っかかりが繰り返し出ることがあります。\n"
    "見えてきたら、次の一手を小さく決めて、実行して終える。\n"
    "小さく動かせば、考えは現実に戻りやすくなります。\n"
    "\n"
    "書き方に迷うなら、型を一つだけ決めてください。\n"
    "場所、直前に考えていたこと、体の反応。\n"
    "この三つだけを書いて、最後に次の一手を一つだけ添える。\n"
    "例えば、会議前、嫌な予感、肩が上がった、だから議題を一つだけ確認する、のように短く。\n"
    "一日で三回もやれば十分です。\n"
    "続けるほど、反射の前に一拍置ける場面が増えていきます。"
)
# CH04: Endings sometimes drift into literary/meditative phrasing.
# Keep the replacement trigger focused on tail-only cues to avoid flattening the whole script.
_CH04_TAIL_BAD_TOKENS = (
    "静寂",
    "深淵",
    "変奏曲",
    "海図",
    "暗号",
    "象徴",
    "道標",
    "彩り",
    "彩る",
    "照らし出",
    "導い",
    "寄り添",
    "溶け合",
    "願っています",
    "今夜",
    "毎晩",
    "目覚め",
    "深い休息",
)


def _strip_trailing_pause_lines(text: str) -> str:
    lines = _normalize_newlines(str(text or "")).splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and lines[-1].strip() == "---":
        lines.pop()
        while lines and not lines[-1].strip():
            lines.pop()
    return "\n".join(lines).rstrip()


def _postprocess_ch04_a_text(text: str) -> str:
    out = _normalize_newlines(str(text or "")).rstrip()
    if not out:
        return out

    # Remove any accidental CTA lines/mentions (keep CTA end-only via our append).
    kept: List[str] = []
    for ln in out.split("\n"):
        if any(tok in ln for tok in _CH04_CTA_WORDS):
            continue
        kept.append(ln)
    out = "\n".join(kept)

    # Kill the most common "poetic drift" tokens that users explicitly reject.
    out = out.replace("という名の", "の")
    out = out.replace("芳醇", "豊かな")
    out = out.replace("奇跡", "偶然")
    out = out.replace("運命", "偶然")
    out = out.replace("静寂", "静けさ")
    out = out.replace("暗闇の中", "暗いところ")
    out = out.replace("暗闇", "暗いところ")
    out = out.replace("魔法のような", "特別な")
    out = out.replace("魔法", "不思議")
    out = out.replace("宇宙", "世界")
    out = out.replace("魂", "心")
    out = out.replace("波動", "雰囲気")
    out = out.replace("高次", "別の")
    out = out.replace("カルマ", "癖")

    # Sleep-framing markers must never appear in non-sleep channels.
    # Replace longer phrases first, then the bare token.
    for src, dst in (
        ("眠りにつく前に", "一日の区切りに"),
        ("眠りにつく前", "一日の区切りに"),
        ("眠りに落ちる", "気持ちが落ち着く"),
        ("眠りに就く", "一区切りつく"),
        ("眠りへ誘う", "気持ちを落ち着かせる"),
        ("眠りへ導く", "気持ちを落ち着かせる"),
        ("眠りへ", "休息へ"),
        ("眠りにつく", "一区切りつく"),
        ("寝る前に", "一日の終わりに"),
        ("寝る前", "一日の終わりに"),
        ("おやすみなさい", ""),
        ("ゆっくりお休み", ""),
        ("睡眠用", ""),
        ("寝落ち", ""),
        ("安眠", ""),
        ("就寝", ""),
        ("入眠", ""),
        ("熟睡", ""),
        ("布団", ""),
        ("ベッド", "部屋"),
        ("枕", ""),
        ("寝室", "部屋"),
    ):
        out = out.replace(src, dst)
    out = out.replace("眠り", "休息")

    # Never mention channel/benchmark names in the script body.
    out = out.replace("隠れ書庫アカシック", "").replace("秘密の図書館", "")

    # If any library-motif words slipped in, rewrite them to neutral, audience-friendly phrasing.
    for src, dst in (
        ("心の書庫", "記憶"),
        ("心の図書館", "記憶"),
        ("内側の書庫", "記憶"),
        ("内側の図書館", "記憶"),
        ("書庫", "記憶"),
        ("図書館", "記憶"),
        ("本棚", "記憶"),
        ("ライブラリー", "記憶"),
        ("アーカイブ", "記録"),
        ("司書", "自分"),
        ("索引", "手がかり"),
        ("背表紙", "ラベル"),
        ("しおり", "目印"),
        ("閲覧注意", "注意"),
        ("閲覧室", "部屋"),
        ("閲覧席", "席"),
        ("閲覧", "見る"),
    ):
        out = out.replace(src, dst)

    out = re.sub(r"\n{3,}", "\n\n", out).strip()

    # Drop duplicated paragraphs (often caused by continuation overlaps).
    paras = [p.strip() for p in re.split(r"\n[ \t\u3000]*\n+", out) if str(p or "").strip()]
    deduped: List[str] = []
    seen: set[str] = set()
    for p in paras:
        if p.strip() == "---":
            deduped.append("---")
            continue
        key = _sha1_text(re.sub(r"\s+", "", p))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)
    out = "\n\n".join(deduped).strip()

    out = _strip_trailing_pause_lines(out).strip()
    if not out:
        return out

    # CH04 endings tend to drift into abstract/literary monologues.
    # If the final block is too long (or obviously poetic), replace it with a compact closing.
    lines = out.splitlines()
    sep_positions = [i for i, ln in enumerate(lines) if ln.strip() == "---"]
    if sep_positions:
        cut = sep_positions[-1]
        tail = "\n".join(lines[cut + 1 :]).strip()
        # CH04: Replace the ending only for clearly "poetic drift" markers.
        # Keep this list narrow to avoid flattening good, topic-specific endings.
        comma_splice = tail.count("です、") >= 3 or tail.count("ます、") >= 3 or "なりますると" in tail
        tail_bad = any(tok in tail for tok in _CH04_TAIL_BAD_TOKENS) or any(tok in tail for tok in ("灯火", "土壌", "羽ばた"))
        if tail and (tail_bad or comma_splice):
            candidate = ("\n".join(lines[: cut + 1]).rstrip() + "\n\n" + _CH04_CLOSING_TEXT).strip()
            candidate_with_cta = (
                candidate.rstrip() + "\n\n" + _CH04_CTA_LINES[0] + "\n" + _CH04_CTA_LINES[1] + "\n"
            )
            # If replacing the ending would make the script too short (and thus fail validation),
            # append a short, concrete padding block (still CTA-free) to reach the safety floor.
            if _a_text_spoken_char_count(candidate_with_cta) < int(_CH04_POSTPROCESS_MIN_SPOKEN_CHARS_FLOOR):
                candidate = (candidate.rstrip() + "\n\n" + _CH04_CLOSING_PAD).strip()
            out = candidate

    out = out.rstrip() + "\n\n" + _CH04_CTA_LINES[0] + "\n" + _CH04_CTA_LINES[1] + "\n"
    return out


def _build_tail_only_prompt(
    *,
    channel: str,
    video: str,
    context_end: str,
    old_tail: str,
    min_required_tail_chars: int,
    max_allowed_tail_chars: int,
    symbol_candidates: List[str],
    operator_instruction: str | None,
    attempt: int,
) -> str:
    """
    Build a small prompt that asks Gemini to output ONLY the replacement tail.
    """
    header = (
        "あなたはYouTube向けの物語台本の編集者です。\n"
        "目的: 既存台本の末尾だけを編集して、抽象で締めがちな癖を減らし、具体で綺麗に完結させます。\n"
        "重要: 新しい出来事/人物/場所/設定を追加しない。内容の因果と結末は維持。\n"
    )
    constraints = (
        "【出力】\n"
        "- 出力は「新しい末尾」だけ。前置き/注釈/見出し/箇条書きは禁止。\n"
        "- 3〜8文。文末は必ず「。」で終える。\n"
        "- 文を切りすぎない。名詞だけの短い文（例: 財布、鍵、ハンカチ。）を連打しない。\n"
        "- 分量: 現行の末尾と同程度の分量（短くしすぎない）。\n"
        "- 最後は『具体行動1つ + 既出の象徴アイテム1つ』で閉じる。\n"
        "- 禁止: 呼吸/闇/誓い/自分の味方 など抽象ワードで締めること。『静かな夜』『夜の闇』『夜の空気』『夜の帳』『夜が更け』『今夜』等の常套句で締めない。\n"
        "- 禁止: おやすみ/寝落ち/睡眠用/安眠/布団/ベッド/枕/寝室/就寝/入眠/熟睡/眠り/眠る/眠れ/寝る 等。\n"
        "- 禁止: 翌日/次の日/翌朝/次の朝/明日の朝/昨日/朝食/来週/来月/数日後/数週間後/数ヶ月後/数年後/それから数… など時間ジャンプ。\n"
        "- 禁止: まとめ/ポイント/コツ/大切なのは/次に/最後に 等の手順口調。\n"
        "- 禁止: 会話の引用符「」を新たに増やす（末尾は地の文で閉じる）。\n"
        "- 末尾を二重にしない（同じ余韻を繰り返さない）。\n"
        "- 文体: 自然で平易。文数は3〜8文で収める（8文を超えない）。字数が足りない場合は文を増やしすぎず、1文に具体描写を入れて調整する（長すぎる一文は避ける）。難しい比喩/気取った言い回し/抽象名詞の連打は避け、分かる言葉で。\n"
    )
    old_tail_chars = _a_text_spoken_char_count(str(old_tail or ""))
    min_required = max(0, int(min_required_tail_chars or 0))
    max_allowed = max(0, int(max_allowed_tail_chars or 0))
    length_hint = ""
    if old_tail_chars > 0:
        base_lo = max(80, int(old_tail_chars) - 30)
        lo = min(150, int(base_lo))
        hi = int(old_tail_chars) + 150
        if min_required > 0:
            lo = max(int(lo), int(min_required))
            hi = max(int(hi), int(lo) + 150)
        if max_allowed > 0:
            hi = min(int(hi), int(max_allowed))
            lo = min(int(lo), int(hi))
            length_hint = (
                f"- 字数条件（必須）: 現行末尾は約{old_tail_chars}字（空白除外）。新しい末尾は最低{lo}字以上、最大{hi}字まで。目安は{old_tail_chars}字前後。\n"
            )
        else:
            length_hint = (
                f"- 字数条件（必須）: 現行末尾は約{old_tail_chars}字（空白除外）。新しい末尾は最低{lo}字以上、目安は{old_tail_chars}字前後（±150字）。\n"
            )
    elif min_required > 0:
        lo = max(80, int(min_required))
        if max_allowed > 0:
            lo = min(int(lo), int(max_allowed))
            length_hint = (
                f"- 字数条件（必須）: 新しい末尾は最低{lo}字以上、最大{int(max_allowed)}字まで（空白除外）。\n"
            )
        else:
            length_hint = f"- 字数条件（必須）: 新しい末尾は最低{lo}字以上（空白除外、短くしすぎない）。\n"
    candidates = ""
    if symbol_candidates:
        candidates = "【象徴アイテム候補（既出から1つだけ選ぶ）】\n" + " / ".join(symbol_candidates[:8]) + "\n"
    prompt_parts: List[str] = [
        header,
        f"channel: {channel}\nvideo: {video}\nretry_attempt: {attempt}\n",
        constraints + (length_hint or ""),
    ]
    if operator_instruction:
        prompt_parts.append("【追加指示】\n" + str(operator_instruction).strip() + "\n")
    if candidates:
        prompt_parts.append(candidates)
    prompt_parts.append("【直前の文脈（末尾に接続する直前）】\n" + str(context_end or "").rstrip() + "\n")
    prompt_parts.append("【現行の末尾（置き換える）】\n" + str(old_tail or "").rstrip() + "\n")
    prompt_parts.append("【新しい末尾】\n")
    return "\n".join([p for p in prompt_parts if str(p).strip()]).strip() + "\n"


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
        "重要: この台本は睡眠用ではない。睡眠導入/寝落ち誘導の呼びかけ・使い方の提示は禁止。"
        "本文と末尾に「寝落ち」「睡眠用」「安眠」「就寝」「入眠」「熟睡」「布団」「ベッド」「枕」「寝室」「寝る」「眠り」"
        "「おやすみ」「ゆっくりお休み」等の語（派生/言い換え含む）を出さない。"
        "末尾は内容として完結させる。"
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
        "追加分で `---` を出さない（セクション区切りを増やさない）。"
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
        # Avoid huge continuation jumps that often cause duplication and too many pause markers.
        need_max = min(int(need_max), int(need_min) + 1800, 3200)

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
        if rc != 0 and _is_gemini_capacity_exhausted(stderr):
            qwen_bin = _find_qwen_bin()
            qwen_approval_mode: str | None = approval_mode
            if qwen_approval_mode == "auto_edit":
                qwen_approval_mode = "auto-edit"
            qrc, qout, qerr, _qelapsed = _run_qwen_cli(
                qwen_bin=qwen_bin,
                prompt=cont_prompt,
                sandbox=True,
                approval_mode=qwen_approval_mode,
                timeout_sec=timeout_sec,
            )
            stdout = qout
            stderr = (str(stderr or "").rstrip() + "\n\n[fallback:qwen]\n" + str(qerr or "").lstrip()).strip() + "\n"
            rc = int(qrc)
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
        # Continuation must not add extra pause markers; remove any that slip in.
        cleaned = "\n".join([ln for ln in cleaned.split("\n") if ln.strip() != "---"]).strip() + "\n"
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        cleaned_compact = cleaned.strip()
        # If the model repeats a large chunk verbatim, do not append; try the next continuation round.
        if len(cleaned_compact) >= 200 and cleaned_compact in combined:
            continue

        combined = combined.rstrip() + "\n\n" + cleaned
        combined = combined.rstrip() + "\n"

    if _a_text_spoken_char_count(combined) >= int(min_spoken_chars):
        return combined, None
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
                },
                # Gemini 3 Flash (user request) with a slightly lower temperature to avoid poetic drift.
                "antigravity-script-g3": {
                    "extends": "base",
                    "modelConfig": {
                        "model": "gemini-3-flash-preview",
                        "generateContentConfig": {
                            "temperature": 0.6,
                            "topP": 0.9,
                            "topK": 40,
                            "maxOutputTokens": 24000,
                            "thinkingConfig": {"thinkingBudget": 0},
                        },
                    },
                },
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

    # Defensive: our scratch dir may be pruned by external cleanup between episodes.
    # Ensure it exists right before spawning the child process.
    _ensure_dir(_scratch_dir())

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


_GEMINI_CAPACITY_EXHAUSTED_MARKERS = (
    "No capacity available for model",
    "MODEL_CAPACITY_EXHAUSTED",
    "RESOURCE_EXHAUSTED",
    "TerminalQuotaError",
    "Quota exceeded",
    "You have exhausted your daily quota",
    "You exceeded your current quota",
    "code: 429",
)


def _is_gemini_capacity_exhausted(stderr: str) -> bool:
    blob = str(stderr or "")
    return any(tok in blob for tok in _GEMINI_CAPACITY_EXHAUSTED_MARKERS)


def _find_qwen_bin() -> str:
    shim = repo_paths.repo_root() / "scripts" / "bin" / "qwen"
    if shim.exists() and os.access(shim, os.X_OK):
        return str(shim)

    for p in str(os.environ.get("PATH") or "").split(os.pathsep):
        if not p:
            continue
        cand = Path(p) / "qwen"
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    raise SystemExit("qwen CLI not found. Install `qwen` and ensure it is on PATH (or use scripts/bin/qwen).")


def _run_qwen_cli(
    *,
    qwen_bin: str,
    prompt: str,
    sandbox: bool,
    approval_mode: str | None,
    timeout_sec: int,
) -> tuple[int, str, str, float]:
    # qwen_bin is the repo shim; auth-type/model/provider switching is enforced there.
    cmd: List[str] = [qwen_bin, "--output-format", "text"]
    if sandbox:
        cmd.append("--sandbox")
    if approval_mode:
        cmd += ["--approval-mode", str(approval_mode)]

    env = dict(os.environ)
    env.setdefault("NO_COLOR", "1")

    # Defensive: our scratch dir may be pruned by external cleanup between episodes.
    _ensure_dir(_scratch_dir())

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

    tail_only = bool(getattr(args, "tail_only", False))
    if tail_only and not bool(args.include_current):
        raise SystemExit("--tail-only requires --include-current (only replace the ending; keep body as-is).")
    if tail_only and section_splits:
        raise SystemExit("--tail-only cannot be used with --split-sections.")

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
        if tail_only and not current:
            failures.append(f"{script_id}: tail_only_missing_current_a_text (need assembled_human.md)")
            continue
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
            max_continue_rounds = 0 if tail_only else int(getattr(args, "max_continue_rounds", 0) or 0)

            success = False
            last_failure: Optional[str] = None

            tail_prefix = ""
            tail_old = ""
            tail_context_end = ""
            tail_symbols: List[str] = []
            tail_prefix_chars = 0
            tail_min_required = 0
            tail_max_allowed = 0
            if tail_only and current:
                tail_prefix, tail_old, tail_context_end = _tail_cut_for_ending_polish(current)
                tail_symbols = _extract_symbol_candidates(current)
                tail_prefix_chars = _a_text_spoken_char_count(tail_prefix)
                if not str(tail_old).strip():
                    failures.append(f"{script_id}: tail_only_empty_tail (cannot cut ending)")
                    continue
                if min_spoken_chars > 0 and tail_prefix_chars > 0:
                    tail_min_required = max(0, int(min_spoken_chars) - int(tail_prefix_chars))
                if detected_max is not None and tail_prefix_chars > 0:
                    tail_max_allowed = max(0, int(detected_max) - int(tail_prefix_chars))

            for attempt in range(1, max_attempts + 1):
                retry_hint = ""
                if attempt > 1:
                    retry_hint = (
                        "再試行: 直前の出力が不合格。"
                        "本文のみを出力し、ルール説明/見出し/箇条書き/番号リスト/マーカー文字列/段落重複を絶対に出さない。"
                    )
                    if (
                        tail_only
                        and tail_min_required > 0
                        and last_failure
                        and ("tail_too_short_relative" in last_failure or "rejected_output=too_short" in last_failure)
                    ):
                        length_hint = (
                            f"字数が足りない。新しい末尾は最低{tail_min_required}字以上（空白除外）。"
                            "文数は3〜8文のまま、1文に具体描写を足して増やす。"
                        )
                        if int(tail_min_required) >= 420:
                            length_hint += "目安: 7〜8文にし、各文は60〜90字程度で書く。"
                        retry_hint = retry_hint + "\n" + length_hint
                    if tail_only and last_failure:
                        retry_hint = (retry_hint + "\n" + _tail_only_retry_hint(last_failure)).strip()
                attempt_instruction = instruction
                if retry_hint:
                    attempt_instruction = (attempt_instruction + "\n\n" + retry_hint).strip() if attempt_instruction else retry_hint
                if attempt_instruction:
                    attempt_instruction = (attempt_instruction + f"\nretry_attempt: {attempt}").strip()

                if tail_only:
                    attempt_prompt = _build_tail_only_prompt(
                        channel=channel,
                        video=vv,
                        context_end=tail_context_end,
                        old_tail=tail_old,
                        min_required_tail_chars=tail_min_required,
                        max_allowed_tail_chars=tail_max_allowed,
                        symbol_candidates=tail_symbols,
                        operator_instruction=attempt_instruction if attempt_instruction else None,
                        attempt=attempt,
                    )
                else:
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

                fallback: Dict[str, Any] | None = None
                # Tail-only polish should be robust even when gemini is rate-limited or quota-exhausted:
                # fall back to qwen for ANY gemini non-zero exit in tail-only mode.
                if rc != 0 and (tail_only or _is_gemini_capacity_exhausted(stderr)):
                    qwen_bin = _find_qwen_bin()
                    qwen_approval_mode: str | None = None
                    if args.gemini_approval_mode:
                        qwen_approval_mode = str(args.gemini_approval_mode).strip()
                        if qwen_approval_mode == "auto_edit":
                            qwen_approval_mode = "auto-edit"

                    qrc, qout, qerr, qelapsed = _run_qwen_cli(
                        qwen_bin=qwen_bin,
                        prompt=attempt_prompt,
                        sandbox=True,
                        approval_mode=qwen_approval_mode,
                        timeout_sec=int(args.timeout_sec),
                    )
                    fallback = {
                        "provider": "qwen",
                        "qwen_bin": qwen_bin,
                        "qwen_sandbox": True,
                        "qwen_approval_mode": qwen_approval_mode or "",
                        "elapsed_sec": qelapsed,
                        "exit_code": qrc,
                    }
                    stdout = qout
                    stderr = (str(stderr or "").rstrip() + "\n\n[fallback:qwen]\n" + str(qerr or "").lstrip()).strip() + "\n"
                    elapsed = float(elapsed) + float(qelapsed)
                    rc = int(qrc)

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
                        "fallback": fallback or {},
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
                        "fallback": fallback or {},
                    },
                )

                if rc != 0:
                    last_failure = f"{script_id}: gemini_exit={rc} (see {attempt_stderr_log})"
                    continue

                if tail_only:
                    tail_out = _normalize_newlines(stdout).strip()
                    tail_out = re.sub(r"^【[^\\n]+】\\s*", "", tail_out).strip()
                    if _reject_obviously_non_script(tail_out):
                        last_failure = f"{script_id}: rejected_output=empty_tail (see {attempt_stdout_log})"
                        continue
                    banned_hits = [tok for tok in _TAIL_ONLY_BANNED_SUBSTRINGS if tok in tail_out]
                    if banned_hits:
                        sanitized = _sanitize_tail_only_output(tail_out)
                        if sanitized and sanitized != tail_out:
                            leftover = [tok for tok in _TAIL_ONLY_BANNED_SUBSTRINGS if tok in sanitized]
                            if not leftover:
                                tail_out = sanitized
                            else:
                                last_failure = (
                                    f"{script_id}: rejected_output=tail_contains_banned_marker banned={','.join(leftover[:8])} "
                                    f"(see {attempt_stdout_log})"
                                )
                                continue
                        else:
                            last_failure = (
                                f"{script_id}: rejected_output=tail_contains_banned_marker banned={','.join(banned_hits[:8])} "
                                f"(see {attempt_stdout_log})"
                            )
                            continue
                    if re.search(r"(?m)^\\s*(#|[-*]\\s|・)", tail_out):
                        last_failure = f"{script_id}: rejected_output=tail_contains_list_or_heading (see {attempt_stdout_log})"
                        continue
                    sentence_count = _count_sentences(tail_out)
                    if sentence_count > 8:
                        tail_out = _compact_tail_to_max_sentences(tail_out, max_sentences=8)
                        sentence_count = _count_sentences(tail_out)
                    if sentence_count < 3 or sentence_count > 8:
                        last_failure = f"{script_id}: rejected_output=tail_sentence_count_invalid (see {attempt_stdout_log})"
                        continue
                    if not tail_out.endswith("。"):
                        last_failure = f"{script_id}: rejected_output=tail_not_ending_period (see {attempt_stdout_log})"
                        continue
                    old_tail_chars = _a_text_spoken_char_count(tail_old)
                    new_tail_chars = _a_text_spoken_char_count(tail_out)
                    if old_tail_chars > 0:
                        # Keep overall script length >= target_chars_min (min_spoken_chars).
                        need_tail = 0
                        if min_spoken_chars > 0 and tail_prefix_chars > 0:
                            need_tail = max(0, int(min_spoken_chars) - int(tail_prefix_chars))
                        # Allow tightening a bloated ending. Use a dynamic minimum based on the
                        # current tail length, while still ensuring the merged script stays above
                        # the channel's minimum length.
                        min_tail_base = min(150, max(80, int(old_tail_chars) - 30))
                        min_tail = max(int(min_tail_base), int(need_tail))
                        if new_tail_chars < min_tail:
                            last_failure = (
                                f"{script_id}: rejected_output=tail_too_short_relative "
                                f"min_tail={min_tail} new_tail={new_tail_chars} "
                                f"(see {attempt_stdout_log})"
                            )
                            continue
                        if tail_max_allowed > 0 and new_tail_chars > tail_max_allowed:
                            last_failure = (
                                f"{script_id}: rejected_output=tail_too_long_relative "
                                f"max_tail={tail_max_allowed} new_tail={new_tail_chars} "
                                f"(see {attempt_stdout_log})"
                            )
                            continue
                    a_text = (tail_prefix + tail_out.lstrip()).rstrip() + "\n"
                    # Only guard the very end: we don't want to reject body text that
                    # legitimately mentions e.g. 「深呼吸」 earlier. Our goal here is
                    # to avoid cliché closings in the final segment.
                    tail_window = a_text[-500:] if len(a_text) > 500 else a_text
                    leftover_hits = [tok for tok in _TAIL_ONLY_BANNED_SUBSTRINGS if tok in tail_window]
                    if leftover_hits:
                        last_failure = (
                            f"{script_id}: rejected_output=ending_cliche_or_banned_leftover banned={','.join(leftover_hits[:8])} "
                            f"(see {attempt_stdout_log})"
                        )
                        continue
                    _write_text(stdout_log, a_text)
                else:
                    a_text = _normalize_newlines(stdout).rstrip() + "\n"
                    _write_text(stdout_log, a_text)
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

                # CH04: keep the body CTA-free (post-process adds a fixed CTA),
                # and normalize a few explicitly banned phrases.
                if str(args.channel or "").strip().upper() == "CH04":
                    a_text = _postprocess_ch04_a_text(a_text)
                    if not a_text.strip():
                        last_failure = f"{script_id}: rejected_output=empty_after_postprocess (see {attempt_stdout_log})"
                        continue
                    _write_text(stdout_log, a_text)

                if sleep_guard_enabled:
                    issue = _sleep_framing_issue(a_text=a_text, assembled_path=mirror_path)
                    if issue:
                        marker_msg = str(issue.get("message") or "").strip()
                        suffix = f" {marker_msg}" if marker_msg else ""
                        last_failure = (
                            f"{script_id}: rejected_output=sleep_framing_contamination{suffix} (see {attempt_stdout_log})"
                        )
                        continue

                validator_md: Dict[str, Any] = {"assembled_path": str(mirror_path)}
                ch = str(args.channel or "").strip().upper()
                if ch:
                    validator_md["channel"] = ch
                    validator_md["channel_code"] = ch
                if detected_min is not None:
                    validator_md["target_chars_min"] = int(detected_min)
                if detected_max is not None:
                    max_allowed = int(detected_max)
                    if ch == "CH04":
                        max_allowed += int(_CH04_TARGET_CHARS_MAX_EXTRA)
                    validator_md["target_chars_max"] = max_allowed
                issues, _stats = validate_a_text(a_text, validator_md)
                hard_errors = [it for it in issues if isinstance(it, dict) and str(it.get("severity") or "") == "error"]
                if not sleep_guard_enabled:
                    hard_errors = [
                        it
                        for it in hard_errors
                        if str(it.get("code") or "") != "sleep_framing_contamination"
                    ]
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

        # CH04: keep the body CTA-free (post-process adds a fixed CTA),
        # and normalize a few explicitly banned phrases.
        if str(args.channel or "").strip().upper() == "CH04":
            a_text = _postprocess_ch04_a_text(a_text)
            if not a_text.strip():
                failures.append(f"{script_id}: rejected_output=empty_after_postprocess (see {stdout_log})")
                continue
            _write_text(stdout_log, a_text)

        if sleep_guard_enabled:
            issue = _sleep_framing_issue(a_text=a_text, assembled_path=mirror_path)
            if issue:
                marker_msg = str(issue.get("message") or "").strip()
                suffix = f" {marker_msg}" if marker_msg else ""
                failures.append(f"{script_id}: rejected_output=sleep_framing_contamination{suffix} (see {stdout_log})")
                continue

        # Deterministic SSOT validation (no LLM): reject if any hard errors remain.
        validator_md: Dict[str, Any] = {"assembled_path": str(mirror_path)}
        ch = str(args.channel or "").strip().upper()
        if ch:
            validator_md["channel"] = ch
            validator_md["channel_code"] = ch
        if detected_min is not None:
            validator_md["target_chars_min"] = int(detected_min)
        if detected_max is not None:
            max_allowed = int(detected_max)
            if ch == "CH04":
                max_allowed += int(_CH04_TARGET_CHARS_MAX_EXTRA)
            validator_md["target_chars_max"] = max_allowed
        issues, _stats = validate_a_text(a_text, validator_md)
        hard_errors = [it for it in issues if isinstance(it, dict) and str(it.get("severity") or "") == "error"]
        if not sleep_guard_enabled:
            hard_errors = [it for it in hard_errors if str(it.get("code") or "") != "sleep_framing_contamination"]
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
    sp.add_argument(
        "--tail-only",
        dest="tail_only",
        action="store_true",
        help="Polish ONLY the ending (last 2-4 sentences) while keeping the body untouched (requires --include-current)",
    )
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
