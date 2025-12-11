
import sys
import os
import argparse
import logging
from pathlib import Path

# プロジェクトルートをパスに追加
project_root = Path(__file__).resolve().parent.parent
sys.path.append(str(project_root / "commentary_02_srt2images_timeline/src"))

from srt2images.orchestration.pipeline import run_pipeline

def main():
    parser = argparse.ArgumentParser(description="Manual runner for pipeline")
    parser.add_argument("--srt", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--channel", default=None)
    parser.add_argument("--engine", default="capcut")
    parser.add_argument("--concurrency", type=int, default=1)
    
    # Defaults needed by pipeline.py
    parser.add_argument("--prompt_template", default=None)
    parser.add_argument("--style", default=None)
    parser.add_argument("--cue_mode", default="auto") # or per_segment
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--imgdur", type=float, default=6.0)
    parser.add_argument("--crossfade", type=float, default=1.0)
    parser.add_argument("--nanobanana", default="direct", choices=["direct", "none"], help="Image generation: direct=Gemini(ImageClient), none=skip")
    parser.add_argument("--nanobanana_bin", default=None, help="Deprecated (CLI removed); keep empty")
    parser.add_argument("--nanobanana_timeout", type=int, default=300)
    parser.add_argument("--nanobanana_config", default=None, help="Deprecated (unused in direct mode)")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--negative", default="")
    parser.add_argument("--seed", type=int, default=-1)
    parser.add_argument("--fit", default="cover")
    parser.add_argument("--margin", type=int, default=0)
    parser.add_argument("--use_aspect_guide", action="store_true")
    parser.add_argument("--retry_until_success", action="store_true", default=False)
    parser.add_argument("--max_retries", type=int, default=6)
    parser.add_argument("--placeholder_text", type=str, default="")

    args = parser.parse_args()
    
    # Run pipeline
    run_pipeline(args)

if __name__ == "__main__":
    main()
