#!/usr/bin/env python3
from __future__ import annotations

"""
Audit Planning(title/thumbnail) <-> Script(A-text) *semantic* alignment (read-only).

This script does NOT modify status.json or any workspace artifacts.
It is intended to catch obvious mismatches like:
  - thumbnail prompt first-line catch differs across columns
  - script missing for a planned episode (assembled_human.md / assembled.md)

Examples:
  python3 scripts/audit_alignment_semantic.py --channels CH01,CH04
  python3 scripts/audit_alignment_semantic.py --json --out workspaces/logs/alignment_audit_semantic.json
"""

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from _bootstrap import bootstrap


bootstrap(load_env=False)

from factory_common import alignment  # noqa: E402
from factory_common.paths import channels_csv_path, planning_root, video_root  # noqa: E402


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
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
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
