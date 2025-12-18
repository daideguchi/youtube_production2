#!/usr/bin/env python3
"""Validate script_pipeline status.json + required outputs across planning CSV.

This replaces the previous validate-status sweep.
It only checks videos that already have `workspaces/scripts/{CH}/{NNN}/status.json`.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from _bootstrap import bootstrap

REPO_ROOT = bootstrap()

from factory_common.paths import logs_root, planning_root

from script_pipeline.runner import reconcile_status, _load_stage_defs  # type: ignore
from script_pipeline.sot import status_path
from script_pipeline.validator import validate_completed_outputs


def _normalize_channel(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value.strip().upper() or None


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


def _build_summary(records: List[Dict]) -> Dict[str, object]:
    total = len(records)
    skipped = sum(1 for item in records if item.get("skipped"))
    success = sum(1 for item in records if item.get("success"))
    per_channel = Counter(item.get("channel_code") for item in records if item.get("channel_code"))
    failures_detail = [
        {
            "channel_code": item.get("channel_code"),
            "video_number": item.get("video_number"),
            "issues": item.get("issues") or [],
        }
        for item in records
        if item.get("success") is False and not item.get("skipped")
    ]
    return {
        "total": total,
        "checked": total - skipped,
        "skipped": skipped,
        "success": success,
        "failures": len(failures_detail),
        "per_channel_counts": dict(per_channel),
        "failures_detail": failures_detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sweep validate status.json across planning CSV")
    parser.add_argument("--channel-code", action="append", help="Channel code filter (repeatable)")
    parser.add_argument("--video-number", help="Video number filter (requires channel filter)")
    parser.add_argument("--exclude-channel", action="append", help="Channel codes to skip (repeatable)")
    parser.add_argument("--context", default="sweep", help="Context label recorded in guard logs")
    parser.add_argument("--limit", type=int, help="Stop after N rows")
    parser.add_argument("--output", help="Explicit output path")
    parser.add_argument(
        "--repair-global",
        action="store_true",
        help="Reconcile status.json before validation (best-effort, allow downgrade)",
    )
    args = parser.parse_args()

    targets = list(
        _iter_planning_rows(
            channel_filter=args.channel_code,
            exclude_channels=args.exclude_channel,
            video_filter=args.video_number,
        )
    )
    if args.limit is not None:
        targets = targets[: args.limit]
    if not targets:
        print("No planning rows matched the provided filters.")
        return 0

    stage_defs = _load_stage_defs()
    root_log_dir = logs_root()
    root_log_dir.mkdir(parents=True, exist_ok=True)
    log_dir = root_log_dir / "regression" / "validate_status"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = Path(args.output) if args.output else (log_dir / f"validate_status_full_{timestamp}.json")

    results: List[Dict] = []
    repairs: List[Dict[str, str]] = []
    for _, meta in targets:
        ch = meta["channel_code"]
        no = meta["video_number"]
        p = status_path(ch, no)
        if not p.exists():
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

        if args.repair_global:
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
        try:
            after_status_value = json.loads(p.read_text(encoding="utf-8")).get("status")
        except Exception:
            after_status_value = None

        issues = validate_completed_outputs(ch, no, stage_defs)
        success = not issues
        results.append(
            {
                "channel_code": ch,
                "video_number": no,
                "status_path": str(p),
                "global_status_before": before_status,
                "global_status_after": after_status_value,
                "success": success,
                "issues": issues,
                "planning_row": {
                    "row_number": meta["row_number"],
                    "title": meta["title"],
                    "progress": meta["progress"],
                },
            }
        )

    summary = _build_summary(results)
    artifact = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "context": args.context,
        "planning_sources": [str(path) for path in _planning_sources()],
        "repairs": repairs,
        "results": results,
        "summary": summary,
    }
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = root_log_dir / "validate_status_full_latest.json"
    latest_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== validate-status sweep summary ===")
    print(f"Rows     : {summary['total']}")
    print(f"Checked  : {summary['checked']}")
    print(f"Skipped  : {summary['skipped']}")
    print(f"Success  : {summary['success']}")
    print(f"Failures : {len(summary['failures_detail'])}")
    if summary["failures_detail"]:
        print("\nFailures:")
        for item in summary["failures_detail"][:10]:
            ch = item.get("channel_code")
            vid = item.get("video_number")
            issues = "; ".join(item.get("issues") or [])
            print(f" - {ch}-{vid}: {issues}")
    print(f"\nOutput   : {output_path}")
    print(f"Latest   : {latest_path}")

    return 1 if summary["failures_detail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
