#!/usr/bin/env python3
"""
Scan A-text script progress (assembled.md) for CH27-CH31 and write a manifest JSON.

Why:
- "どこまで仕上がってるか" は作業中にどんどん変わるため、ファイル実体から毎回再計算する。
- antigravity 手動/半自動運用の「再開点」を固定し、未完/不合格だけを確実に叩けるようにする。

Output:
- JSON manifest describing per-video status based on simple, deterministic checks.

Notes:
- This is not SSOT. SSOT is still:
  - `workspaces/scripts/{CH}/{NNN}/content/assembled_human.md` (if present)
  - otherwise `workspaces/scripts/{CH}/{NNN}/content/assembled.md`
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common import paths as repo_paths  # noqa: E402


DEFAULT_CHANNELS = ["CH27", "CH28", "CH29", "CH30", "CH31"]
DEFAULT_VIDEOS = [str(i).zfill(3) for i in range(1, 31)]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _non_space_chars(text: str) -> int:
    return len("".join(ch for ch in (text or "") if not ch.isspace()))


def _detect_issues(text: str) -> List[str]:
    """
    Deterministic, conservative checks. Keep this aligned with prompt constraints:
    - A-text only (no headings/bullets/URLs/parentheses)
    - Pause marker is `---` only, line-alone
    - Ban punctuation-only padding (e.g. 、。 spam)
    """
    t = text or ""
    issues: List[str] = []

    # URLs
    if re.search(r"https?://", t):
        issues.append("has_url")

    # Parentheses (both half/full width)
    if re.search(r"[()（）]", t):
        issues.append("has_parentheses")

    # Markdown headings / bullets / list-like lines
    if re.search(r"^\s*#{1,6}\s+\S", t, flags=re.M):
        issues.append("has_heading")
    if re.search(r"^\s*[-*]\s+\S", t, flags=re.M):
        issues.append("has_bullets")
    if re.search(r"^\s*・\s*\S", t, flags=re.M):
        issues.append("has_list_dot")
    if re.search(r"^\s*\d+\.\s+\S", t, flags=re.M):
        issues.append("has_numbered_list")
    if re.search(r"^\s*\d+\)\s+\S", t, flags=re.M):
        issues.append("has_numbered_list_paren")

    # Bad separators
    if re.search(r"^\s*(\*{3,}|_{3,}|={3,}|/{3,})\s*$", t, flags=re.M):
        issues.append("has_bad_separator")

    # Pause marker must be exactly '---' on its own line if used.
    for line in t.splitlines():
        if "---" not in line:
            continue
        if line.strip() != "---":
            issues.append("bad_pause_marker_usage")
            break

    # Punctuation padding (punctuation-only lines or long runs)
    punct_only_allowed = set("、。・…!?！？")
    for line in t.splitlines():
        s = line.strip()
        if not s:
            continue
        if all(ch in punct_only_allowed for ch in s):
            issues.append("punct_only_line")
            break
    if re.search(r"(、。){3,}|(、){12,}|(。){12,}|(…){10,}", t):
        issues.append("punct_run")

    # Truncation (very rough): must end with a Japanese punctuation mark.
    last = (t.rstrip()[-1:] or "")
    if last and last not in "。！？":
        issues.append("not_terminated")

    return sorted(set(issues))


def _status_from(*, exists: bool, chars: int, issues: Sequence[str], min_chars: int, max_chars: int) -> str:
    if not exists:
        return "missing"
    if chars <= 0:
        return "empty"
    if "punct_only_line" in issues or "punct_run" in issues:
        return "needs_rebuild_punct"
    if any(
        x in issues
        for x in (
            "has_url",
            "has_parentheses",
            "has_heading",
            "has_bullets",
            "has_list_dot",
            "has_numbered_list",
            "has_numbered_list_paren",
            "has_bad_separator",
            "bad_pause_marker_usage",
        )
    ):
        return "needs_rebuild_forbidden"
    if chars < min_chars:
        return "needs_extend"
    if chars > max_chars:
        return "needs_shorten"
    if "not_terminated" in issues:
        return "needs_fix_ending"
    return "ok"


@dataclass(frozen=True)
class Item:
    id: str
    channel: str
    video: str
    path: str
    exists: bool
    chars: int
    issues: List[str]
    status: str


def _iter_targets(channels: Sequence[str], videos: Sequence[str]) -> Iterable[tuple[str, str]]:
    for ch in channels:
        for v in videos:
            yield ch, v


def _assembled_paths(channel: str, video: str) -> List[Path]:
    """
    Prefer assembled_human if present (SoT), but always report assembled.md too.
    """
    base = repo_paths.video_root(channel, video) / "content"
    return [base / "assembled_human.md", base / "assembled.md"]


def build_manifest(*, channels: Sequence[str], videos: Sequence[str], min_chars: int, max_chars: int) -> Dict[str, object]:
    items: List[Item] = []
    for ch, v in _iter_targets(channels, videos):
        paths = _assembled_paths(ch, v)

        # Choose best existing path for status (human preferred).
        chosen: Path = paths[-1]  # assembled.md fallback
        for p in paths:
            if p.exists():
                chosen = p
                break

        exists = chosen.exists()
        text = chosen.read_text(encoding="utf-8") if exists else ""
        chars = _non_space_chars(text) if exists else 0
        issues = _detect_issues(text) if exists else []
        status = _status_from(exists=exists, chars=chars, issues=issues, min_chars=min_chars, max_chars=max_chars)

        items.append(
            Item(
                id=f"{ch}-{v}",
                channel=ch,
                video=v,
                path=str(chosen.as_posix()),
                exists=exists,
                chars=chars,
                issues=list(issues),
                status=status,
            )
        )

    summary: Dict[str, int] = {}
    for it in items:
        summary[it.status] = summary.get(it.status, 0) + 1

    return {
        "schema_version": 1,
        "updated_at": _utc_now_iso(),
        "scope": {"channels": list(channels), "videos": list(videos)},
        "quality_gate": {"min_chars": int(min_chars), "max_chars": int(max_chars)},
        "summary": dict(sorted(summary.items(), key=lambda kv: (-kv[1], kv[0]))),
        "items": [asdict(x) for x in items],
    }


def _parse_csv_list(raw: str) -> List[str]:
    return [x.strip() for x in (raw or "").split(",") if x.strip()]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--channels", default=",".join(DEFAULT_CHANNELS), help="Comma-separated channels (default: CH27-CH31)")
    p.add_argument("--videos", default="001-030", help="Video range '001-030' or comma list '001,002,...'")
    p.add_argument(
        "--min-chars",
        type=int,
        default=6000,
        help="Minimum non-space characters for OK (default: 6000)",
    )
    p.add_argument(
        "--max-chars",
        type=int,
        default=8000,
        help="Maximum non-space characters for OK (default: 8000)",
    )
    p.add_argument(
        "--out",
        default=str((repo_paths.workspace_root() / "scripts" / "_state" / "antigravity_ch27_31_progress.json").as_posix()),
        help="Output manifest path (default under workspaces/scripts/_state)",
    )
    return p.parse_args()


def _parse_videos(raw: str) -> List[str]:
    s = (raw or "").strip()
    if not s:
        return list(DEFAULT_VIDEOS)
    if "-" in s and "," not in s:
        a, b = [x.strip() for x in s.split("-", 1)]
        lo = int(a)
        hi = int(b)
        if hi < lo:
            lo, hi = hi, lo
        return [str(i).zfill(3) for i in range(lo, hi + 1)]
    # comma list
    out: List[str] = []
    for tok in _parse_csv_list(s):
        out.append(str(int(tok)).zfill(3) if tok.isdigit() else tok.zfill(3))
    return out


def main() -> None:
    args = parse_args()
    channels = [c.upper() for c in _parse_csv_list(args.channels)] or list(DEFAULT_CHANNELS)
    videos = _parse_videos(args.videos)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = build_manifest(channels=channels, videos=videos, min_chars=int(args.min_chars), max_chars=int(args.max_chars))
    out_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote: {out_path}")
    print("[summary]", manifest.get("summary"))


if __name__ == "__main__":
    main()

