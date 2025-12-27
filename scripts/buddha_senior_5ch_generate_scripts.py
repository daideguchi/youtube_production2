#!/usr/bin/env python3
"""
Script mass-production for the Buddha senior 5ch set (CH12–CH16).

Goals:
- Offline mode: no external LLM calls (SCRIPT_PIPELINE_DRY=1)
- API mode: use LLM API calls (optionally split Azure/non-Azure)
- Keep Planning SoT (CSV) as the source of titles/tags
- Generate a full A-text (assembled.md) per video via script_pipeline stages
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

from _bootstrap import bootstrap

bootstrap(load_env=False)

from factory_common.paths import channels_csv_path, repo_root  # noqa: E402
from script_pipeline.runner import reconcile_status, run_stage  # noqa: E402
from script_pipeline.sot import load_status  # noqa: E402


DEFAULT_CHANNELS = ["CH12", "CH13", "CH14", "CH15", "CH16"]
SCRIPT_STAGES = [
    "topic_research",
    "script_outline",
    "chapter_brief",
    "script_draft",
    "script_enhancement",
    "script_review",
    "quality_check",
    "script_validation",
]


def _parse_channels(value: Optional[str]) -> List[str]:
    if not value:
        return DEFAULT_CHANNELS[:]
    raw = [v.strip().upper() for v in value.split(",") if v.strip()]
    unknown = [ch for ch in raw if ch not in DEFAULT_CHANNELS]
    if unknown:
        raise SystemExit(f"Unsupported channel(s): {', '.join(unknown)} (allowed: {', '.join(DEFAULT_CHANNELS)})")
    return raw


def _parse_video_filter(value: Optional[str]) -> Optional[Set[str]]:
    if not value:
        return None
    out: Set[str] = set()
    for token in [t.strip() for t in value.split(",") if t.strip()]:
        if "-" in token:
            lo_s, hi_s = token.split("-", 1)
            if not lo_s.strip().isdigit() or not hi_s.strip().isdigit():
                raise SystemExit(f"Invalid --videos range: {token}")
            lo, hi = int(lo_s), int(hi_s)
            if lo > hi:
                lo, hi = hi, lo
            for n in range(lo, hi + 1):
                out.add(f"{n:03d}")
        else:
            if not token.isdigit():
                raise SystemExit(f"Invalid --videos token: {token}")
            out.add(f"{int(token):03d}")
    return out


def _iter_planning_rows(channel: str) -> Iterable[Tuple[str, str]]:
    path = channels_csv_path(channel)
    if not path.exists():
        raise SystemExit(f"Planning CSV not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            video_raw = (row.get("動画番号") or "").strip()
            title = (row.get("タイトル") or "").strip()
            if not video_raw or not video_raw.isdigit() or not title:
                continue
            yield f"{int(video_raw):03d}", title


def _run_prepare(channels: List[str], videos: Optional[Set[str]]) -> None:
    cmd = [
        sys.executable,
        str((repo_root() / "scripts" / "buddha_senior_5ch_prepare.py").resolve()),
        "prepare",
        "--channels",
        ",".join(channels),
    ]
    if videos is not None:
        # stable, compact representation (e.g., "001,002") for prepare script parser
        cmd.extend(["--videos", ",".join(sorted(videos))])
    subprocess.run(cmd, cwd=str(repo_root()), check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate CH12–CH16 scripts (offline or API).")
    parser.add_argument("--channels", help="Comma-separated channel codes (default: CH12-CH16)")
    parser.add_argument("--videos", help="Video filter: e.g. 1-30 or 1-3,10,12", default=None)
    parser.add_argument("--mode", choices=["offline", "api"], default="offline", help="Generation mode (default: offline)")
    parser.add_argument(
        "--azure-split-ratio",
        type=float,
        default=None,
        help="When --mode api: optional split ratio for Azure vs non-Azure (e.g. 0.5). If omitted, keep OpenRouter-first.",
    )
    parser.add_argument("--force", action="store_true", help="Run stages even if already completed (costly in API mode)")
    parser.add_argument("--skip-prepare", action="store_true", help="Skip status init/metadata backfill step")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first error")
    args = parser.parse_args()

    channels = _parse_channels(args.channels)
    videos = _parse_video_filter(args.videos)

    if args.mode == "offline":
        os.environ["SCRIPT_PIPELINE_DRY"] = "1"
    else:
        # API mode
        os.environ.pop("SCRIPT_PIPELINE_DRY", None)
        # Router reads this env to split Azure/non-Azure roughly (stable by episode key).
        if args.azure_split_ratio is not None:
            os.environ["LLM_AZURE_SPLIT_RATIO"] = str(float(args.azure_split_ratio))
        else:
            os.environ.pop("LLM_AZURE_SPLIT_RATIO", None)

    if not args.skip_prepare:
        _run_prepare(channels, videos)

    total = 0
    ok = 0
    failed: List[str] = []

    for ch in channels:
        for video, title in _iter_planning_rows(ch):
            if videos is not None and video not in videos:
                continue
            total += 1
            label = f"{ch}-{video}"
            try:
                try:
                    st0 = load_status(ch, video)
                except Exception:
                    st0 = None
                for stage in SCRIPT_STAGES:
                    if not args.force:
                        prev = st0.stages.get(stage) if st0 is not None else None
                        if prev and prev.status == "completed":
                            continue
                    st = run_stage(ch, video, stage, title=title)
                    if st.stages.get(stage) and st.stages[stage].status != "completed":
                        raise RuntimeError(f"{label}: stage not completed: {stage} ({st.stages[stage].status})")
                reconcile_status(ch, video, allow_downgrade=True)
                ok += 1
                print(f"OK {label}")
            except Exception as e:  # noqa: BLE001
                msg = f"FAIL {label}: {e}"
                print(msg)
                failed.append(msg)
                if args.fail_fast:
                    raise SystemExit(1) from e

    print(f"targets: {total}")
    print(f"ok: {ok}")
    if failed:
        print("failed:")
        for line in failed:
            print(" -", line)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
