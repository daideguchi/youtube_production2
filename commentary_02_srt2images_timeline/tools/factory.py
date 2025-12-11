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

# Define PROJECT_ROOT before using it
PROJECT_ROOT = Path(__file__).resolve().parents[1]

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

def run_command(cmd: List[str], cwd: Path = PROJECT_ROOT, check: bool = True) -> int:
    """Run a subprocess command with real-time output."""
    cmd_str = " ".join(cmd)
    logger.info(f"🚀 Executing: {cmd_str}")
    try:
        result = subprocess.run(cmd, cwd=cwd, check=check)
        return result.returncode
    except subprocess.CalledProcessError as e:
        logger.error(f"❌ Command failed: {cmd_str}")
        return e.returncode

def find_latest_run_dir(video_id: str, output_dir: Path) -> Optional[Path]:
    """
    Find the latest valid run directory for a given video ID.
    A valid run directory must contain image_cues.json to be considered valid.

    Args:
        video_id: Video ID (e.g., 'CH02-015')
        output_dir: Output directory to search in

    Returns:
        Path to the latest valid run directory or None if not found
    """
    # Find all directories matching the video ID pattern
    pattern = str(output_dir / f"{video_id}_*")
    candidates = []

    for path in glob.glob(pattern):
        run_path = Path(path)
        if run_path.is_dir() and (run_path / "image_cues.json").exists():
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

    args = parser.parse_args()

    # Validate Inputs
    srt_path = Path(args.srt).resolve()
    if not srt_path.exists():
        logger.error(f"❌ SRT file not found: {srt_path}")
        sys.exit(1)

    # Extract video ID from the SRT filename (without extension)
    video_id = srt_path.stem  # e.g., "CH02-015" from "CH02-015.srt"

    # Define the output directory
    output_dir = PROJECT_ROOT / "output"

    # Construct the base command for auto_capcut_run.py
    tool_path = Path(__file__).resolve().parent / "auto_capcut_run.py"

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
            "--nanobanana", "direct",  # Enable actual image generation via LLMRouter
            "--use-aspect-guide"
        ]

        # Add title if provided
        if args.title:
            run_pipeline_cmd.extend(["--title", args.title])

        # Run the pipeline
        ret_code = run_command(run_pipeline_cmd)
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

        if args.debug:
            base_cmd.append("--dry-run")

        run_command(base_cmd)


    elif args.intent == "draft":
        logger.info(f"⏩ Draft regeneration for {video_id}: Looking for latest run_dir...")

        # Find the latest run directory with image_cues.json
        latest_run_dir = find_latest_run_dir(video_id, output_dir)
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

            run_command(base_cmd)
        else:
            logger.error(f"❌ No valid run directory found for {video_id} with image_cues.json. Run with 'new' first.")
            sys.exit(1)

    elif args.intent == "check":
        logger.info(f"🔍 CHECK Mode for {video_id} (validation only)...")

        # Generate run name with timestamp
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        run_name = f"{video_id}_{timestamp}"

        # Execute pipeline in none mode to generate image_cues.json
        run_pipeline_cmd = [
            sys.executable,
            str(PROJECT_ROOT / "tools" / "run_pipeline.py"),
            "--srt", str(srt_path),
            "--out", str(output_dir / run_name),  # Specify the output directory
            "--engine", "none",  # Skip timeline engine
            "--channel", args.channel,
            "--concurrency", str(args.concurrency),
            "--nanobanana", "none",  # Skip actual image generation in check mode
            "--use-aspect-guide"
        ]

        ret_code = run_command(run_pipeline_cmd)
        if ret_code != 0:
            logger.error("❌ Pipeline execution failed in check mode")
            sys.exit(ret_code)

        # Find the latest run directory to get the run_dir for belt generation
        latest_run_dir = find_latest_run_dir(video_id, output_dir)
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

            run_command(base_cmd)
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
