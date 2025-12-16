#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


REPO_ROOT = _discover_repo_root(Path(__file__).resolve())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_common.llm_router import get_router  # noqa: E402
from factory_common.paths import repo_root, script_data_root, video_root  # noqa: E402
from factory_common.text_sanitizer import strip_meta_from_script  # noqa: E402


try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


_RE_URL = re.compile(r"https?://\S+|\bwww\.[^\s)）\]】」』<>]+")
_RE_FOOTNOTE_ANY = re.compile(r"\[(\d+)\]")
_RE_BULLET = re.compile(r"^\s*(?:[-*+]\s+|・\s*|\d+[.)]\s+|\d+）\s+)")
_RE_OTHER_SEP = re.compile(r"^\s*(?:\*{3,}|_{3,}|/{3,}|={3,})\s*$")
_RE_HYPHEN_ONLY = re.compile(r"^\s*[-\s]+\s*$")
_RE_HEADING = re.compile(r"^\s*#{1,6}\s+")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _norm_channel(value: str) -> str:
    ch = (value or "").strip().upper()
    if not ch:
        raise SystemExit("channel is required (e.g. CH07)")
    return ch


def _norm_video(value: str) -> str:
    token = (value or "").strip()
    if not token:
        raise SystemExit("video is required (e.g. 028)")
    digits = "".join(ch for ch in token if ch.isdigit())
    if not digits:
        raise SystemExit(f"invalid video: {value}")
    return f"{int(digits):03d}"


def _load_sources() -> Dict[str, Any]:
    if yaml is None:
        return {}
    path = repo_root() / "configs" / "sources.yaml"
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _channel_cfg(channel: str, sources: Dict[str, Any]) -> Dict[str, Any]:
    channels_cfg = sources.get("channels")
    if not isinstance(channels_cfg, dict):
        return {}
    cfg = channels_cfg.get(channel)
    return cfg if isinstance(cfg, dict) else {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _canonical_a_text_path(base: Path) -> Path:
    human = base / "content" / "assembled_human.md"
    return human if human.exists() else (base / "content" / "assembled.md")


def _backup_file(path: Path, backup_root: Path) -> None:
    if not path.exists():
        return
    rel = path.resolve().relative_to(repo_root())
    dst = backup_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(_read_text(path), encoding="utf-8")


def _chars_no_newlines(text: str) -> int:
    return len((text or "").replace("\n", ""))


def _quote_paren_counts(text: str) -> Tuple[int, int]:
    src = text or ""
    quotes = src.count("「") + src.count("」")
    parens = src.count("（") + src.count("）") + src.count("(") + src.count(")")
    return quotes, parens


def _strip_tts_punct(text: str) -> str:
    """
    TTS safety: remove quote/paren punctuation while keeping inner content.
    This helps prevent unnatural pauses and stabilizes style constraints.
    """
    if not text:
        return ""
    out = text
    for ch in ("「", "」", "（", "）", "(", ")"):
        out = out.replace(ch, "")
    return out


def _strip_list_prefixes(text: str) -> str:
    """
    TTS safety: remove list/bullet prefixes if a model accidentally outputs them.
    Keeps content, drops the leading marker like '-', '・', or '1.'.
    """
    if not text:
        return ""
    out_lines: List[str] = []
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.lstrip()
        if stripped and _RE_BULLET.match(stripped):
            stripped = _RE_BULLET.sub("", stripped, count=1).lstrip()
            out_lines.append(stripped)
        else:
            out_lines.append(line)
    return "\n".join(out_lines).strip() + "\n"


def _strip_heading_lines(text: str) -> str:
    if not text:
        return ""
    out_lines: List[str] = []
    for raw in text.splitlines():
        if _RE_HEADING.match(raw.strip()):
            continue
        out_lines.append(raw.rstrip())
    return "\n".join(out_lines).strip() + "\n"


def _target_min_chars(*, min_chars: int, max_chars: Optional[int]) -> int:
    """
    Ask the model to aim slightly above min_chars so we reliably clear the floor
    after sanitization and minor variability, while respecting max_chars if set.
    """
    buffer_chars = 180
    if max_chars is None:
        return min_chars + buffer_chars
    safety_margin = 60
    upper = max_chars - safety_margin
    if upper <= min_chars:
        return min_chars
    return min(min_chars + buffer_chars, upper)


@dataclass(frozen=True)
class Validation:
    ok: bool
    reasons: List[str]


def _validate_a_text(
    text: str,
    *,
    min_chars: int,
    max_chars: Optional[int],
    max_quotes: Optional[int],
    max_parens: Optional[int],
) -> Validation:
    reasons: List[str] = []
    n = _chars_no_newlines(text)
    if n < min_chars:
        reasons.append(f"too short: {n} < {min_chars}")
    if max_chars is not None and n > max_chars:
        reasons.append(f"too long: {n} > {max_chars}")
    # Optional style constraints (TTS safety)
    quotes, parens = _quote_paren_counts(text)
    if max_quotes is not None and quotes > max_quotes:
        reasons.append(f"too many quote marks: {quotes} > {max_quotes}")
    if max_parens is not None and parens > max_parens:
        reasons.append(f"too many parentheses marks: {parens} > {max_parens}")
    if _RE_URL.search(text):
        reasons.append("contains URL")
    if _RE_FOOTNOTE_ANY.search(text):
        reasons.append("contains [number] tokens")
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if _RE_HEADING.match(stripped):
            reasons.append("contains heading lines")
            break
        if _RE_BULLET.match(stripped):
            reasons.append("contains bullet/list lines")
            break
        if _RE_OTHER_SEP.match(stripped):
            reasons.append("contains forbidden separators (*** / ___ / /// / ===)")
            break
        if _RE_HYPHEN_ONLY.match(stripped):
            compact = re.sub(r"\s+", "", stripped)
            if compact != "---":
                reasons.append("contains invalid hyphen separator (only '---' allowed)")
                break
    return Validation(ok=not reasons, reasons=reasons)


def _build_messages(
    *,
    channel: str,
    video: str,
    min_chars: int,
    target_min_chars: int,
    max_chars: Optional[int],
    global_rules: str,
    persona: str,
    channel_prompt: str,
    planning_hint: str,
    current_text: str,
    current_len: int,
    attempt: int,
    action: str,
    delta_hint: str,
) -> List[Dict[str, str]]:
    length_rule = f"最低{min_chars}字以上"
    if max_chars is not None:
        length_rule += f"、最大{max_chars}字以下"
    if target_min_chars > min_chars:
        length_rule += f"（目安: {target_min_chars}字以上）"

    system = (
        "あなたは日本語のYouTubeナレーション台本の編集者です。"
        "出力は台本本文のみ。説明・箇条書き・見出し・メタ情報は一切出力しません。"
        "視聴者がストレスなく聴ける自然さと、冗長さの排除を最優先します。"
        "文字数要件を満たすまで出力を確定しません。"
    )

    if action == "expand":
        action_line = "拡張して厚みを足す"
    elif action == "shrink":
        action_line = "削って引き締める"
    else:
        action_line = "表現を整えて読みやすくする"
    user_sections = [
        f"チャンネル: {channel}",
        f"動画: {channel}-{video}",
        f"要件: {length_rule}（改行は文字数に数えない）",
        f"現在の文字数（改行除外）: {current_len}",
        f"今回の操作: {action_line}",
        delta_hint.strip(),
        "",
        "絶対ルール（破ったら失敗）",
        "- ポーズ挿入は `---` のみ（1行単独）。他の区切り記号は使わない。",
        "- URL、脚注、参照番号、出典メタ、タイムスタンプは禁止。",
        "- 箇条書き/番号リスト/見出しは使わない。",
        "- `「」` は原則使わない。必要でも短く、全体で多くても10組程度まで。直接話法より間接話法を優先する。",
        "- `（）` は原則使わない。必要な場合も短く、連発しない。",
        "- 同じ言い回しの連発、薄い言い換えでの水増し、蛇足の比喩は禁止。",
        "",
        "重要（重複回避）",
        "- 既存台本に含まれる主要エピソード/仏教逸話/核心メッセージは置き換えない。核は維持し、深掘りと具体化で厚みを作る。",
        "- 新しい“有名な一話”を無理に追加して別回と同じにならないようにする。必要なら日常の具体例（人物/場面/行動）で増やす。",
        "- 直接話法（`「」`）は意味を変えずに間接話法へ言い換えてよい（TTSが途切れない形を優先）。",
        "",
        "SSOT: 全チャンネル共通Aテキストルール",
        (global_rules or "").strip(),
        "",
        "チャンネル固有プロンプト（方針）",
        (channel_prompt or "").strip(),
        "",
        "ペルソナ",
        (persona or "").strip(),
        "",
        "企画ヒント（あれば）",
        (planning_hint or "").strip(),
        "",
        "編集タスク",
        "- 既存台本の核は維持しつつ、具体例・場面・行動の描写で厚みを作る。",
        "- `---` は文脈の切れ目にだけ追加/調整する（機械的な等間隔は禁止）。",
        "- 出力は最終版の台本本文だけ（説明・メモ禁止）。",
        "",
        "現在の台本（これを拡張）",
        (current_text or "").strip(),
        "",
        f"(attempt {attempt})",
        "",
    ]
    user = "\n".join(user_sections).strip() + "\n"

    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _build_append_messages(
    *,
    channel: str,
    video: str,
    min_chars: int,
    target_min_chars: int,
    max_chars: Optional[int],
    global_rules: str,
    persona: str,
    channel_prompt: str,
    planning_hint: str,
    current_text: str,
    current_len: int,
    attempt: int,
    need_chars: int,
) -> List[Dict[str, str]]:
    length_rule = f"最低{min_chars}字以上"
    if max_chars is not None:
        length_rule += f"、最大{max_chars}字以下"
    if target_min_chars > min_chars:
        length_rule += f"（目安: {target_min_chars}字以上）"

    system = (
        "あなたは日本語のYouTubeナレーション台本の編集者です。"
        "出力は追記する台本本文のみ。説明・箇条書き・見出し・メタ情報は一切出力しません。"
        "視聴者がストレスなく聴ける自然さと、冗長さの排除を最優先します。"
        "文字数要件を満たすために必要な分だけ追記します。"
    )

    user_sections = [
        f"チャンネル: {channel}",
        f"動画: {channel}-{video}",
        f"要件: {length_rule}（改行は文字数に数えない）",
        f"現在の文字数（改行除外）: {current_len}",
        f"不足: あと{need_chars}字以上（目安まで到達させる）",
        "",
        "絶対ルール（破ったら失敗）",
        "- ポーズ挿入は `---` のみ（1行単独）。他の区切り記号は使わない。",
        "- URL、脚注、参照番号、出典メタ、タイムスタンプは禁止。",
        "- 箇条書き/番号リスト/見出しは使わない。",
        "- `「」` は原則使わない。必要でも短く、全体で多くても10組程度まで。直接話法より間接話法を優先する。",
        "- `（）` は原則使わない。必要な場合も短く、連発しない。",
        "- 同じ言い回しの連発、薄い言い換えでの水増し、蛇足の比喩は禁止。",
        "",
        "追記の方針",
        "- 既存台本の核（主要エピソード/核心メッセージ）は置き換えない。追記は“深掘り”と“具体化”だけ。",
        "- 新しい有名逸話を無理に足さない。必要なら日常の場面・行動例で厚みを作る。",
        "- 直接話法（`「」`）は意味を変えずに間接話法へ言い換えてよい（TTSが途切れない形を優先）。",
        "- `---` は文脈の切れ目にだけ追加する（機械的な等間隔は禁止）。",
        "- 追記は自然に最後へつながるように書く。既存の文を繰り返さない。",
        "",
        "SSOT: 全チャンネル共通Aテキストルール",
        (global_rules or "").strip(),
        "",
        "チャンネル固有プロンプト（方針）",
        (channel_prompt or "").strip(),
        "",
        "ペルソナ",
        (persona or "").strip(),
        "",
        "企画ヒント（あれば）",
        (planning_hint or "").strip(),
        "",
        "現在の台本（この最後に追記する）",
        (current_text or "").strip(),
        "",
        "出力指示",
        "- 追記する本文だけを出力する。既存本文の再掲は禁止。",
        "",
        f"(append attempt {attempt})",
        "",
    ]
    user = "\n".join(user_sections).strip() + "\n"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def main() -> int:
    ap = argparse.ArgumentParser(description="Expand A-text to a minimum character count using LLMRouter")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--min-chars", type=int, help="Override minimum chars (excluding newlines)")
    ap.add_argument("--max-chars", type=int, help="Optional maximum chars (excluding newlines)")
    ap.add_argument("--max-quotes", type=int, help="Optional max quote marks count (「+」)")
    ap.add_argument("--max-parens", type=int, help="Optional max parentheses marks count (（）()+)")
    ap.add_argument("--mode", choices=["dry-run", "run"], default="dry-run")
    ap.add_argument("--max-attempts", type=int, default=4)
    ap.add_argument("--task", default="script_chapter_review", help="LLMRouter task name (default: script_chapter_review)")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--temperature", type=float, default=0.4)
    ap.add_argument("--force", action="store_true", help="Rewrite even if already within constraints")
    args = ap.parse_args()

    channel = _norm_channel(args.channel)
    video = _norm_video(args.video)

    sources = _load_sources()
    cfg = _channel_cfg(channel, sources)
    sg = sources.get("script_globals") if isinstance(sources.get("script_globals"), dict) else {}

    cfg_min = cfg.get("target_chars_min")
    cfg_max = cfg.get("target_chars_max")
    try:
        cfg_min_i = int(cfg_min) if cfg_min is not None and str(cfg_min).strip() else None
    except Exception:
        cfg_min_i = None
    try:
        cfg_max_i = int(cfg_max) if cfg_max is not None and str(cfg_max).strip() else None
    except Exception:
        cfg_max_i = None

    min_chars = args.min_chars if args.min_chars is not None else (cfg_min_i or 6000)
    max_chars = args.max_chars if args.max_chars is not None else cfg_max_i
    target_min = _target_min_chars(min_chars=min_chars, max_chars=max_chars)

    def _as_int(value: Any) -> Optional[int]:
        try:
            return int(value) if value is not None and str(value).strip() else None
        except Exception:
            return None

    max_quotes = (
        args.max_quotes
        if args.max_quotes is not None
        else _as_int(cfg.get("a_text_quote_marks_max")) or _as_int(sg.get("a_text_quote_marks_max"))
    )
    max_parens = (
        args.max_parens
        if args.max_parens is not None
        else _as_int(cfg.get("a_text_paren_marks_max")) or _as_int(sg.get("a_text_paren_marks_max"))
    )

    base = video_root(channel, video)
    a_path = _canonical_a_text_path(base)
    if not a_path.exists():
        raise SystemExit(f"A-text not found: {a_path}")

    current = _read_text(a_path)
    cur_len = _chars_no_newlines(current)
    current_validation = _validate_a_text(
        current, min_chars=min_chars, max_chars=max_chars, max_quotes=max_quotes, max_parens=max_parens
    )
    if not args.force and current_validation.ok:
        print(f"[OK] already within constraints: chars={cur_len} path={a_path}")
        return 0

    # Load SSOT global rules + channel prompt/persona (best-effort)
    global_rules = ""
    rules_path = sg.get("a_text_rules") if isinstance(sg, dict) else None
    if rules_path:
        rp = Path(str(rules_path))
        rp = rp if rp.is_absolute() else (repo_root() / rp)
        if rp.exists():
            global_rules = _read_text(rp)

    channel_prompt = ""
    prompt_path = cfg.get("channel_prompt")
    if prompt_path:
        pp = Path(str(prompt_path))
        pp = pp if pp.is_absolute() else (repo_root() / pp)
        if pp.exists():
            channel_prompt = _read_text(pp)

    persona = ""
    persona_path = cfg.get("persona")
    if persona_path:
        psp = Path(str(persona_path))
        psp = psp if psp.is_absolute() else (repo_root() / psp)
        if psp.exists():
            persona = _read_text(psp)

    planning_hint = ""
    # planning_csv is optional; do not fail expansion if missing
    csv_path = cfg.get("planning_csv")
    if csv_path:
        cp = Path(str(csv_path))
        cp = cp if cp.is_absolute() else (repo_root() / cp)
        if cp.exists():
            try:
                import csv as _csv

                with cp.open(encoding="utf-8", newline="") as f:
                    rows = list(_csv.reader(f))
                if rows:
                    header = rows[0]
                    for row in rows[1:]:
                        if len(row) > 2 and row[2].strip().zfill(3) == video:
                            rec = dict(zip(header, row))
                            planning_hint = "\\n".join(
                                s
                                for s in [
                                    rec.get("タイトル") or "",
                                    rec.get("企画意図") or "",
                                    rec.get("ターゲット層") or "",
                                    rec.get("具体的な内容（話の構成案）") or "",
                                    rec.get("悩みタグ_メイン") or "",
                                    rec.get("悩みタグ_サブ") or "",
                                    rec.get("キーコンセプト") or "",
                                    rec.get("ベネフィット一言") or "",
                                ]
                                if s.strip()
                            )
                            break
            except Exception:
                planning_hint = ""

    router = get_router()

    draft = current
    last_validation = _validate_a_text(
        draft, min_chars=min_chars, max_chars=max_chars, max_quotes=max_quotes, max_parens=max_parens
    )
    for attempt in range(1, max(1, int(args.max_attempts)) + 1):
        if last_validation.ok and _chars_no_newlines(draft) >= min_chars:
            break

        cur_len = _chars_no_newlines(draft)
        quotes, parens = _quote_paren_counts(draft)
        action = "sanitize"
        delta_hint = ""
        if cur_len < min_chars:
            missing = min_chars - cur_len
            # When we're close to the minimum, prefer append-only to avoid overshooting max.
            if max_chars is not None and missing <= 800:
                need = max(0, target_min - cur_len)
                append_messages = _build_append_messages(
                    channel=channel,
                    video=video,
                    min_chars=min_chars,
                    target_min_chars=target_min,
                    max_chars=max_chars,
                    global_rules=global_rules,
                    persona=persona,
                    channel_prompt=channel_prompt,
                    planning_hint=planning_hint,
                    current_text=draft,
                    current_len=cur_len,
                    attempt=attempt,
                    need_chars=need,
                )
                out = router.call(
                    task=str(args.task),
                    messages=append_messages,
                    temperature=float(args.temperature),
                    max_tokens=int(args.max_tokens),
                )
                if isinstance(out, str) and out.strip():
                    out = out.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
                    out = _strip_heading_lines(_strip_list_prefixes(_strip_tts_punct(strip_meta_from_script(out).text)))
                    addition = out.strip()
                    if addition:
                        left = draft.rstrip()
                        right = addition.lstrip()
                        if left.endswith("\n---") and right.startswith("---"):
                            right = right.lstrip("-").lstrip()
                        draft = left + "\n" + right.strip() + "\n"
                        last_validation = _validate_a_text(
                            draft,
                            min_chars=min_chars,
                            max_chars=max_chars,
                            max_quotes=max_quotes,
                            max_parens=max_parens,
                        )
                        if last_validation.ok and _chars_no_newlines(draft) >= min_chars:
                            break
                        continue

            need = max(0, target_min - cur_len)
            # avoid mechanical filler; aim slightly above min to survive sanitization
            delta_hint = (
                f"不足: あと{need}字以上。言い換えで水増しせず、具体例や場面描写を追加して厚みを足してください。"
            )
            action = "expand"
        elif max_chars is not None and cur_len > max_chars:
            over = cur_len - max_chars
            hard_cut = over + 120
            delta_hint = (
                f"超過: {over}字。最低でも{hard_cut}字分を削り、必ず{max_chars}字以下に収めてください。"
                "核と要点は保ち、重複・回りくどい前置き・比喩の連打・同趣旨の言い換えを優先して削ってください。"
                f"ただし{min_chars}字未満にしないこと。"
            )
            action = "shrink"
        else:
            problems: List[str] = []
            if max_quotes is not None and quotes > max_quotes:
                problems.append(f"引用符が多すぎる: {quotes} > {max_quotes}")
            if max_parens is not None and parens > max_parens:
                problems.append(f"括弧が多すぎる: {parens} > {max_parens}")
            if _RE_URL.search(draft):
                problems.append("URLを削除")
            if _RE_FOOTNOTE_ANY.search(draft):
                problems.append("[number]形式を削除")
            if not problems:
                problems.append("禁則と読みやすさを整える")
            length_band = f"{min_chars}字以上" if max_chars is None else f"{min_chars}〜{max_chars}字"
            delta_hint = (
                " / ".join(problems)
                + "。意味は変えず、直接話法→間接話法の言い換えで記号を減らしてください。"
                + "削って短くしない。文字数の増減は±200以内を目安に、最終的に"
                + length_band
                + "へ収めてください。"
            )

        messages = _build_messages(
            channel=channel,
            video=video,
            min_chars=min_chars,
            target_min_chars=target_min,
            max_chars=max_chars,
            global_rules=global_rules,
            persona=persona,
            channel_prompt=channel_prompt,
            planning_hint=planning_hint,
            current_text=draft,
            current_len=cur_len,
            attempt=attempt,
            action=action,
            delta_hint=delta_hint,
        )

        out = router.call(
            task=str(args.task),
            messages=messages,
            temperature=float(args.temperature),
            max_tokens=int(args.max_tokens),
        )
        if not isinstance(out, str) or not out.strip():
            raise SystemExit("LLM returned empty output")

        out = out.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"

        # Hard safety: remove URL/footnote/meta tokens if they slipped in.
        sanitized = strip_meta_from_script(out)
        out = _strip_heading_lines(_strip_list_prefixes(_strip_tts_punct(sanitized.text)))

        draft = out
        last_validation = _validate_a_text(
            draft, min_chars=min_chars, max_chars=max_chars, max_quotes=max_quotes, max_parens=max_parens
        )
        if last_validation.ok and _chars_no_newlines(draft) >= min_chars:
            break

    # Fallback: if we are still short, ask for a small append-only continuation and merge.
    append_tries = 3
    for append_attempt in range(1, append_tries + 1):
        cur_len = _chars_no_newlines(draft)
        if cur_len >= min_chars:
            break
        need = max(0, target_min - cur_len)
        if need <= 0:
            need = max(0, min_chars - cur_len)
        if need <= 0:
            break

        messages = _build_append_messages(
            channel=channel,
            video=video,
            min_chars=min_chars,
            target_min_chars=target_min,
            max_chars=max_chars,
            global_rules=global_rules,
            persona=persona,
            channel_prompt=channel_prompt,
            planning_hint=planning_hint,
            current_text=draft,
            current_len=cur_len,
            attempt=append_attempt,
            need_chars=need,
        )
        out = router.call(
            task=str(args.task),
            messages=messages,
            temperature=float(args.temperature),
            max_tokens=int(args.max_tokens),
        )
        if not isinstance(out, str) or not out.strip():
            break
        out = out.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
        out = _strip_heading_lines(_strip_list_prefixes(_strip_tts_punct(strip_meta_from_script(out).text)))
        addition = out.strip()
        if not addition:
            break

        # Merge carefully (avoid accidental double separators at the join).
        left = draft.rstrip()
        right = addition.lstrip()
        if left.endswith("\n---") and right.startswith("---"):
            right = right.lstrip("-").lstrip()
        draft = left + "\n" + right.strip() + "\n"
        last_validation = _validate_a_text(
            draft, min_chars=min_chars, max_chars=max_chars, max_quotes=max_quotes, max_parens=max_parens
        )
        if last_validation.ok and _chars_no_newlines(draft) >= min_chars:
            break

    # Fallback: if we are still too long, run a few shrink-only passes.
    if max_chars is not None:
        shrink_tries = 3
        for shrink_attempt in range(1, shrink_tries + 1):
            cur_len = _chars_no_newlines(draft)
            if cur_len <= max_chars:
                break
            over = cur_len - max_chars
            hard_cut = over + 120
            delta_hint = (
                f"最終調整: {over}字超過。最低でも{hard_cut}字分を削り、必ず{max_chars}字以下に収めてください。"
                "核と要点は保ち、重複・回りくどい前置き・比喩の連打・同趣旨の言い換えを優先して削ってください。"
                f"ただし{min_chars}字未満にしないこと。"
            )
            messages = _build_messages(
                channel=channel,
                video=video,
                min_chars=min_chars,
                target_min_chars=target_min,
                max_chars=max_chars,
                global_rules=global_rules,
                persona=persona,
                channel_prompt=channel_prompt,
                planning_hint=planning_hint,
                current_text=draft,
                current_len=cur_len,
                attempt=max(1, int(args.max_attempts)) + shrink_attempt,
                action="shrink",
                delta_hint=delta_hint,
            )
            out = router.call(
                task=str(args.task),
                messages=messages,
                temperature=float(args.temperature),
                max_tokens=int(args.max_tokens),
            )
            if not isinstance(out, str) or not out.strip():
                break
            out = out.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
            out = _strip_heading_lines(_strip_list_prefixes(_strip_tts_punct(strip_meta_from_script(out).text)))
            draft = out
            last_validation = _validate_a_text(
                draft, min_chars=min_chars, max_chars=max_chars, max_quotes=max_quotes, max_parens=max_parens
            )
            if last_validation.ok:
                break

    final_len = _chars_no_newlines(draft)
    if not last_validation.ok or final_len < min_chars:
        reasons = ", ".join(last_validation.reasons) if last_validation.reasons else "unknown"
        raise SystemExit(f"failed to reach constraints (chars={final_len}): {reasons}")

    if args.mode == "dry-run":
        print(f"[DRY] would write expanded A-text: chars={final_len} path={a_path}")
        return 0

    backup_root = script_data_root() / "_archive" / f"a_text_expand_{_utc_now_compact()}"
    backup_root.mkdir(parents=True, exist_ok=True)
    _backup_file(a_path, backup_root)
    mirror = base / "content" / "assembled.md"
    if mirror.exists() and mirror.resolve() != a_path.resolve():
        _backup_file(mirror, backup_root)

    # Write canonical + mirror to avoid split-brain.
    a_path.parent.mkdir(parents=True, exist_ok=True)
    a_path.write_text(draft, encoding="utf-8")
    mirror.parent.mkdir(parents=True, exist_ok=True)
    mirror.write_text(draft, encoding="utf-8")

    print(f"[OK] updated: {channel}-{video} chars={final_len}")
    print(f"[OK] backup: {backup_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
