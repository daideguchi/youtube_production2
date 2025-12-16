#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
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

from factory_common.paths import repo_root, video_root  # noqa: E402

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore


@dataclass(frozen=True)
class LintResult:
    channel: str
    video: str
    path: Path
    chars_no_newlines: int
    quote_count: int
    paren_count: int
    issues: List[str]
    warnings: List[str]


_RE_URL = re.compile(r"https?://\S+|\bwww\.[^\s)）\]】」』<>]+")
_RE_FOOTNOTE_ANY = re.compile(r"\[(\d+)\]")
_RE_BULLET = re.compile(r"^\s*(?:[-*+]\s+|・\s*|\d+[.)]\s+|\d+）\s+)")
_RE_OTHER_SEP = re.compile(r"^\s*(?:\*{3,}|_{3,}|/{3,}|={3,})\s*$")
_RE_HYPHEN_ONLY = re.compile(r"^\s*[-\s]+\s*$")
_RE_HEADING = re.compile(r"^\s*#{1,6}\s+")


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


def _parse_videos(raw: str) -> List[str]:
    if not raw:
        return []
    out: List[str] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        out.append(_norm_video(part))
    return sorted(set(out))


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


def _channel_targets(channel: str, sources: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    channels_cfg = sources.get("channels")
    if not isinstance(channels_cfg, dict):
        return None, None
    ch_cfg = channels_cfg.get(channel)
    if not isinstance(ch_cfg, dict):
        return None, None
    tmin = ch_cfg.get("target_chars_min")
    tmax = ch_cfg.get("target_chars_max")
    try:
        tmin = int(tmin) if tmin is not None and str(tmin).strip() else None
    except Exception:
        tmin = None
    try:
        tmax = int(tmax) if tmax is not None and str(tmax).strip() else None
    except Exception:
        tmax = None
    return tmin, tmax


def _style_limits(channel: str, sources: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
    sg = sources.get("script_globals") if isinstance(sources.get("script_globals"), dict) else {}
    channels_cfg = sources.get("channels")
    ch_cfg = channels_cfg.get(channel) if isinstance(channels_cfg, dict) else None
    ch_cfg = ch_cfg if isinstance(ch_cfg, dict) else {}

    def _as_int(value: Any) -> Optional[int]:
        try:
            return int(value) if value is not None and str(value).strip() else None
        except Exception:
            return None

    max_quotes = _as_int(ch_cfg.get("a_text_quote_marks_max")) or _as_int(sg.get("a_text_quote_marks_max"))
    max_parens = _as_int(ch_cfg.get("a_text_paren_marks_max")) or _as_int(sg.get("a_text_paren_marks_max"))
    return max_quotes, max_parens


def _canonical_a_text_path(base: Path) -> Path:
    human = base / "content" / "assembled_human.md"
    return human if human.exists() else (base / "content" / "assembled.md")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")


def lint_one(channel: str, video: str, *, sources: Dict[str, Any], min_chars: Optional[int] = None) -> LintResult:
    base = video_root(channel, video)
    a_path = _canonical_a_text_path(base)
    issues: List[str] = []
    warnings: List[str] = []

    if not a_path.exists():
        return LintResult(channel, video, a_path, 0, 0, 0, [f"missing A-text: {a_path}"], warnings)

    text = _read_text(a_path)
    chars_no_newlines = len(text.replace("\n", ""))

    quote_count = text.count("「") + text.count("」")
    paren_count = text.count("（") + text.count("）") + text.count("(") + text.count(")")

    cfg_min, cfg_max = _channel_targets(channel, sources)
    max_quotes, max_parens = _style_limits(channel, sources)
    effective_min = min_chars if min_chars is not None else cfg_min
    if effective_min is not None and chars_no_newlines < effective_min:
        issues.append(f"too short: {chars_no_newlines} < {effective_min} (chars, excluding newlines)")
    if cfg_max is not None and chars_no_newlines > cfg_max:
        warnings.append(f"too long: {chars_no_newlines} > {cfg_max} (chars, excluding newlines)")

    if _RE_URL.search(text):
        issues.append("contains URL (forbidden in A-text)")
    if _RE_FOOTNOTE_ANY.search(text):
        issues.append("contains footnote-like [number] tokens (forbidden in A-text)")

    for i, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        if _RE_HEADING.match(stripped):
            issues.append(f"heading line detected at L{i}: {stripped[:60]}")
            break
        if _RE_BULLET.match(stripped):
            issues.append(f"bullet/list line detected at L{i}: {stripped[:60]}")
            break
        if _RE_OTHER_SEP.match(stripped):
            issues.append(f"forbidden separator detected at L{i}: {stripped}")
            break
        if _RE_HYPHEN_ONLY.match(stripped):
            compact = re.sub(r"\s+", "", stripped)
            if compact != "---":
                issues.append(f"invalid hyphen separator at L{i}: '{stripped}' (only '---' allowed)")
                break

    if chars_no_newlines > 0:
        if max_quotes is not None:
            if quote_count > max_quotes:
                warnings.append(f"quote marks over limit: {quote_count} > {max_quotes} (consider reducing '「」')")
        elif quote_count > max(20, int(chars_no_newlines / 200)):
            warnings.append(f"high quote usage: {quote_count} (consider reducing '「」')")

        if max_parens is not None:
            if paren_count > max_parens:
                warnings.append(f"parentheses marks over limit: {paren_count} > {max_parens} (consider reducing '（）')")
        elif paren_count > max(10, int(chars_no_newlines / 400)):
            warnings.append(f"high parentheses usage: {paren_count} (consider reducing '（）')")

    return LintResult(channel, video, a_path, chars_no_newlines, quote_count, paren_count, issues, warnings)


def main() -> int:
    ap = argparse.ArgumentParser(description="Lint A-text (assembled_human/assembled) for TTS-safe global rules")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--videos", required=True, help="Comma-separated videos, e.g. 001,002,028")
    ap.add_argument("--min-chars", type=int, help="Override minimum chars (excluding newlines)")
    args = ap.parse_args()

    channel = _norm_channel(args.channel)
    videos = _parse_videos(args.videos)
    if not videos:
        raise SystemExit("no videos specified")

    sources = _load_sources()
    any_error = False

    for v in videos:
        res = lint_one(channel, v, sources=sources, min_chars=args.min_chars)
        status = "OK"
        if res.issues:
            status = "ERROR"
            any_error = True
        elif res.warnings:
            status = "WARN"

        try:
            rel = str(res.path.resolve().relative_to(repo_root()))
        except Exception:
            rel = str(res.path)

        print(
            f"[{status}] {channel}-{v} chars={res.chars_no_newlines} "
            f"quotes={res.quote_count} parens={res.paren_count} path={rel}"
        )
        for msg in res.issues:
            print(f"  - {msg}")
        for msg in res.warnings:
            print(f"  - {msg}")

    return 1 if any_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
