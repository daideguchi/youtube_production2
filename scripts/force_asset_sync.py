#!/usr/bin/env python3
"""Force asset-driven status reconciliation for script_pipeline.

Legacy repair-status flow is removed; this script now:
- Reads `workspaces/planning/channels/*.csv`
- For videos that already have `workspaces/scripts/{CH}/{NNN}/status.json`:
  - (default) runs `script_pipeline.runner.reconcile_status` (best-effort, allow downgrade)
  - validates completed-stage outputs (`script_pipeline.validator.validate_completed_outputs`)

`--dry-run` skips reconciliation and only validates.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from _bootstrap import bootstrap

REPO_ROOT = bootstrap()

from factory_common.paths import logs_root, planning_root

from script_pipeline.runner import reconcile_status, _load_stage_defs  # type: ignore
from script_pipeline.sot import status_path
from script_pipeline.validator import validate_completed_outputs

LOG_ROOT = logs_root() / "regression"


def _normalize_channel(code: str) -> str:
    return (code or "").strip().upper()


def _normalize_video(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    token = str(value).strip()
    if not token:
        return None
    digits = "".join(ch for ch in token if ch.isdigit())
    if digits:
        return f"{int(digits):03d}"
    return token


def _planning_sources() -> List[Path]:
    return sorted((planning_root() / "channels").glob("*.csv"))


def _iter_planning_rows(
    *,
    channel_filter: Optional[Iterable[str]] = None,
    exclude_channels: Optional[Iterable[str]] = None,
    video_filter: Optional[str] = None,
) -> Iterator[Tuple[int, Dict[str, str]]]:
    channels = {_normalize_channel(code or "") for code in (channel_filter or []) if code}
    excludes = {_normalize_channel(code or "") for code in (exclude_channels or []) if code}
    video = _normalize_video(video_filter)

    for csv_path in _planning_sources():
        channel = csv_path.stem.upper()
        if channels and channel not in channels:
            continue
        if excludes and channel in excludes:
            continue
        try:
            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                for row_no, row in enumerate(reader, start=2):  # header=1
                    video_number = _normalize_video(row.get("動画番号") or row.get("VideoNumber") or row.get("No."))
                    if not video_number:
                        continue
                    if video and video_number != video:
                        continue
                    yield row_no, {
                        "channel_code": channel,
                        "video_number": video_number,
                        "row_number": str(row_no),
                        "title": (row.get("タイトル") or row.get("title") or "").strip(),
                        "progress": (row.get("進捗") or row.get("progress") or "").strip(),
                    }
        except FileNotFoundError:
            continue


def main() -> int:
    parser = argparse.ArgumentParser(description="Force asset state onto script_pipeline status.json (reconcile + validate)")
    parser.add_argument("--channel-code", help="対象チャンネル (例: CH06)。省略時は全件")
    parser.add_argument("--exclude-channel", action="append", help="除外するチャンネルコード（繰り返し可）")
    parser.add_argument("--video-number", help="特定動画番号 (例: 016)。チャンネル指定が必要")
    parser.add_argument("--limit", type=int, help="上限件数")
    parser.add_argument("--dry-run", action="store_true", help="reconcile を実行せず validate のみ")
    args = parser.parse_args()

    targets = list(
        _iter_planning_rows(
            channel_filter=[args.channel_code] if args.channel_code else None,
            exclude_channels=args.exclude_channel,
            video_filter=args.video_number,
        )
    )
    if args.limit is not None:
        targets = targets[: args.limit]

    if not targets:
        print("該当する行がありません。")
        return 0

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_ROOT / f"asset_sync_{timestamp}.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    stage_defs = _load_stage_defs()
    results: List[Dict[str, object]] = []
    repairs: List[Dict[str, str]] = []
    skipped = 0

    for _, meta in targets:
        ch = meta["channel_code"]
        no = meta["video_number"]
        p = status_path(ch, no)
        if not p.exists():
            skipped += 1
            results.append(
                {
                    "channel_code": ch,
                    "video_number": no,
                    "skipped": True,
                    "reason": "status.json missing",
                    "status_path": str(p),
                    "planning_row": {
                        "row_number": meta["row_number"],
                        "title": meta["title"],
                        "progress": meta["progress"],
                    },
                }
            )
            continue

        before_status = None
        try:
            before_status = json.loads(p.read_text(encoding="utf-8")).get("status")
        except Exception:
            before_status = None

        if not args.dry_run:
            st = reconcile_status(ch, no, allow_downgrade=True)
            after_status = st.status
            if before_status and after_status != before_status:
                repairs.append(
                    {
                        "channel_code": ch,
                        "video_number": no,
                        "before": str(before_status),
                        "after": str(after_status),
                    }
                )

        issues = validate_completed_outputs(ch, no, stage_defs)
        results.append(
            {
                "channel_code": ch,
                "video_number": no,
                "status_path": str(p),
                "success": not issues,
                "issues": issues,
                "planning_row": {
                    "row_number": meta["row_number"],
                    "title": meta["title"],
                    "progress": meta["progress"],
                },
            }
        )

    failures = [r for r in results if r.get("success") is False]
    artifact = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        "dry_run": bool(args.dry_run),
        "planning_sources": [str(path) for path in _planning_sources()],
        "repairs": repairs,
        "results": results,
        "summary": {
            "total": len(results),
            "checked": len(results) - skipped,
            "skipped": skipped,
            "failures": len(failures),
            "repairs_applied": len(repairs),
        },
    }
    log_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Completed {len(results)} rows (checked={len(results)-skipped}, failures={len(failures)}, skipped={skipped}).")
    print(f"Log: {log_path}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
