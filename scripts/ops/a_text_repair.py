#!/usr/bin/env python3
"""
a_text_repair.py — deterministic A-text repair helper (NO LLM).

Goal:
- Fix *hard* A-text validator failures without "manual rewrite" or LLM APIs.
- Keep edits minimal, mechanical, and reversible (archive-first backups).

Targets (SSOT: ssot/ops/OPS_A_TEXT_GLOBAL_RULES.md):
- punctuation_only_line
- punctuation_wrap_emphasis_abuse (>= 50 occurrences of `。<short>。`)
- sleep_framing_contamination (non-sleep channels)
- duplicate_paragraph (>=120 chars, whitespace-insensitive)
- forbidden_statistics (percent/パーセント表現; warning)

Usage:
  python3 scripts/ops/a_text_repair.py --channel CH06 --videos 034-093 --mode run
  python3 scripts/ops/a_text_repair.py --channel CH06 --videos 034,036,037 --mode dry-run
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import logs_root, repo_root, video_root  # noqa: E402
from script_pipeline.validator import validate_a_text  # noqa: E402


_PUNCT_ONLY_CHARS = set("。、.,，．・…")
_RE_WS_FOR_DUP = re.compile(r"\s+")
# NOTE: must match script_pipeline.validator._RE_PUNCT_WRAP_EMPHASIS semantics exactly.
# That regex is intentionally conservative and treats very short "sentences" as emphasis spam,
# even when line breaks exist between them.
_RE_PUNCT_WRAP = re.compile(r"。([^\\s。！？]{1,12})。")
_RE_SENT_SPLIT = re.compile(r"(?<=[。！？])")

# Copy of validator markers (keep in sync with script_pipeline.validator; do NOT broaden casually).
_SLEEP_FRAMING_ANY_MARKERS = (
    "寝落ち",
    "睡眠用",
    "安眠",
    "就寝",
    "入眠",
    "熟睡",
    "ベッド",
    "枕",
    "寝室",
    "寝る",
    "眠り",
    "眠りにつ",
    "眠りに落",
    "眠りに就",
    "眠りへ誘う",
    "眠りへ導く",
    "眠りへ",
)

_RE_STATS_TOKEN = re.compile(r"(?:[%％]|パーセント)")


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _norm_channel(value: str) -> str:
    ch = (value or "").strip().upper()
    if not ch:
        raise SystemExit("channel is required (e.g. CH06)")
    if re.fullmatch(r"CH\d{2}", ch):
        return ch
    m = re.fullmatch(r"CH(\d+)", ch)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return ch


def _norm_video(value: str) -> str:
    token = (value or "").strip()
    digits = "".join(ch for ch in token if ch.isdigit())
    if not digits:
        raise SystemExit(f"invalid video token: {value!r}")
    return f"{int(digits):03d}"


def _parse_videos(values: Optional[Iterable[str]]) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for raw in values:
        if raw is None:
            continue
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                start = int(_norm_video(a))
                end = int(_norm_video(b))
                if end < start:
                    start, end = end, start
                out.extend([f"{n:03d}" for n in range(start, end + 1)])
            else:
                out.append(_norm_video(part))
    return sorted(set(out))


@dataclass(frozen=True)
class TargetPaths:
    base_dir: Path
    assembled_human: Path
    assembled: Path
    status_json: Path

    @property
    def canonical(self) -> Path:
        return self.assembled_human if self.assembled_human.exists() else self.assembled


def _resolve_targets(channel: str, video: str) -> TargetPaths:
    base = video_root(channel, video)
    content_dir = base / "content"
    return TargetPaths(
        base_dir=base,
        assembled_human=content_dir / "assembled_human.md",
        assembled=content_dir / "assembled.md",
        status_json=base / "status.json",
    )


def _read_text_best_effort(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _is_punct_only_line(line: str) -> bool:
    s = (line or "").strip()
    if not s or s == "---":
        return False
    return all(ch in _PUNCT_ONLY_CHARS for ch in s)


def _remove_punct_only_lines(text: str) -> tuple[str, int]:
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    removed = 0
    out: list[str] = []
    for ln in lines:
        if _is_punct_only_line(ln):
            removed += 1
            # Replace with blank line (pause must be `---`, not punctuation).
            out.append("")
            continue
        out.append(ln)
    return "\n".join(out), removed


def _remove_duplicate_paragraphs(text: str) -> tuple[str, int]:
    """
    Remove verbatim duplicate paragraphs (>=120 chars, whitespace-insensitive).
    Paragraph boundary = blank line or `---` (same as validator).
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    paragraphs: list[list[str]] = []
    buf: list[str] = []
    for line in lines:
        if not line.strip() or line.strip() == "---":
            if buf:
                paragraphs.append(buf)
                buf = []
            paragraphs.append([line])  # keep boundary marker/blank as-is
            continue
        buf.append(line)
    if buf:
        paragraphs.append(buf)

    seen: dict[str, bool] = {}
    removed = 0
    out_lines: list[str] = []
    para_buf: list[str] = []

    def _flush_para(lines_: list[str]) -> None:
        nonlocal removed
        if not lines_:
            return
        if len(lines_) == 1 and (not lines_[0].strip() or lines_[0].strip() == "---"):
            out_lines.append(lines_[0])
            return
        para = "\n".join(lines_).strip()
        core = _RE_WS_FOR_DUP.sub("", para).strip()
        if len(core) >= 120 and core in seen:
            removed += 1
            return
        if len(core) >= 120:
            seen[core] = True
        out_lines.extend(lines_)

    # Re-walk with boundary-aware blocks.
    for blk in paragraphs:
        if len(blk) == 1 and (not blk[0].strip() or blk[0].strip() == "---"):
            _flush_para(para_buf)
            para_buf = []
            _flush_para(blk)
            continue
        # Part of a paragraph
        para_buf.extend(blk)
    _flush_para(para_buf)
    return "\n".join(out_lines), removed


def _remove_duplicate_sentences(text: str, *, min_chars: int = 24) -> tuple[str, int]:
    """
    Remove duplicate sentences (best-effort, deterministic).

    Why:
    - Some generated scripts repeat the same 2-10 sentences after `---` or later in the text,
      but the paragraphs are not exact duplicates (extra sentences appended), so paragraph-dedupe
      can't catch them.
    - This targets verbatim repeats only; it does not attempt semantic rewriting.

    Notes:
    - We dedupe only when a sentence is >= min_chars (whitespace-insensitive), to avoid
      removing common short connectives ("だが", "しかし", etc.) that may legitimately recur.
    - `---` lines are preserved as-is.
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.strip():
        return normalized, 0

    seen: set[str] = set()
    prev_core: str | None = None
    removed = 0
    out_lines: list[str] = []

    for raw_line in normalized.split("\n"):
        line = raw_line.rstrip("\n")
        if line.strip() == "---":
            out_lines.append("---")
            prev_core = None
            continue
        if not line.strip():
            out_lines.append("")
            prev_core = None
            continue

        parts = [p for p in _RE_SENT_SPLIT.split(line) if p]
        kept: list[str] = []
        for part in parts:
            core = _RE_WS_FOR_DUP.sub("", part).strip()
            if core and prev_core == core:
                removed += 1
                continue
            if core:
                prev_core = core
            if len(core) < int(min_chars):
                kept.append(part)
                continue
            if core in seen:
                removed += 1
                continue
            seen.add(core)
            kept.append(part)
        new_line = "".join(kept).strip()
        out_lines.append(new_line if new_line else "")

    out = "\n".join(out_lines)
    if normalized.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out, removed


def _apply_ch06_phrase_sanitizer(text: str) -> tuple[str, int]:
    """
    CH06-specific cliché cleanup (deterministic).

    Why:
    - CH06 polish rules forbid some stock phrases (e.g. 「断片をつなぐと筋が見える」) and
      the broader pattern is overused across drafts.
    - This function makes minimal, mechanical substitutions without adding new facts.
    """
    raw = text or ""
    out = raw

    # Strongly discouraged phrase (normalize variants with optional punctuation).
    out = re.sub(
        r"断片をつなぐと[、,\s]*筋が見える",
        "散らばった記録を照らし合わせると、同じ流れが見えてくる",
        out,
    )

    # Reduce overuse of "connect fragments" phrasing.
    out = out.replace("断片をつなぎ合わせ", "断片を照らし合わせ")
    out = out.replace("断片をつなげば", "断片を照合すれば")
    out = out.replace("断片をつなごう", "断片を照合しよう")
    out = out.replace("断片をつなぐ", "断片を照合する")

    changed = out != raw
    return out, (1 if changed else 0)


def _strip_sleep_framing_markers(text: str) -> tuple[str, int]:
    """
    Non-sleep channels must not contain sleep-framing markers.
    This is a best-effort deterministic replacement (minimal writing).
    """
    raw = text or ""
    before = raw

    # Prefer phrase-level substitutions first (avoid partials).
    repls = [
        # Canonical non-sleep outro (avoid turning into "安らかな沈黙を").
        ("静寂の中で、安らかな眠りを。", "静寂の中で、心がほどけますように。"),
        ("静寂の中で、安らかな眠りを", "静寂の中で、心がほどけますように"),
        ("眠りにつ", "静かにな"),
        ("眠りに落", "意識が遠の"),
        ("眠りに就", "静けさに沈"),
        ("眠りへ誘う", "静けさへ導く"),
        ("眠りへ導く", "静けさへ導く"),
        ("寝落ち", "聞き流し"),
        ("睡眠用", "作業用"),
        ("安眠", "安心"),
        ("就寝", "夜"),
        ("入眠", "休息"),
        ("熟睡", "深い休息"),
        ("寝室", "部屋"),
        ("ベッド", "部屋"),
        ("枕", "クッション"),
        ("寝る", "休む"),
        ("眠り", "沈黙"),
        ("眠りへ", "静けさへ"),
    ]
    for src, dst in repls:
        before = before.replace(src, dst)

    # Count how many markers remain (best-effort; overlaps possible).
    removed = 0
    for marker in _SLEEP_FRAMING_ANY_MARKERS:
        if marker in raw and marker not in before:
            removed += 1
    # Also: if we changed anything, removed>=1 even if counting overlaps is messy.
    if before != raw and removed == 0:
        removed = 1
    return before, removed


def _sanitize_forbidden_statistics(text: str) -> tuple[str, int]:
    """
    Best-effort: remove percent/percentage expressions to avoid "fake statistics" vibes.
    This is deterministic and does NOT add new claims; it only makes numeric ratios qualitative.
    (See: script_pipeline.runner._sanitize_a_text_forbidden_statistics)
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return text or "", 0
    if not _RE_STATS_TOKEN.search(normalized):
        return text or "", 0

    before_tokens = len(_RE_STATS_TOKEN.findall(normalized))
    fullwidth_to_ascii = str.maketrans("０１２３４５６７８９", "0123456789")

    def _to_int(raw: str) -> int | None:
        s = (raw or "").translate(fullwidth_to_ascii).strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return None

    changed = False

    def repl_people(m: re.Match[str]) -> str:
        nonlocal changed
        n = _to_int(m.group(1))
        suffix = str(m.group(2) or "人")
        if n is None:
            changed = True
            return f"一部の{suffix}"
        if n >= 70:
            prefix = "多くの"
        elif n >= 40:
            prefix = "少なくない"
        elif n >= 10:
            prefix = "一部の"
        else:
            prefix = "ごく一部の"
        changed = True
        return f"{prefix}{suffix}"

    def repl_probability(m: re.Match[str]) -> str:
        nonlocal changed
        n = _to_int(m.group(1))
        kind = str(m.group(2) or "可能性")
        if n is None:
            changed = True
            return f"一定の{kind}"
        if n >= 90:
            prefix = "非常に高い"
        elif n >= 70:
            prefix = "高い"
        elif n >= 40:
            prefix = "それなりの"
        elif n >= 10:
            prefix = "低い"
        else:
            prefix = "ごく低い"
        changed = True
        return f"{prefix}{kind}"

    def repl_general(m: re.Match[str]) -> str:
        nonlocal changed
        n = _to_int(m.group(1))
        if n is None:
            changed = True
            return "ある程度"

        # If followed by "の", prefer noun-like expressions (e.g., ほとんどの人).
        after = normalized[m.end() :]
        i = 0
        while i < len(after) and after[i] in (" ", "\t", "\u3000"):
            i += 1
        follows_no = after[i : i + 1] == "の"
        if follows_no:
            if n >= 100:
                out = "すべて"
            elif n >= 90:
                out = "ほとんど"
            elif n >= 70:
                out = "多く"
            elif n >= 40:
                out = "半分ほど"
            elif n >= 10:
                out = "一部"
            else:
                out = "ごく一部"
        else:
            if n >= 100:
                out = "完全に"
            elif n >= 90:
                out = "ほぼ"
            elif n >= 70:
                out = "たいてい"
            elif n >= 40:
                out = "半分ほど"
            elif n >= 10:
                out = "ときどき"
            else:
                out = "まれに"
        changed = True
        return out

    out = normalized
    out = re.sub(r"([0-9０-９]{1,3})\s*(?:[%％]|パーセント)\s*の\s*(人(?:々|たち)?)", repl_people, out)
    out = re.sub(r"([0-9０-９]{1,3})\s*(?:[%％]|パーセント)\s*の\s*(確率|可能性)", repl_probability, out)
    out = re.sub(r"([0-9０-９]{1,3})\s*(?:[%％]|パーセント)", repl_general, out)

    if _RE_STATS_TOKEN.search(out):
        changed = True
        out = out.replace("%", "").replace("％", "").replace("パーセント", "")

    if not changed:
        return text or "", 0
    # Preserve trailing newline if the original had it.
    if normalized.endswith("\n") and not out.endswith("\n"):
        out += "\n"
    return out, before_tokens


def _choose_wrap_replacement(phrase: str) -> str:
    """
    Decide how to break `。<phrase>。` pattern.
    - Questions -> '？'
    - Otherwise -> '、' (merge with next sentence)
    """
    s = (phrase or "").strip()
    if any(tok in s for tok in ("なぜ", "どうして", "本当", "なのか", "だろう", "でしょうか")):
        return "？"
    if s.endswith(("か", "のか")):
        return "？"
    # Dates/numbers often read better when merged.
    if re.fullmatch(r"[0-9]{1,4}年", s):
        return "、"
    if re.fullmatch(r"[0-9]{1,2}月", s):
        return "、"
    if re.fullmatch(r"[0-9]{1,2}日", s):
        return "、"
    return "、"


def _reduce_punct_wrap_emphasis(text: str, *, max_allowed: int = 49) -> tuple[str, dict[str, int]]:
    """
    Reduce occurrences of `。<short>。` until it drops below the validator threshold.
    Returns (new_text, stats).
    """
    raw = text or ""
    matches = list(_RE_PUNCT_WRAP.finditer(raw))
    total = len(matches)
    if total <= max_allowed:
        return raw, {"before": total, "after": total, "rewritten": 0}

    need = total - max_allowed

    # Prioritize very short phrases first (tend to be "emphasis spam" rather than content).
    ranked: list[tuple[int, int]] = []
    for i, m in enumerate(matches):
        phrase = (m.group(1) or "").strip()
        prio = 0
        prio += max(0, 12 - len(phrase))  # shorter => higher priority
        if phrase in ("しかし", "だが", "でも", "そして", "つまり", "要するに"):
            prio += 10
        if any(tok in phrase for tok in ("なぜ", "どうして", "本当")):
            prio += 8
        ranked.append((-prio, i))
    ranked.sort()

    rewrite_idx: set[int] = set(i for _prio, i in ranked[:need])

    out: list[str] = []
    last = 0
    rewritten = 0
    for i, m in enumerate(matches):
        out.append(raw[last : m.start()])
        phrase = m.group(1) or ""
        if i in rewrite_idx:
            repl = _choose_wrap_replacement(phrase)
            out.append("。" + phrase + repl)
            rewritten += 1
        else:
            out.append(m.group(0))
        last = m.end()
    out.append(raw[last:])
    new = "".join(out)

    after = len(_RE_PUNCT_WRAP.findall(new))

    # Safety: script must not end with a comma due to a rewrite.
    # Use a closing punctuation that keeps the wrap-emphasis pattern broken.
    core = new.rstrip()
    if core.endswith("、"):
        new = core[:-1] + "！" + ("\n" if new.endswith("\n") else "")
    after2 = len(_RE_PUNCT_WRAP.findall(new))
    return new, {"before": total, "after": after2, "rewritten": rewritten}


def _load_metadata(status_json: Path) -> dict[str, Any]:
    if not status_json.exists():
        return {}
    try:
        obj = json.loads(status_json.read_text(encoding="utf-8"))
    except Exception:
        return {}
    md = obj.get("metadata") if isinstance(obj, dict) and isinstance(obj.get("metadata"), dict) else {}
    return dict(md)


def _backup_file(src: Path, backup_root: Path) -> Path:
    root = repo_root()
    try:
        rel = src.resolve().relative_to(root)
    except Exception:
        rel = Path(str(src).replace("\\", "/").lstrip("/"))
    dst = backup_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(_read_text_best_effort(src), encoding="utf-8")
    return dst


def _restamp_alignment_from_status(*, channel: str, video: str, script_path: Path) -> bool:
    """
    If we modify A-text, we must re-stamp alignment hashes in status.json,
    otherwise script_validation/audio will block with alignment_script_hash_mismatch.
    """
    try:
        from factory_common.alignment import ALIGNMENT_SCHEMA, build_alignment_stamp
        from script_pipeline.sot import load_status, save_status

        st = load_status(channel, video)
        align = st.metadata.get("alignment")
        planning = align.get("planning") if isinstance(align, dict) else None
        if not isinstance(planning, dict) or not planning:
            return False

        stamp = build_alignment_stamp(planning_row=planning, script_path=script_path)
        st.metadata["alignment"] = stamp.as_dict()
        st.metadata["alignment"]["schema"] = ALIGNMENT_SCHEMA
        try:
            title = stamp.planning.get("title")
            if isinstance(title, str) and title.strip():
                st.metadata["sheet_title"] = title.strip()
        except Exception:
            pass
        save_status(st)
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic A-text repair (no LLM).")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--videos", required=True, help="Comma/range list, e.g. 034-093 or 034,036")
    ap.add_argument("--mode", choices=["dry-run", "run"], default="run")
    ap.add_argument(
        "--backup-dir",
        default="",
        help="Override backup dir (default: workspaces/scripts/_archive/a_text_repair_<timestamp>)",
    )
    ap.add_argument("--write-report", action="store_true", help="Write JSON report under workspaces/logs/regression/")
    args = ap.parse_args()

    channel = _norm_channel(args.channel)
    videos = _parse_videos([args.videos])
    if not videos:
        raise SystemExit("no videos specified")

    backup_root = (
        Path(args.backup_dir).expanduser().resolve()
        if str(args.backup_dir).strip()
        else (repo_root() / "workspaces" / "scripts" / "_archive" / f"a_text_repair_{_utc_now_compact()}")
    )

    report_rows: list[dict[str, Any]] = []
    changed_any = False
    for video in videos:
        targets = _resolve_targets(channel, video)
        canonical = targets.canonical
        if not canonical.exists():
            print(f"[SKIP] {channel}-{video}: missing A-text: {canonical}")
            continue

        raw = _read_text_best_effort(canonical)
        meta = _load_metadata(targets.status_json)
        issues_before, stats_before = validate_a_text(raw, meta)
        hard_before = [it for it in issues_before if str(it.get("severity") or "error").lower() != "warning"]

        text, removed_punct = _remove_punct_only_lines(raw)
        text, removed_dup = _remove_duplicate_paragraphs(text)
        text, removed_dup_sent = _remove_duplicate_sentences(text)
        text, removed_ch06_phrases = (_apply_ch06_phrase_sanitizer(text) if channel == "CH06" else (text, 0))
        text, removed_sleep = _strip_sleep_framing_markers(text)
        text, stats_tokens = _sanitize_forbidden_statistics(text)
        text, wrap_stats = _reduce_punct_wrap_emphasis(text)

        issues_after, stats_after = validate_a_text(text, meta)
        hard_after = [it for it in issues_after if str(it.get("severity") or "error").lower() != "warning"]

        changed = text != raw
        changed_any = changed_any or changed

        # Even when no further text change is needed, status.json may still have stale alignment hashes
        # (e.g., text was repaired previously by another tool).
        alignment_needs_restamp = False
        try:
            from factory_common.alignment import sha1_file

            align = meta.get("alignment") if isinstance(meta.get("alignment"), dict) else {}
            old_hash = str(align.get("script_hash") or "").strip()
            if old_hash:
                cur_hash = sha1_file(canonical)
                alignment_needs_restamp = old_hash != cur_hash
        except Exception:
            alignment_needs_restamp = False

        row = {
            "schema": "ytm.ops.a_text_repair_row.v1",
            "script_id": f"{channel}-{video}",
            "path": str(canonical.relative_to(repo_root())),
            "changed": changed,
            "fixes": {
                "punct_only_lines_removed": removed_punct,
                "duplicate_paragraphs_removed": removed_dup,
                "duplicate_sentences_removed": removed_dup_sent,
                "ch06_phrase_sanitizer_applied": removed_ch06_phrases,
                "sleep_markers_replaced": removed_sleep,
                "statistics_tokens_sanitized": stats_tokens,
                "wrap_emphasis": wrap_stats,
            },
            "validator": {
                "hard_before": [it.get("code") for it in hard_before if isinstance(it, dict)],
                "hard_after": [it.get("code") for it in hard_after if isinstance(it, dict)],
                "stats_before": {"punct_wrap_emphasis": stats_before.get("punct_wrap_emphasis")},
                "stats_after": {"punct_wrap_emphasis": stats_after.get("punct_wrap_emphasis")},
            },
        }
        report_rows.append(row)

        label = f"{channel}-{video}"
        if not changed and not alignment_needs_restamp:
            print(f"[OK ] {label}: no changes needed")
            continue

        if changed:
            print(
                f"[FIX] {label}: punct_only={removed_punct} dup_para={removed_dup} dup_sent={removed_dup_sent} "
                f"sleep={removed_sleep} stats={stats_tokens} "
                f"wrap={wrap_stats.get('before')}->{wrap_stats.get('after')} (rewrote={wrap_stats.get('rewritten')})"
            )
        else:
            print(f"[FIX] {label}: restamp alignment only (text unchanged)")
        if args.mode != "run":
            continue

        # Backup canonical + mirror if present.
        if changed:
            _backup_file(canonical, backup_root)
            if targets.assembled.exists() and targets.assembled.resolve() != canonical.resolve():
                _backup_file(targets.assembled, backup_root)

        # Write canonical and mirror to the same content (avoid split-brain).
        if changed:
            canonical.write_text(text, encoding="utf-8")
            targets.assembled.parent.mkdir(parents=True, exist_ok=True)
            targets.assembled.write_text(text, encoding="utf-8")

        # Re-stamp alignment hashes (keeps script_validation/audio gates consistent).
        if changed or alignment_needs_restamp:
            _restamp_alignment_from_status(channel=channel, video=video, script_path=canonical)

    if args.mode == "run" and changed_any:
        print(f"[OK] Updated scripts. Backup: {backup_root}")
    elif args.mode == "run":
        print("[OK] No changes needed.")

    if bool(args.write_report):
        out_dir = logs_root() / "regression" / "a_text_repair"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = _utc_now_compact()
        payload = {
            "schema": "ytm.ops.a_text_repair_report.v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "channel": channel,
            "videos": videos,
            "mode": args.mode,
            "backup_root": str(backup_root),
            "rows": report_rows,
        }
        p = out_dir / f"a_text_repair_{channel}__{ts}.json"
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[OK] report: {p.relative_to(repo_root())}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
