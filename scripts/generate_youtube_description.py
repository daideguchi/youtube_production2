#!/usr/bin/env python3
"""
CLI helper to generate YouTube descriptions from final SRT files.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "commentary_01_srtfile_v2"))

from core.tools.youtube_description import YouTubeDescriptionGenerator  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a YouTube description from the final SRT transcript.",
    )
    parser.add_argument("--channel", required=True, help="Channel code (e.g. CH01)")
    parser.add_argument("--video", required=True, help="Video number (e.g. 191)")
    parser.add_argument("--model", help="Override OpenRouter model ID")
    parser.add_argument("--prompt", type=Path, help="Custom prompt template path")
    parser.add_argument("--temperature", type=float, default=0.45)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--max-blocks", type=int, default=12, help="Timeline block limit")
    parser.add_argument("--target-chars", type=int, default=360, help="Approx chars per block before splitting")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing youtube_description.md")
    parser.add_argument("--dry-run", action="store_true", help="Prepare prompt without calling OpenRouter")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    generator = YouTubeDescriptionGenerator(
        prompt_path=args.prompt,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
    )
    result = generator.generate_description(
        channel_code=args.channel,
        video_number=args.video,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        max_blocks=args.max_blocks,
        target_chars=args.target_chars,
    )
    print(result)


if __name__ == "__main__":
    main()
