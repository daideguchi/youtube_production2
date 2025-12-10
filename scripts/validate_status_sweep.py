#!/usr/bin/env python3
"""Run progress_manager validate-status across channels CSV."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMENTARY_ROOT = PROJECT_ROOT / "commentary_01_srtfile_v2"
DATA_ROOT = COMMENTARY_ROOT / "data"
PLANNING_PATH = PROJECT_ROOT / "progress" / "channels CSV"
PROGRESS_MANAGER = COMMENTARY_ROOT / "core" / "tools" / "progress_manager.py"
LOGS_DIR = PROJECT_ROOT / "logs"
PROGRESS_CACHE = DATA_ROOT / "_progress" / "processing_status.json"

if str(COMMENTARY_ROOT) not in sys.path:
    sys.path.insert(0, str(COMMENTARY_ROOT))

from core.tools import planning_store  # type: ignore


def _normalize_channel(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value.strip().upper() or None


def _normalize_video(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    token = value.strip()
    if not token:
        return None
    if token.isdigit():
        return f"{int(token):03d}"
    return token


def _planning_sources() -> List[Path]:
    sources = planning_store.list_planning_sources()
    if sources:
        return [Path(path) for path in sources]
    return [PLANNING_PATH]


def _iter_planning_rows(
    *,
    channel_filter: Optional[Iterable[str]] = None,
    exclude_channels: Optional[Iterable[str]] = None,
    video_filter: Optional[str] = None,
) -> Iterator[Tuple[int, Dict[str, str]]]:
    channels = {_normalize_channel(code or "") for code in channel_filter or [] if code}
    excludes = {_normalize_channel(code or "") for code in exclude_channels or [] if code}
    video = _normalize_video(video_filter)

    planning_store.refresh(force=True)
    available_channels: Sequence[str] = planning_store.list_channels()
    target_channels: List[str]
    if channels:
        target_channels = [code for code in available_channels if code in channels]
    else:
        target_channels = list(available_channels)

    for channel in sorted(target_channels):
        if excludes and channel in excludes:
            continue
        for row in planning_store.get_rows(channel):
            video_number = row.video_number
            if video and video_number != video:
                continue
            yield row.row_number, {
                "channel_code": channel,
                "video_number": video_number,
                "row_number": row.row_number,
                "title": row.raw.get("タイトル") or row.raw.get("title") or "",
                "progress": row.raw.get("進捗") or row.raw.get("progress") or "",
            }


def _run_validate_status(channel: str, video: str, *, context: Optional[str]) -> Dict:
    cmd = [
        sys.executable,
        str(PROGRESS_MANAGER),
        "validate-status",
        "--channel-code",
        channel,
        "--video-number",
        video,
        "--json",
    ]
    if context:
        cmd.extend(["--context", context])
    result = subprocess.run(
        cmd,
        cwd=str(COMMENTARY_ROOT),
        capture_output=True,
        text=True,
    )
    stdout = result.stdout.strip()
    payload: Dict = {}
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = {
                "channel_code": channel,
                "video_number": video,
                "success": False,
                "issues": ["JSON decode error"],
                "raw_output": stdout,
            }
    else:
        payload = {
            "channel_code": channel,
            "video_number": video,
            "success": False,
            "issues": ["validate-status produced no output"],
        }
    payload["returncode"] = result.returncode
    stderr = result.stderr.strip()
    if stderr:
        payload["stderr"] = stderr
    return payload


def _apply_global_status_fix(status_path: Path, script_id: str, new_status: str) -> bool:
    if not status_path.exists():
        return False
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload["status"] = new_status
    payload["updated_at"] = now
    if new_status == "completed":
        payload.setdefault("completed_at", now)
    else:
        payload.pop("completed_at", None)
    status_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if PROGRESS_CACHE.exists():
        try:
            cache_payload = json.loads(PROGRESS_CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache_payload = {}
        if cache_payload.get("script_id") == script_id:
            cache_payload["status"] = new_status
            cache_payload["updated_at"] = now
            if new_status == "completed":
                cache_payload.setdefault("completed_at", now)
            else:
                cache_payload.pop("completed_at", None)
            PROGRESS_CACHE.write_text(
                json.dumps(cache_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    return True


def _build_summary(records: List[Dict]) -> Dict[str, object]:
    summary: Dict[str, object] = {}
    total = len(records)
    success = sum(1 for item in records if item.get("success"))
    failures = total - success
    warning_only = sum(
        1 for item in records if item.get("success") and item.get("warnings")
    )
    summary["total"] = total
    summary["success"] = success
    summary["failures"] = failures
    summary["warning_only"] = warning_only

    per_channel = Counter(item.get("channel_code") for item in records if item.get("channel_code"))
    summary["per_channel_counts"] = dict(per_channel)
    failure_details = [
        {
            "channel_code": item.get("channel_code"),
            "video_number": item.get("video_number"),
            "issues": item.get("issues") or [],
        }
        for item in records
        if not item.get("success")
    ]
    summary["failures_detail"] = failure_details
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Sweep validate-status across channels CSV")
    parser.add_argument("--channel-code", action="append", help="Channel code filter (repeatable)")
    parser.add_argument("--video-number", help="Video number filter (requires channel filter)")
    parser.add_argument(
        "--exclude-channel",
        action="append",
        help="Channel codes to skip (repeatable)",
    )
    parser.add_argument("--context", default="sweep", help="Context label recorded in guard logs")
    parser.add_argument("--limit", type=int, help="Stop after N rows")
    parser.add_argument("--output", help="Explicit output path")
    parser.add_argument(
        "--repair-global",
        action="store_true",
        help="Automatically align status.json top-level status with derived result",
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
        return

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_path = Path(args.output) if args.output else LOGS_DIR / f"validate_status_full_{timestamp}.json"

    results: List[Dict] = []
    repairs: List[Dict[str, str]] = []
    for _, meta in targets:
        payload = _run_validate_status(meta["channel_code"], meta["video_number"], context=args.context)
        global_status = payload.get("global_status")
        derived_status = payload.get("derived_status")
        status_path_str = payload.get("status_path")
        if (
            args.repair_global
            and global_status
            and derived_status
            and derived_status != global_status
            and status_path_str
        ):
            fixed = _apply_global_status_fix(Path(status_path_str), payload.get("script_id") or "", derived_status)
            if fixed:
                repairs.append(
                    {
                        "channel_code": payload.get("channel_code"),
                        "video_number": payload.get("video_number"),
                        "before": global_status,
                        "after": derived_status,
                    }
                )
                payload = _run_validate_status(meta["channel_code"], meta["video_number"], context=args.context)
        payload["planning_row"] = {
            "row_number": meta["row_number"],
            "title": meta["title"],
            "progress": meta["progress"],
        }
        results.append(payload)

    summary = _build_summary(results)
    summary["repairs_applied"] = len(repairs)
    artifact = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "context": args.context,
        "planning_sources": [str(path) for path in _planning_sources()],
        "repairs": repairs,
        "results": results,
        "summary": summary,
    }
    output_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path = LOGS_DIR / "validate_status_full_latest.json"
    latest_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== validate-status sweep summary ===")
    print(f"Rows     : {summary['total']}")
    print(f"Success  : {summary['success']}")
    print(f"Failures : {summary['failures']}")
    print(f"Warnings : {summary['warning_only']}")
    if summary["failures_detail"]:
        print("\nFailures:")
        for item in summary["failures_detail"][:10]:
            ch = item.get("channel_code")
            vid = item.get("video_number")
            issues = "; ".join(item.get("issues") or [])
            print(f" - {ch}-{vid}: {issues}")
    print(f"\nOutput   : {output_path}")
    print(f"Latest   : {latest_path}")


if __name__ == "__main__":
    main()
