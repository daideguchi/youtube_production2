#!/usr/bin/env python3
"""
Factory Commentary CLI: Unified entry point for multi-channel video production.
Handles SRT → Images → Belts → CapCut draft pipeline across all channels.

Usage:
  factory-commentary <CHANNEL_ID> <SRT_PATH> [INTENT]

Intents:
  new   : Full pipeline (Images -> Belt -> Draft)
  draft : Regenerate draft only using latest run_dir (skips image gen)
  check : Validate inputs and generate cues/belt (no draft)

Examples:
  factory-commentary CH02 commentary_02_哲学系/CH02-015.srt check
  factory-commentary CH02 commentary_02_哲学系/CH02-015.srt new
  factory-commentary CH02 commentary_02_哲学系/CH02-015.srt draft
"""
import sys
import argparse
import subprocess
import logging
from pathlib import Path
from typing import Optional, List
import os
import glob
import re
import json

def _bootstrap_repo_root() -> Path:
    start = Path(__file__).resolve()
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate
    return cur


_BOOTSTRAP_REPO = _bootstrap_repo_root()
if str(_BOOTSTRAP_REPO) not in sys.path:
    sys.path.insert(0, str(_BOOTSTRAP_REPO))

from factory_common.paths import (  # noqa: E402
    audio_artifacts_root,
    repo_root,
    video_pkg_root,
    video_runs_root,
)

PROJECT_ROOT = video_pkg_root()
REPO_ROOT = repo_root()

from factory_common.timeline_manifest import EpisodeId, parse_episode_id, resolve_final_audio_srt

# Import using the installed package structure
try:
    from commentary_02_srt2images_timeline.src.core.config import config
except ImportError:
    # Fallback to relative import if the package isn't properly installed
    import sys
    sys.path.append(str(PROJECT_ROOT / "src"))
    from src.core.config import config

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

def run_command(cmd: List[str], cwd: Path = PROJECT_ROOT, check: bool = True, abort_patterns=None) -> int:
    """Run a subprocess command with real-time output. If abort_patterns is provided, abort when any pattern appears."""
    cmd_str = " ".join(cmd)
    logger.info(f"🚀 Executing: {cmd_str}")
    abort_patterns = [p.strip() for p in abort_patterns.split(",")] if abort_patterns else []

    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for line in proc.stdout:
            line = line.rstrip("\n")
            print(line)
            if abort_patterns and any(p in line for p in abort_patterns):
                logger.error(f"❌ Abort pattern detected: {line}")
                proc.terminate()
                proc.wait(timeout=5)
                return 1
        return proc.wait()
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Command failed: {cmd_str}")
        return e.returncode
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        proc.terminate()
        return 1

def canonical_video_id(channel: str, stem: str) -> str:
    """
    Build a canonical video ID that is safe across channels.
    Examples:
      - stem="CH02-015" -> "CH02-015"
      - stem="220" with channel="CH01" -> "CH01-220"
      - stem="CH01_人生の道標_220" -> "CH01-220"
    """
    raw = (stem or "").strip()
    ch = (channel or "").strip().upper()

    m = re.search(r"(CH\d{2})[-_ ]?(\d{3})", raw, flags=re.IGNORECASE)
    if m:
        return f"{m.group(1).upper()}-{m.group(2)}"
    if re.fullmatch(r"\d{1,3}", raw):
        return f"{ch}-{raw.zfill(3)}"
    m2 = re.search(r"(\d{3})", raw)
    if m2:
        return f"{ch}-{m2.group(1)}"
    return f"{ch}-{raw}" if ch else raw


def find_latest_run_dir(video_id: str, output_dir: Path, *, channel: Optional[str] = None, fallback_ids: Optional[List[str]] = None) -> Optional[Path]:
    """
    Find the latest valid run directory for a given video ID.
    A valid run directory must contain image_cues.json to be considered valid.

    Args:
        video_id: Video ID (e.g., 'CH02-015')
        output_dir: Output directory to search in

    Returns:
        Path to the latest valid run directory or None if not found
    """
    patterns = [video_id] + [x for x in (fallback_ids or []) if x and x != video_id]
    candidates: List[Path] = []

    for vid in patterns:
        pattern = str(output_dir / f"{vid}_*")
        for path in glob.glob(pattern):
            run_path = Path(path)
            if not (run_path.is_dir() and (run_path / "image_cues.json").exists()):
                continue
            if channel:
                info_path = run_path / "auto_run_info.json"
                if info_path.exists():
                    try:
                        info = json.loads(info_path.read_text(encoding="utf-8"))
                        if (info.get("channel") or "").upper() != channel.upper():
                            continue
                    except Exception:
                        # If metadata is broken, fall back to name-based filter.
                        pass
            candidates.append(run_path)

    if not candidates:
        return None

    # Sort by modification time to get the latest
    latest_run = max(candidates, key=lambda x: x.stat().st_mtime)
    return latest_run

def main():
    import datetime

    parser = argparse.ArgumentParser(description="Factory Commentary CLI")
    parser.add_argument("channel", help="Channel ID (e.g., CH02)")
    parser.add_argument("srt", help="Path to input SRT file")
    parser.add_argument("intent", nargs="?", default="new",
                        choices=["new", "draft", "check"],
                        help="Operation mode (default: new)")

    # Passthrough options
    parser.add_argument("--concurrency", default=3, type=int, help="Image generation concurrency")
    parser.add_argument("--title", help="Explicit video title (overrides LLM generation)")
    parser.add_argument("--labels", help="Explicit belt labels (comma separated)")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--nanobanana", choices=["direct", "none"], default="direct", help="Image generation mode: direct=ImageClient(Gemini), none=skip")
    parser.add_argument("--abort-on-log", help="Comma-separated patterns; if any appears in child stdout/stderr, abort the process.")
    parser.add_argument("--timeout-ms", type=int, help="Optional timeout (ms) for child commands (run_pipeline/auto_capcut_run). Default: no timeout.")

    args = parser.parse_args()

    # Validate Inputs
    requested_srt_path = Path(args.srt).resolve()
    if not requested_srt_path.exists():
        logger.error(f"❌ SRT file not found: {requested_srt_path}")
        sys.exit(1)

    # Safety: prevent cross-channel wiring.
    name_match = re.search(r"(CH\d{2})", requested_srt_path.name, flags=re.IGNORECASE)
    if name_match and name_match.group(1).upper() != args.channel.upper():
        logger.error(
            f"❌ channel mismatch: srt={requested_srt_path.name} implies {name_match.group(1).upper()} but channel={args.channel}"
        )
        sys.exit(1)
    final_root = audio_artifacts_root() / "final"
    try:
        rel_parts = requested_srt_path.relative_to(final_root).parts
        if rel_parts:
            dir_ch = rel_parts[0][:4].upper()
            if dir_ch.startswith("CH") and dir_ch[2:4].isdigit() and dir_ch != args.channel.upper():
                logger.error(f"❌ channel mismatch: srt under {dir_ch} but channel={args.channel}")
                sys.exit(1)
    except Exception:
        pass

    # Prefer audio_tts_v2 final SRT when resolvable (prevents stale input copies).
    episode = parse_episode_id(str(requested_srt_path))
    if episode is None and re.fullmatch(r"\d{1,3}", requested_srt_path.stem):
        episode = EpisodeId(channel=args.channel.upper(), video=requested_srt_path.stem.zfill(3))
    srt_path = requested_srt_path
    if episode and episode.channel.upper() == args.channel.upper():
        try:
            _wav, final_srt = resolve_final_audio_srt(episode)
            if final_srt.resolve() != requested_srt_path.resolve():
                logger.info("✅ SoT SRT selected: %s (requested: %s)", final_srt, requested_srt_path)
            srt_path = final_srt.resolve()
        except FileNotFoundError:
            srt_path = requested_srt_path

    # Extract and normalize video ID from the SRT filename (without extension)
    raw_video_id = episode.episode if episode else srt_path.stem  # e.g., "CH02-015" or "220"
    video_id = canonical_video_id(args.channel, raw_video_id)

    # Define the output directory
    output_dir = video_runs_root()

    # Construct the base command for auto_capcut_run.py
    tool_path = Path(__file__).resolve().parent / "auto_capcut_run.py"

    abort_patterns = args.abort_on_log or ""

    # Apply options based on Intent
    if args.intent == "new":
        logger.info(f"🎬 Starting NEW production pipeline for {video_id}...")

        # Generate run name with timestamp
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = f"{video_id}_{timestamp}"

        # Execute full pipeline via run_pipeline.py first
        run_pipeline_cmd = [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "run_pipeline.py"),
            "--srt", str(srt_path),
            "--out", str(output_dir / run_name),  # Specify the output directory
            "--engine", "none",  # Use none to avoid timeline engine, but images will be generated
            "--channel", args.channel,
            "--concurrency", str(args.concurrency),
            "--nanobanana", args.nanobanana,
            "--use-aspect-guide"
        ]

        # Add title if provided
        if args.title:
            run_pipeline_cmd.extend(["--title", args.title])

        # Run the pipeline
        run_timeout = args.timeout_ms / 1000 if args.timeout_ms else None

        ret_code = run_command(run_pipeline_cmd, abort_patterns=abort_patterns)
        if ret_code != 0:
            logger.error("❌ Pipeline execution failed")
            sys.exit(ret_code)

        # Belt generation happens as part of the run_pipeline or auto_capcut_run
        # Now run auto_capcut_run for draft generation if needed
        base_cmd = [
            sys.executable,
            str(tool_path),
            "--channel", args.channel,
            "--srt", str(srt_path),
            "--run-name", run_name,  # Pass the run name to auto_capcut_run
            "--img-concurrency", str(args.concurrency),
            "--suppress-warnings"
        ]
        if args.timeout_ms:
            base_cmd.extend(["--timeout-ms", str(args.timeout_ms)])
        if abort_patterns:
            base_cmd.extend(["--abort-on-log", abort_patterns])

        if args.debug:
            base_cmd.append("--dry-run")

        run_command(base_cmd, abort_patterns=abort_patterns)


    elif args.intent == "draft":
        logger.info(f"⏩ Draft regeneration for {video_id}: Looking for latest run_dir...")

        # Find the latest run directory with image_cues.json
        latest_run_dir = find_latest_run_dir(
            video_id,
            output_dir,
            channel=args.channel,
            fallback_ids=[raw_video_id],
        )
        if latest_run_dir:
            logger.info(f"📁 Using run_dir: {latest_run_dir.name}")

            # Run auto_capcut_run with the specific run directory
            base_cmd = [
                sys.executable,
                str(tool_path),
                "--channel", args.channel,
                "--srt", str(srt_path),
                "--run-name", latest_run_dir.name,  # Explicitly specify the run to use
                "--img-concurrency", str(args.concurrency),
                "--suppress-warnings"
            ]

            if args.debug:
                base_cmd.append("--dry-run")

            run_command(base_cmd, abort_patterns=abort_patterns)
        else:
            logger.error(f"❌ No valid run directory found for {video_id} with image_cues.json. Run with 'new' first.")
            sys.exit(1)

    elif args.intent == "check":
        logger.info(f"🔍 CHECK Mode for {video_id} (validation only)...")

        # Generate run name with timestamp
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = f"{video_id}_{timestamp}"

        # Execute pipeline; respect requested nanobanana mode (default: direct).
        run_pipeline_cmd = [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "run_pipeline.py"),
            "--srt", str(srt_path),
            "--out", str(output_dir / run_name),  # Specify the output directory
            "--engine", "none",  # Skip timeline engine
            "--channel", args.channel,
            "--concurrency", str(args.concurrency),
            "--nanobanana", args.nanobanana,
            "--use-aspect-guide"
        ]

        ret_code = run_command(run_pipeline_cmd, abort_patterns=abort_patterns)
        if ret_code != 0:
            logger.error("❌ Pipeline execution failed in check mode")
            sys.exit(ret_code)

        # Find the latest run directory to get the run_dir for belt generation
        latest_run_dir = find_latest_run_dir(
            video_id,
            output_dir,
            channel=args.channel,
            fallback_ids=[raw_video_id],
        )
        if latest_run_dir:
            logger.info(f"📁 Processing belt config in run_dir: {latest_run_dir.name}")
            # Run auto_capcut_run to generate belts (but not draft)
            base_cmd = [
                sys.executable,
                str(tool_path),
                "--channel", args.channel,
                "--srt", str(srt_path),
                "--run-name", latest_run_dir.name,  # Use specific run
                "--img-concurrency", str(args.concurrency),
                "--suppress-warnings",
                "--dry-run"  # Skip actual draft creation
            ]

            if args.timeout_ms:
                base_cmd.extend(["--timeout-ms", str(args.timeout_ms)])
            if abort_patterns:
                base_cmd.extend(["--abort-on-log", abort_patterns])

            run_command(base_cmd, abort_patterns=abort_patterns)
        else:
            logger.warning(f"⚠️  Could not find run directory after pipeline execution for {video_id}")

    # Passthrough options would be handled by the respective tools

    # Execute
    print("-" * 60)
    print(f"🏭 FACTORY JOB: {args.intent.upper()}")
    print(f"📺 Channel: {args.channel}")
    print(f"📜 Script : {srt_path.name}")
    print("-" * 60)

    print("\n✅ Factory job completed successfully!")


if __name__ == "__main__":
    main()
