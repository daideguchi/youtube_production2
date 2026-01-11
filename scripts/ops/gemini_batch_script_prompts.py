#!/usr/bin/env python3
from __future__ import annotations

"""
gemini_batch_script_prompts.py — Gemini Batch用の「台本本文」プロンプトを生成する

背景:
- Fireworks/OpenRouter が使えない期間でも、台本本文（Aテキスト）を止めないための緊急導線。
- ただし “勝手に別モデルで書く” は事故なので、下準備（プロンプト）を Git に残し、Batchで実行する。

出力（既定）:
- マスタープロンプト（固定）: prompts/antigravity_gemini/MASTER_PROMPT.md
- 個別プロンプト（台本ごと; Git保存）: prompts/antigravity_gemini/CHxx/CHxx_NNN_PROMPT.md
- FULL（Batch投入の実体; master+個別）: prompts/antigravity_gemini/CHxx/CHxx_NNN_FULL_PROMPT.md
"""

import argparse
import csv
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from _bootstrap import bootstrap

bootstrap(load_env=False)

import yaml  # noqa: E402

from factory_common import paths as repo_paths  # noqa: E402


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _z3(n: int | str) -> str:
    try:
        return str(int(n)).zfill(3)
    except Exception:
        return str(n).zfill(3)


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
            out.extend(list(range(int(a), int(b) + 1)))
        else:
            out.append(int(t))
    return sorted(set([i for i in out if i > 0]))


def _parse_videos(expr: str) -> List[str]:
    ids = _parse_indices(expr)
    return [_z3(i) for i in ids]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_yaml(path: Path) -> Dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _load_sources_channel(channel: str) -> Dict[str, Any]:
    cfg = _read_yaml(repo_paths.repo_root() / "configs" / "sources.yaml")
    channels = cfg.get("channels") if isinstance(cfg, dict) else None
    ch = (channels or {}).get(str(channel).upper()) if isinstance(channels, dict) else None
    return ch if isinstance(ch, dict) else {}


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


def _pattern_triggers_match(triggers: Any, title: str) -> tuple[bool, int]:
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
        # Prefer channel-specific over wildcard when scores tie.
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

    # Fallback: first wildcard pattern.
    for pat in patterns:
        if not isinstance(pat, dict):
            continue
        if _pattern_channel_applies(pat.get("channels"), norm_channel) and "*" in [
            str(x or "").strip() for x in (pat.get("channels") or [])
        ]:
            return pat
    return {}


def _read_planning_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows: List[Dict[str, str]] = []
        for row in reader:
            rows.append({k: (v if v is not None else "") for k, v in row.items()})
    return fieldnames, rows


def _video_number_from_row(row: Dict[str, str]) -> str:
    for key in ("動画番号", "No.", "VideoNumber", "video_number", "video"):
        raw = (row.get(key) or "").strip()
        if not raw:
            continue
        try:
            return _z3(int(raw))
        except Exception:
            return _z3(raw)
    for key in ("動画ID", "台本番号", "ScriptID", "script_id"):
        v = (row.get(key) or "").strip()
        m = re.search(r"\bCH\d{2}-(\d{3})\b", v)
        if m:
            return m.group(1)
    return ""


def _index_rows_by_video(rows: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for row in rows:
        vid = _video_number_from_row(row)
        if not vid:
            continue
        out.setdefault(vid, row)
    return out


def _extract_str(row: Dict[str, str], key: str) -> str:
    return str(row.get(key) or "").strip()


def _join_nonempty(lines: Iterable[str]) -> str:
    return "\n".join([x for x in [str(s) for s in lines] if x.strip()]).rstrip() + "\n"


def _trim_channel_prompt(text: str) -> str:
    """
    Channel prompts often include human-facing input templates/code blocks.
    For Batch, keep only the directive part (reduce tokens + avoid template echo).
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    marker = "▼▼▼プロンプト入力欄"
    if marker in raw:
        raw = raw.split(marker, 1)[0].rstrip()
    return raw.strip()


def _extract_persona_one_liner(text: str) -> str:
    """
    Extract a single persona sentence from a persona/template markdown.
    Uses the first blockquote line (e.g. '> ...').
    """
    raw = str(text or "")
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith(">"):
            return s.lstrip(">").strip()
    return ""


@dataclass(frozen=True)
class BuildResult:
    channel: str
    video: str
    script_id: str
    prompt_path: Path
    full_prompt_path: Path


_INDIVIDUAL_PROMPT_MARKER = "<<<INDIVIDUAL_PROMPT_START>>>"


def build_prompts(
    *,
    channel: str,
    videos: List[str],
    overwrite: bool,
    require_existing_script_dir: bool,
    dry_run: bool,
) -> List[BuildResult]:
    ch = str(channel).strip().upper()
    if not re.fullmatch(r"CH\d{2}", ch):
        raise SystemExit(f"Invalid --channel: {channel!r} (expected CHxx)")

    sources = _load_sources_channel(ch)
    planning_csv_rel = str(sources.get("planning_csv") or "").strip()
    channel_prompt_rel = str(sources.get("channel_prompt") or "").strip()
    persona_rel = str(sources.get("persona") or "").strip()
    if not planning_csv_rel:
        raise SystemExit(f"configs/sources.yaml: channels.{ch}.planning_csv is missing")
    if not channel_prompt_rel:
        raise SystemExit(f"configs/sources.yaml: channels.{ch}.channel_prompt is missing")

    planning_csv = (repo_paths.repo_root() / planning_csv_rel).resolve()
    channel_prompt_path = (repo_paths.repo_root() / channel_prompt_rel).resolve()
    persona_path = (repo_paths.repo_root() / persona_rel).resolve() if persona_rel else None

    if not planning_csv.exists():
        raise SystemExit(f"Planning CSV not found: {planning_csv}")
    if not channel_prompt_path.exists():
        raise SystemExit(f"Channel prompt not found: {channel_prompt_path}")

    _fields, rows = _read_planning_csv(planning_csv)
    by_video = _index_rows_by_video(rows)

    patterns_doc = _read_yaml(repo_paths.repo_root() / "ssot" / "ops" / "OPS_SCRIPT_PATTERNS.yaml")

    master_prompt_path = repo_paths.repo_root() / "prompts" / "antigravity_gemini" / "MASTER_PROMPT.md"
    if not master_prompt_path.exists():
        raise SystemExit(f"Master prompt not found: {master_prompt_path}")
    master_prompt = _read_text(master_prompt_path)
    channel_prompt = _trim_channel_prompt(_read_text(channel_prompt_path))
    persona_raw = _read_text(persona_path) if (persona_path and persona_path.exists()) else ""
    persona_one = _extract_persona_one_liner(persona_raw)

    target_min = sources.get("target_chars_min")
    target_max = sources.get("target_chars_max")
    chapter_count = sources.get("chapter_count")

    results: List[BuildResult] = []
    for video in videos:
        row = by_video.get(video)
        if row is None:
            raise SystemExit(f"Planning row not found for {ch}-{video} (CSV: {planning_csv})")
        title = _extract_str(row, "タイトル")
        if not title:
            raise SystemExit(f"Missing title for {ch}-{video} in CSV")

        script_id = f"{ch}-{video}"
        pat = _select_pattern(patterns_doc, ch, title)
        pat_id = str(pat.get("id") or "").strip()
        plan = pat.get("plan") if isinstance(pat.get("plan"), dict) else {}
        sections = plan.get("sections") if isinstance(plan, dict) else None
        sections = sections if isinstance(sections, list) else []

        video_root = repo_paths.video_root(ch, video)
        if require_existing_script_dir and not video_root.exists():
            print(f"[SKIP] missing script dir: {video_root} ({script_id})")
            continue
        out_dir = repo_paths.repo_root() / "prompts" / "antigravity_gemini" / ch
        prompt_path = out_dir / f"{ch}_{video}_PROMPT.md"
        full_prompt_path = out_dir / f"{ch}_{video}_FULL_PROMPT.md"
        if (prompt_path.exists() or full_prompt_path.exists()) and not overwrite:
            print(f"[SKIP] exists: {prompt_path} / {full_prompt_path}")
            continue

        prompt_lines: List[str] = []
        prompt_lines.append(f"# GEMINI_BATCH_SCRIPT_PROMPT — {script_id}")
        prompt_lines.append(f"- generated_at: {_utc_now_iso()}")
        prompt_lines.append(f"- channel: {ch}")
        prompt_lines.append(f"- video: {video}")
        prompt_lines.append(f"- title: {title}")
        if pat_id:
            prompt_lines.append(f"- pattern_id: {pat_id}")
        prompt_lines.append("")

        prompt_lines.append("## 0) 使い方（固定）")
        prompt_lines.append("- 先に `prompts/antigravity_gemini/MASTER_PROMPT.md` を貼る")
        prompt_lines.append("- 続けて、この個別プロンプトを全部貼る")
        prompt_lines.append("")

        prompt_lines.append("## 1) CHANNEL PROMPT（チャンネル固有; 抜粋）")
        prompt_lines.append(channel_prompt.strip())
        prompt_lines.append("")
        if persona_one.strip():
            prompt_lines.append("## 2) PERSONA（固定一文）")
            prompt_lines.append(persona_one.strip())
            prompt_lines.append("")

        prompt_lines.append("## 3) 構造設計（Pattern plan）")
        prompt_lines.append(f"- target_chars_min: {target_min}")
        prompt_lines.append(f"- target_chars_max: {target_max}")
        prompt_lines.append(f"- chapter_count: {chapter_count}")
        if plan:
            core_msg = str(plan.get("core_message_template") or "").strip()
            if core_msg:
                prompt_lines.append(f"- core_message: {core_msg}")
        prompt_lines.append("")
        if sections:
            prompt_lines.append("セクション（順番固定 / 章見出しは出力しない。区切りは `---` の行のみ）:")
            for i, sec in enumerate(sections, start=1):
                if not isinstance(sec, dict):
                    continue
                name = str(sec.get("name") or "").strip()
                budget = sec.get("char_budget")
                goal = str(sec.get("goal") or "").strip()
                notes = str(sec.get("content_notes") or "").strip()
                prompt_lines.append(f"{i}. {name}（{budget}字目安）")
                if goal:
                    prompt_lines.append(f"   - goal: {goal}")
                if notes:
                    prompt_lines.append(f"   - notes: {notes}")
            prompt_lines.append("")
        else:
            prompt_lines.append("（pattern plan が見つからない/空です。チャンネルプロンプトと企画入力を優先して構成する）")
            prompt_lines.append("")

        # Optional: use existing outline if present (\"本文だけ\"運用の下準備).
        outline_path = repo_paths.video_root(ch, video) / "content" / "outline.md"
        if outline_path.exists():
            prompt_lines.append("## 4) 参考: outline.md（あれば最優先で従う）")
            prompt_lines.append(_read_text(outline_path).strip())
            prompt_lines.append("")

        prompt_lines.append("## 5) INPUT（企画; Planning CSV）")
        # Keep the minimum set visible (even when empty) so Gemini can return [NEEDS_INPUT] safely.
        target = _extract_str(row, "ターゲット層") or persona_one.strip()
        minimal_fields: List[Tuple[str, str]] = [
            ("企画意図", _extract_str(row, "企画意図")),
            ("ターゲット層", target),
            ("具体的な内容（話の構成案）", _extract_str(row, "具体的な内容（話の構成案）")),
            ("史実エピソード候補", _extract_str(row, "史実エピソード候補")),
            ("避けたい話題/表現", _extract_str(row, "避けたい話題/表現")),
        ]
        for k, v in minimal_fields:
            prompt_lines.append(f"- {k}:")
            prompt_lines.append(v if v else "（未入力）")
            prompt_lines.append("")

        optional_keys = [
            "悩みタグ_メイン",
            "悩みタグ_サブ",
            "ライフシーン",
            "キーコンセプト",
            "ベネフィット一言",
            "たとえ話イメージ",
            "説明文_リード",
            "説明文_この動画でわかること",
        ]
        for k in optional_keys:
            val = _extract_str(row, k)
            if not val:
                continue
            prompt_lines.append(f"- {k}:")
            prompt_lines.append(val)
            prompt_lines.append("")

        prompt_lines.append("### INPUT CHECK（不足なら [NEEDS_INPUT]）")
        prompt_lines.append("- 企画意図 / 具体的な内容（話の構成案） / 史実エピソード候補 が未入力のままなら本文を書かない")
        prompt_lines.append("- 必須が埋まらない場合は、不足項目だけを列挙して終了する（本文は出さない）")
        prompt_lines.append("")

        prompt_lines.append("## 6) 出力の追加条件（この個別で固定したいもの）")
        prompt_lines.append("- 出力は台本本文のみ（前置き/解説/見出し/箇条書きは禁止）")
        prompt_lines.append("- 章見出しは出さない（必要なら文脈で話題転換する）")
        prompt_lines.append("- ポーズ/区切りは `---` のみ（等間隔/機械分割は禁止。論点転換の境界で最小限）")
        prompt_lines.append("")

        individual_prompt = _join_nonempty(prompt_lines)
        full_prompt = _join_nonempty(
            [
                master_prompt.strip(),
                "",
                _INDIVIDUAL_PROMPT_MARKER,
                "",
                individual_prompt.strip(),
            ]
        )

        if dry_run:
            print(f"[DRY] write: {prompt_path}")
            print(f"[DRY] write: {full_prompt_path}")
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(individual_prompt, encoding="utf-8")
            full_prompt_path.write_text(full_prompt, encoding="utf-8")
            print(f"[OK] wrote: {prompt_path}")
            print(f"[OK] wrote: {full_prompt_path}")

        results.append(
            BuildResult(
                channel=ch,
                video=video,
                script_id=script_id,
                prompt_path=prompt_path,
                full_prompt_path=full_prompt_path,
            )
        )

    return results


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate per-episode prompts for Gemini Batch script writing.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("build", help="Build per-episode prompt files from Planning CSV + SSOT patterns + channel prompt")
    sp.add_argument("--channel", required=True, help="Channel id (e.g., CH01)")
    sp.add_argument("--videos", required=True, help="Video ids/ranges (e.g., 251-290 or 001,002,010)")
    sp.add_argument("--overwrite", action="store_true", help="Overwrite existing prompt files")
    sp.add_argument(
        "--allow-missing-script-dir",
        action="store_true",
        help="Also generate prompts even when workspaces/scripts/{CH}/{NNN} does not exist (outline/status may be unavailable).",
    )
    sp.add_argument("--dry-run", action="store_true", help="Do not write files; only print actions")

    args = ap.parse_args()
    if args.cmd == "build":
        videos = _parse_videos(args.videos)
        build_prompts(
            channel=str(args.channel),
            videos=videos,
            overwrite=bool(args.overwrite),
            require_existing_script_dir=not bool(args.allow_missing_script_dir),
            dry_run=bool(args.dry_run),
        )
        return 0

    raise SystemExit(f"Unknown cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
