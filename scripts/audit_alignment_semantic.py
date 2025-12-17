#!/usr/bin/env python3
from __future__ import annotations

"""
Audit Planning(title/thumbnail) <-> Script(A-text) *semantic* alignment (read-only).

This script does NOT modify status.json or any workspace artifacts.
It is intended to catch obvious mismatches like:
  - thumbnail prompt first-line catch differs across columns
  - title bracket-topic (【...】) never appears in the script preview
  - title/catch tokens overlap is below a configurable threshold

Examples:
  python3 scripts/audit_alignment_semantic.py --channels CH01,CH04
  python3 scripts/audit_alignment_semantic.py --channels CH01 --videos 001,002 --min-title-overlap 0.6 --ignore-bracket-in-title
  python3 scripts/audit_alignment_semantic.py --channels CH01 --min-thumb-catch-overlap 0.6
  python3 scripts/audit_alignment_semantic.py --json --out workspaces/logs/alignment_audit_semantic.json
"""

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    return cur.resolve()


REPO_ROOT = _discover_repo_root(Path(__file__).resolve())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_common import alignment  # noqa: E402
from factory_common.paths import channels_csv_path, planning_root, video_root  # noqa: E402


_TOKEN_RE = getattr(
    alignment,
    "_TOKEN_RE",
    re.compile(r"[一-龯]{2,}|[ぁ-ん]{2,}|[ァ-ヴー]{2,}|[A-Za-z0-9]{2,}"),
)
_STOPWORDS = set(getattr(alignment, "_STOPWORDS", set()) or set())
_TITLE_BRACKET_RE = getattr(alignment, "_TITLE_BRACKET_RE", re.compile(r"【([^】]+)】"))
_HIRAGANA_ONLY_RE = re.compile(r"^[ぁ-ん]+$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _norm_channel(value: str) -> str:
    return str(value or "").strip().upper()


def _norm_video(value: object) -> Optional[str]:
    raw = str(value or "").strip()
    if not raw:
        return None
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return None
    try:
        return f"{int(digits):03d}"
    except ValueError:
        return None


def _parse_videos(raw: Optional[str]) -> Optional[set[str]]:
    if raw is None:
        return None
    token = str(raw).strip()
    if not token:
        return None
    out: set[str] = set()
    for part in token.split(","):
        part = part.strip()
        if not part:
            continue
        v = _norm_video(part)
        if v:
            out.add(v)
    return out or None


def _load_csv_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _resolve_script_path(channel: str, video: str) -> Optional[Path]:
    content_dir = video_root(channel, video) / "content"
    human = content_dir / "assembled_human.md"
    if human.exists():
        return human
    assembled = content_dir / "assembled.md"
    if assembled.exists():
        return assembled
    return None


def _tokenize(text: str) -> set[str]:
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(text or ""):
        tok = raw.lower() if raw.isascii() else raw
        if tok in _STOPWORDS:
            continue
        tokens.add(tok)
    return tokens


def _drop_hiragana_only(tokens: set[str]) -> set[str]:
    return {t for t in tokens if not _HIRAGANA_ONLY_RE.match(t)}


def _strip_title_brackets(title: str) -> str:
    return _TITLE_BRACKET_RE.sub("", title or "").strip()


@dataclass(frozen=True)
class Finding:
    channel: str
    video: str
    code: str
    message: str
    title: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel,
            "video": self.video,
            "code": self.code,
            "message": self.message,
            "title": self.title,
        }


def _check_row(
    channel: str,
    row: Dict[str, str],
    *,
    min_title_overlap: Optional[float],
    min_thumb_catch_overlap: Optional[float],
    ignore_bracket_in_title: bool,
    max_missing_tokens: int,
    skip_bracket_topic: bool,
    title_tokenizer: str,
) -> List[Finding]:
    findings: List[Finding] = []
    video = _norm_video(row.get("動画番号") or row.get("No.") or row.get("No") or "")
    if not video:
        return findings

    title = str(row.get("タイトル") or "").strip()
    script_path = _resolve_script_path(channel, video)
    if not script_path:
        findings.append(
            Finding(
                channel=channel,
                video=video,
                code="script_missing",
                message="script not found (assembled_human.md / assembled.md)",
                title=title,
            )
        )
        return findings

    try:
        preview = script_path.read_text(encoding="utf-8")[:6000]
    except Exception:
        preview = ""

    # 1) Thumbnail catch mismatch (prompt first line)
    catches = {c for c in alignment.iter_thumbnail_catches_from_row(row)}
    if len(catches) > 1:
        findings.append(
            Finding(
                channel=channel,
                video=video,
                code="thumb_catch_mismatch",
                message="thumbnail prompt first-line catch differs across columns",
                title=title,
            )
        )

    # 1b) Thumbnail catch tokens overlap threshold (configurable)
    if min_thumb_catch_overlap is not None:
        catch = alignment.select_thumbnail_catch(row)
        if catch:
            catch_tokens_full = _tokenize(catch)
            drop_hira = False
            if title_tokenizer == "no_hiragana":
                drop_hira = True
            elif title_tokenizer == "auto":
                drop_hira = any(not _HIRAGANA_ONLY_RE.match(t) for t in catch_tokens_full)
            elif title_tokenizer == "full":
                drop_hira = False

            catch_tokens = _drop_hiragana_only(catch_tokens_full) if drop_hira else catch_tokens_full
            script_tokens = _tokenize(preview[:6000])
            if drop_hira:
                script_tokens = _drop_hiragana_only(script_tokens)

            if catch_tokens:
                missing = sorted(catch_tokens - script_tokens)
                overlap = catch_tokens & script_tokens
                ratio = len(overlap) / max(len(catch_tokens), 1)
                if ratio < float(min_thumb_catch_overlap):
                    miss_preview = ", ".join(missing[: max(0, int(max_missing_tokens))]) if max_missing_tokens else ""
                    if miss_preview and len(missing) > int(max_missing_tokens):
                        miss_preview += ", ..."
                    msg = f"thumbnail catch token overlap too low (overlap={ratio:.2f} missing={len(missing)})"
                    if miss_preview:
                        msg += f" missing_tokens=[{miss_preview}]"
                    findings.append(
                        Finding(
                            channel=channel,
                            video=video,
                            code="thumb_catch_overlap_low",
                            message=msg,
                            title=title,
                        )
                    )

    # 2) Bracket topic mismatch (【...】)
    if not skip_bracket_topic and title and not alignment.bracket_topic_overlaps(title, preview):
        ratio = alignment.title_script_token_overlap_ratio(title, preview)
        findings.append(
            Finding(
                channel=channel,
                video=video,
                code="title_bracket_topic_missing",
                message=f"title bracket-topic tokens missing in script preview (overlap={ratio:.2f})",
                title=title,
            )
        )

    # 3) Title tokens overlap threshold (configurable)
    if min_title_overlap is not None and title:
        title_for_check = _strip_title_brackets(title) if ignore_bracket_in_title else title
        title_tokens_full = _tokenize(title_for_check)

        drop_hira = False
        if title_tokenizer == "no_hiragana":
            drop_hira = True
        elif title_tokenizer == "auto":
            drop_hira = any(not _HIRAGANA_ONLY_RE.match(t) for t in title_tokens_full)
        elif title_tokenizer == "full":
            drop_hira = False

        title_tokens = _drop_hiragana_only(title_tokens_full) if drop_hira else title_tokens_full
        script_tokens = _tokenize(preview[:6000])
        if drop_hira:
            script_tokens = _drop_hiragana_only(script_tokens)

        if title_tokens:
            missing = sorted(title_tokens - script_tokens)
            overlap = title_tokens & script_tokens
            ratio = len(overlap) / max(len(title_tokens), 1)
            if ratio < float(min_title_overlap):
                miss_preview = ", ".join(missing[: max(0, int(max_missing_tokens))]) if max_missing_tokens else ""
                if miss_preview and len(missing) > int(max_missing_tokens):
                    miss_preview += ", ..."
                msg = f"title token overlap too low (overlap={ratio:.2f} missing={len(missing)})"
                if miss_preview:
                    msg += f" missing_tokens=[{miss_preview}]"
                findings.append(
                    Finding(
                        channel=channel,
                        video=video,
                        code="title_token_overlap_low",
                        message=msg,
                        title=title,
                    )
                )

    return findings


def _iter_channels(selected: Optional[set[str]]) -> Iterable[str]:
    root = planning_root() / "channels"
    for p in sorted(root.glob("CH*.csv")):
        ch = p.stem.upper()
        if selected and ch not in selected:
            continue
        yield ch


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit semantic alignment between planning and scripts (read-only).")
    ap.add_argument("--channels", help="Comma-separated channel codes (e.g. CH01,CH04). Omit for all.")
    ap.add_argument("--videos", help="Comma-separated video numbers (e.g. 001,002,028). Omit for all.")
    ap.add_argument("--limit", type=int, help="Stop after N findings (for quick checks).")
    ap.add_argument("--json", action="store_true", help="Emit JSON payload to stdout.")
    ap.add_argument("--out", help="Write JSON report to file path (in addition to stdout output).")
    ap.add_argument(
        "--min-title-overlap",
        type=float,
        default=None,
        help="Optional: flag when (title token overlap ratio) < threshold. Example: 1.0 enforces 'title tokens must appear'.",
    )
    ap.add_argument(
        "--min-thumb-catch-overlap",
        type=float,
        default=None,
        help="Optional: flag when (thumbnail catch token overlap ratio) < threshold.",
    )
    ap.add_argument(
        "--ignore-bracket-in-title",
        action="store_true",
        help="When computing --min-title-overlap, ignore 【...】 segment in title (clickbait tags etc).",
    )
    ap.add_argument(
        "--max-missing-tokens",
        type=int,
        default=12,
        help="For overlap findings, include up to N missing tokens in message (default: 12).",
    )
    ap.add_argument(
        "--skip-bracket-topic",
        action="store_true",
        help="Skip the dedicated 【...】 bracket-topic check (useful when it is too noisy).",
    )
    ap.add_argument(
        "--title-tokenizer",
        choices=["auto", "full", "no_hiragana"],
        default="auto",
        help="Tokenizer mode for overlap checks (default: auto).",
    )
    args = ap.parse_args()

    selected = None
    if args.channels:
        selected = {_norm_channel(x) for x in str(args.channels).split(",") if x.strip()}
    selected_videos = _parse_videos(args.videos)

    findings: List[Finding] = []
    scanned = 0
    considered = 0
    for ch in _iter_channels(selected):
        csv_path = channels_csv_path(ch)
        if not csv_path.exists():
            continue
        try:
            rows = _load_csv_rows(csv_path)
        except Exception:
            continue
        for row in rows:
            scanned += 1
            video = _norm_video(row.get("動画番号") or row.get("No.") or row.get("No") or "")
            if selected_videos and (not video or video not in selected_videos):
                continue
            considered += 1

            for finding in _check_row(
                ch,
                row,
                min_title_overlap=args.min_title_overlap,
                min_thumb_catch_overlap=args.min_thumb_catch_overlap,
                ignore_bracket_in_title=bool(args.ignore_bracket_in_title),
                max_missing_tokens=int(args.max_missing_tokens),
                skip_bracket_topic=bool(args.skip_bracket_topic),
                title_tokenizer=str(args.title_tokenizer),
            ):
                findings.append(finding)
                if args.limit is not None and len(findings) >= int(args.limit):
                    break
            if args.limit is not None and len(findings) >= int(args.limit):
                break
        if args.limit is not None and len(findings) >= int(args.limit):
            break

    payload = {
        "generated_at": _utc_now_iso(),
        "planning_root": str(planning_root()),
        "scanned_rows": scanned,
        "considered_rows": considered,
        "filters": {
            "channels": sorted(selected) if selected else None,
            "videos": sorted(selected_videos) if selected_videos else None,
        },
        "findings": [f.as_dict() for f in findings],
        "counts": {
            "total": len(findings),
            "by_code": {
                code: sum(1 for f in findings if f.code == code)
                for code in sorted({f.code for f in findings})
            },
        },
    }

    if args.out:
        out_path = Path(str(args.out)).expanduser()
        if not out_path.is_absolute():
            out_path = REPO_ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"[alignment_audit] scanned_rows={scanned} considered_rows={considered} "
            f"findings={len(findings)} planning_root={payload['planning_root']}"
        )
        for f in findings[:200]:
            print(f"- {f.channel}-{f.video} {f.code}: {f.message} ({f.title})")
        if len(findings) > 200:
            print(f"... ({len(findings) - 200} more)")

    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())

