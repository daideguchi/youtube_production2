#!/usr/bin/env python3
"""
Sanitize Planning CSV rows to reduce human/agent confusion from "L3" contaminated fields.

This tool is deterministic (no LLM) and intentionally conservative:
- It does NOT rewrite titles or planning intent.
- It only clears known contamination signals inside L3-ish free-text columns
  (e.g. '台本本文（冒頭サンプル）' containing a canned opener from another channel).

Why:
- L3 fields are not inputs to A-text generation (see SSOT input contract),
  but they easily mislead humans/agents and re-introduce canned openers.

SSOT:
- ssot/ops/OPS_SCRIPT_INPUT_CONTRACT.md (L1/L2/L3)
- ssot/ops/OPS_PLANNING_CSV_WORKFLOW.md

Usage:
  python scripts/ops/planning_sanitize.py --channel CH07 --apply --write-latest
  python scripts/ops/planning_sanitize.py --csv workspaces/planning/channels/CH07.csv --apply
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from _bootstrap import bootstrap

PROJECT_ROOT = bootstrap(load_env=False)

from factory_common.paths import logs_root, planning_root, repo_root
from factory_common.locks import default_active_locks_for_mutation, find_blocking_lock


_DEEPNIGHT_OPENER_RE = re.compile(r"深夜の偉人ラジオへようこそ")
_BULLET_LIKE_RE = re.compile(r"^\s*[-*•]|^\s*・\s+", flags=re.MULTILINE)


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


def _planning_csv_path(channel: str) -> Path:
    return planning_root() / "channels" / f"{channel}.csv"


def _video_number_from_row(row: dict[str, str]) -> str:
    for key in ("動画番号", "No.", "VideoNumber", "video_number", "video"):
        if key in row and (row.get(key) or "").strip():
            raw = (row.get(key) or "").strip()
            try:
                return f"{int(raw):03d}"
            except Exception:
                return raw
    for key in ("動画ID", "台本番号", "ScriptID", "script_id"):
        v = (row.get(key) or "").strip()
        m = re.search(r"\bCH\d{2}-(\d{3})\b", v)
        if m:
            return m.group(1)
    return "???"


def _is_l3_like_column(col: str) -> bool:
    c = (col or "").strip()
    if not c:
        return False
    if c in {"script_sample", "script_body"}:
        return True
    # Japanese columns commonly used as free-text script samples.
    if "台本本文" in c:
        return True
    if "冒頭サンプル" in c:
        return True
    return False


def _should_clear_value(val: str) -> bool:
    s = str(val or "")
    if not s.strip():
        return False
    if _DEEPNIGHT_OPENER_RE.search(s):
        return True
    if _BULLET_LIKE_RE.search(s):
        return True
    return False


@dataclass(frozen=True)
class Change:
    row_index: int
    channel: str
    video: str
    column: str
    reason: str
    before_excerpt: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_index": self.row_index,
            "channel": self.channel,
            "video": self.video,
            "column": self.column,
            "reason": self.reason,
            "before_excerpt": self.before_excerpt,
        }


def sanitize_csv(csv_path: Path, channel: str) -> tuple[list[dict[str, str]], list[str], list[Change]]:
    with csv_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = list(reader.fieldnames or [])
        rows = list(reader)

    changes: list[Change] = []
    l3_cols = [h for h in headers if _is_l3_like_column(h)]
    if not l3_cols:
        return rows, headers, changes

    for idx, row in enumerate(rows, start=1):
        video = _video_number_from_row(row)
        for col in l3_cols:
            raw = str(row.get(col) or "")
            if not _should_clear_value(raw):
                continue
            reason = "contains_deepnight_radio_opener" if _DEEPNIGHT_OPENER_RE.search(raw) else "contains_bullet_like_text"
            excerpt = raw.strip().replace("\n", " ")[:120]
            changes.append(
                Change(
                    row_index=idx,
                    channel=channel,
                    video=video,
                    column=col,
                    reason=reason,
                    before_excerpt=excerpt,
                )
            )
            row[col] = ""

    return rows, headers, changes


def _write_report(channel: str, csv_path: Path, changes: list[Change], *, write_latest: bool) -> tuple[Path, Path]:
    out_dir = logs_root() / "regression" / "planning_sanitize"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = _utc_now_compact()
    json_path = out_dir / f"planning_sanitize_{channel}__{ts}.json"
    md_path = out_dir / f"planning_sanitize_{channel}__{ts}.md"

    payload: dict[str, Any] = {
        "schema": "ytm.planning_sanitize.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "channel": channel,
        "csv_path": str(csv_path),
        "changes": [c.as_dict() for c in changes],
        "changed_rows": len({(c.row_index) for c in changes}),
        "changed_cells": len(changes),
        "ok": True,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines: list[str] = []
    lines.append(f"# planning_sanitize report: {channel}")
    lines.append("")
    lines.append(f"- generated_at: {payload['generated_at']}")
    lines.append(f"- csv_path: {payload['csv_path']}")
    lines.append(f"- changed_rows: {payload['changed_rows']}")
    lines.append(f"- changed_cells: {payload['changed_cells']}")
    lines.append("")
    if changes:
        lines.append("## Changes (first 80)")
        for c in changes[:80]:
            lines.append(
                f"- {c.channel}/{c.video} row={c.row_index} col={c.column}: {c.reason} (before='{c.before_excerpt}')"
            )
    else:
        lines.append("## Changes")
        lines.append("- (none)")
    md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    if write_latest:
        (out_dir / f"planning_sanitize_{channel}__latest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (out_dir / f"planning_sanitize_{channel}__latest.md").write_text(
            "\n".join(lines).rstrip() + "\n", encoding="utf-8"
        )
    return json_path, md_path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--channel", default="", help="Channel code like CH07")
    ap.add_argument("--csv", default="", help="Explicit CSV path (overrides --channel)")
    ap.add_argument("--apply", action="store_true", help="Rewrite the CSV in-place")
    ap.add_argument("--write-latest", action="store_true", help="Also write *_latest.json/md")
    args = ap.parse_args(argv)

    channel = _normalize_channel(args.channel) if args.channel else ""
    csv_path = Path(args.csv).expanduser() if args.csv else None

    if csv_path is None:
        if not channel:
            print("ERROR: Provide --channel CHxx or --csv <path>", file=sys.stderr)
            return 2
        csv_path = _planning_csv_path(channel)
    else:
        if not channel:
            channel = _normalize_channel(csv_path.stem)

    if not csv_path.exists():
        print(f"ERROR: CSV not found: {csv_path}", file=sys.stderr)
        return 2

    rows, headers, changes = sanitize_csv(csv_path, channel)
    _write_report(channel, csv_path, changes, write_latest=bool(args.write_latest))

    if not args.apply:
        print(f"DRY-RUN: {channel} changes={len(changes)} (use --apply to rewrite CSV)")
        return 0

    locks = default_active_locks_for_mutation()
    blocking = find_blocking_lock(csv_path, locks)
    if blocking:
        print(
            f"ERROR: blocked by lock: {blocking.lock_id} created_by={blocking.created_by} scopes={blocking.scopes}",
            file=sys.stderr,
        )
        return 3

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"OK: wrote {csv_path} (changes={len(changes)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
