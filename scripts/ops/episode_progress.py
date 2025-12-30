#!/usr/bin/env python3
"""
Derived episode progress view (read-only).

This is intentionally NOT a new SoT; it aggregates from:
  - Planning CSV
  - status.json
  - audio final
  - video runs (CapCut draft)

Usage:
  python3 scripts/ops/episode_progress.py --channel CH12
  python3 scripts/ops/episode_progress.py --channel CH12 --videos 012,013
  python3 scripts/ops/episode_progress.py --channel CH12 --format json
  python3 scripts/ops/episode_progress.py --channel CH12 --format summary
  python3 scripts/ops/episode_progress.py --channel CH12 --issues-only
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from _bootstrap import bootstrap


bootstrap(load_env=False)

from factory_common.episode_progress import build_episode_progress_view  # noqa: E402


def _print_summary(view: dict[str, Any]) -> None:
    channel = str(view.get("channel") or "").strip()
    generated_at = str(view.get("generated_at") or "").strip()
    print(f"channel\t{channel}")
    if generated_at:
        print(f"generated_at\t{generated_at}")
    if view.get("planning_csv_path"):
        print(f"planning_csv_path\t{view.get('planning_csv_path')}")
    if view.get("planning_duplicate_videos"):
        print(f"planning_duplicate_videos\t{','.join(view.get('planning_duplicate_videos') or [])}")
    print(f"episodes_total\t{int(view.get('episodes_total') or 0)}")
    print(f"episodes_published\t{int(view.get('episodes_published') or 0)}")
    print(f"episodes_with_issues\t{int(view.get('episodes_with_issues') or 0)}")
    print("issues_summary")
    summary = view.get("issues_summary") or {}
    if not isinstance(summary, dict) or not summary:
        print("(none)")
        return
    for k, v in summary.items():
        print(f"- {k}\t{v}")


def _print_tsv(view: dict[str, Any]) -> None:
    header = [
        "video",
        "published",
        "planning_progress",
        "script_status",
        "audio_ready",
        "video_run_id",
        "capcut_status",
        "capcut_run",
        "capcut_target",
        "issues",
    ]
    print("\t".join(header))
    for ep in view.get("episodes") or []:
        issues = ",".join(ep.get("issues") or [])
        print(
            "\t".join(
                [
                    str(ep.get("video") or ""),
                    "1" if ep.get("published_locked") else "0",
                    str(ep.get("planning_progress") or ""),
                    str(ep.get("script_status") or ""),
                    "1" if ep.get("audio_ready") else "0",
                    str(ep.get("video_run_id") or ""),
                    str(ep.get("capcut_draft_status") or ""),
                    str(ep.get("capcut_draft_run_id") or ""),
                    str(ep.get("capcut_draft_target") or ""),
                    issues,
                ]
            )
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True, help="e.g. CH12")
    parser.add_argument("--videos", default="", help="Comma-separated (e.g. 012,013)")
    parser.add_argument("--format", choices=["tsv", "json", "summary"], default="tsv")
    parser.add_argument("--issues-only", action="store_true", help="Only include episodes where issues[] is non-empty")
    parser.add_argument("--include-unplanned", action="store_true", help="Include episodes present in workspaces/scripts even if missing in CSV")
    parser.add_argument("--include-hidden-runs", action="store_true", help="Also scan runs starting with _ or .")
    args = parser.parse_args(argv)

    videos = [v for v in str(args.videos or "").split(",") if v.strip()] if args.videos else None
    view = build_episode_progress_view(
        args.channel,
        videos=videos,
        include_unplanned=bool(args.include_unplanned),
        include_hidden_runs=bool(args.include_hidden_runs),
    )

    if args.issues_only:
        episodes = [ep for ep in (view.get("episodes") or []) if ep.get("issues")]
        issues_summary: dict[str, int] = {}
        episodes_published = 0
        for ep in episodes:
            if ep.get("published_locked"):
                episodes_published += 1
            for issue in ep.get("issues") or []:
                token = str(issue or "").strip()
                if not token:
                    continue
                issues_summary[token] = issues_summary.get(token, 0) + 1
        view = {
            **view,
            "episodes": episodes,
            "episodes_total": len(episodes),
            "episodes_published": episodes_published,
            "episodes_with_issues": len(episodes),
            "issues_summary": dict(sorted(issues_summary.items(), key=lambda kv: (-kv[1], kv[0]))),
        }

    if args.format == "json":
        print(json.dumps(view, ensure_ascii=False, indent=2))
        return 0

    if args.format == "summary":
        _print_summary(view)
        return 0

    _print_tsv(view)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
