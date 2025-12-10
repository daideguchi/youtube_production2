#!/usr/bin/env python3
"""Batch re-run of stage sequence for specific channel/videos."""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMENTARY_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
RUN_STAGE = COMMENTARY_ROOT / "qwen" / "run_stage.py"
DEFAULT_STAGE_SEQUENCE = [
    "topic_research",
    "script_outline",
    "script_draft",
    "script_enhancement",
    "script_review",
    "quality_check",
    "script_validation",
]
LOG_ROOT = REPO_ROOT / "logs" / "regression" / "stage_recover"


def _parse_videos(raw: str) -> List[str]:
    values: List[str] = []
    for chunk in raw.split(","):
        token = chunk.strip()
        if not token:
            continue
        if token.isdigit():
            token = f"{int(token):03d}"
        values.append(token)
    if not values:
        raise ValueError("動画番号を指定してください")
    return values


def _resolve_stage_sequence(start_stage: str, stages: Iterable[str] | None) -> List[str]:
    sequence = list(stages or DEFAULT_STAGE_SEQUENCE)
    if start_stage not in sequence:
        raise ValueError(f"開始ステージ {start_stage} がシーケンス内にありません")
    index = sequence.index(start_stage)
    return sequence[index:]


def _run_stage(
    channel: str,
    video: str,
    stage: str,
    dry_run: bool,
    log_dir: Path,
) -> Tuple[int, Path]:
    timestamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{channel}-{video}_{stage}_{timestamp}.log"
    cmd = [
        sys.executable,
        str(RUN_STAGE),
        "--channel",
        channel,
        "--video",
        video,
        "--stage",
        stage,
    ]
    if dry_run:
        log_path.write_text("[dry-run] " + " ".join(cmd) + "\n", encoding="utf-8")
        return 0, log_path

    proc = subprocess.run(cmd, cwd=str(COMMENTARY_ROOT), capture_output=True, text=True)
    log_path.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
    return proc.returncode, log_path


def _should_retry(stage: str, log_path: Path) -> bool:
    if stage not in {"topic_research", "quality_check"}:
        return False
    if not log_path.exists():
        return False
    text = log_path.read_text(encoding="utf-8")
    return "429" in text or "Too Many Requests" in text


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-run stage sequences for specific videos")
    parser.add_argument("--channel-code", required=True, help="チャンネルコード (例: CH04)")
    parser.add_argument(
        "--videos",
        required=True,
        help="カンマ区切りの動画番号リスト (例: 022,024,033)",
    )
    parser.add_argument(
        "--start-stage",
        default="topic_research",
        help="再実行を開始するステージ (デフォルト: topic_research)",
    )
    parser.add_argument(
        "--stages",
        help="カンマ区切りで独自のステージシーケンスを指定 (省略時は既定)",
    )
    parser.add_argument("--dry-run", action="store_true", help="コマンドを実行せずログのみ出力")
    args = parser.parse_args()

    channel = args.channel_code.strip().upper()
    videos = _parse_videos(args.videos)
    stages = (
        [token.strip() for token in args.stages.split(",") if token.strip()]
        if args.stages
        else None
    )
    stage_sequence = _resolve_stage_sequence(args.start_stage, stages)
    log_dir = LOG_ROOT / f"{dt.datetime.now():%Y%m%d}"

    for video in videos:
        print(f"== {channel}-{video} ==")
        for stage in stage_sequence:
            print(f"  -> {stage}", flush=True)
            for attempt in range(3):
                code, log_path = _run_stage(channel, video, stage, args.dry_run, log_dir)
                if code == 0 or args.dry_run:
                    break
                if _should_retry(stage, log_path) and attempt < 2:
                    wait_seconds = 60 * (attempt + 1)
                    print(f"    ⚠️  Detected rate limit. Retrying in {wait_seconds} seconds...")
                    time.sleep(wait_seconds)
                    continue
                print(f"  !! stage {stage} failed with exit code {code}")
                return code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
