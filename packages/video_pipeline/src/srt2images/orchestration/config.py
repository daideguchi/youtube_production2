import argparse
import os
from pathlib import Path
import toml

from factory_common.paths import video_pkg_root

def get_args():
    parser = argparse.ArgumentParser(description="SRT -> images timeline & engine deployer")
    parser.add_argument(
        "--config",
        default=str(video_pkg_root() / "config.toml"),
        help="Path to config.toml file",
    )
    parser.add_argument("--srt", help="Path to input .srt")
    parser.add_argument(
        "--channel",
        help="Channel ID (e.g., CH01) used to auto-apply preset prompt/style values",
    )
    parser.add_argument("--out", help="Output directory (will be created)")
    parser.add_argument("--engine", choices=["none", "capcut", "remotion"], help="Target engine")
    parser.add_argument("--size", help="Output resolution, e.g. 1920x1080")
    parser.add_argument("--imgdur", type=float, help="Duration per image in seconds")
    parser.add_argument(
        "--cue-mode",
        choices=["grouped", "per_segment", "single"],
        help="Cue building mode: grouped (~imgdur buckets; may use LLM planner) or per_segment (one image per SRT segment) or single (one image for entire video)",
    )
    parser.add_argument("--crossfade", type=float, help="Crossfade seconds between images (0 allowed)")
    parser.add_argument("--fps", type=int, help="Timeline FPS")
    parser.add_argument("--nanobanana", choices=["direct", "none"], help="Image generation mode (direct=ImageClient(Gemini), none=skip)")
    parser.add_argument("--nanobanana-bin", help="Path to nanobanana CLI binary (deprecated; ignored)")
    parser.add_argument("--nanobanana-timeout", type=int, help="Per-image generation timeout (seconds)")
    parser.add_argument(
        "--prompt-template",
        help="Prompt template path",
    )
    parser.add_argument("--style", help="Optional style string")
    parser.add_argument("--negative", help="Optional negative hints")
    parser.add_argument("--concurrency", type=int, help="Image generation concurrency")
    parser.add_argument("--seed", type=int, help="Seed for generation (optional)")
    parser.add_argument("--force", action="store_true", help="Force regenerate images")
    parser.add_argument("--use-aspect-guide", action="store_true", help="Attach a 16:9 blank guide image to steer the generator to 16:9 output")
    parser.add_argument("--fit", choices=["cover", "contain", "fill"], help="Image objectFit in Remotion")
    parser.add_argument("--margin", type=int, help="Margin (px) around image inside frame")
    parser.add_argument("--nanobanana-config", help="Config file path for direct mode")
    # Robustness / placeholder controls
    parser.add_argument("--retry-until-success", action="store_true", help="Keep retrying image generation until success (no placeholder)")
    parser.add_argument("--max-retries", type=int, help="Max retries per image (ignored if --retry-until-success)")
    parser.add_argument("--placeholder-text", help="Text to render on placeholder images when generation fails")
    
    args = parser.parse_args()

    config = {}
    if os.path.exists(args.config):
        config = toml.load(args.config)

    def get_config_val(key, default=None):
        section, name = key.split('.')
        return config.get(section, {}).get(name, default)

    # Merge configs, args override config file
    args.srt = args.srt or get_config_val('input.srt')
    args.channel = args.channel or get_config_val('input.channel')
    args.out = args.out or get_config_val('output.out')
    args.engine = args.engine or get_config_val('output.engine', 'none')
    args.size = args.size or get_config_val('output.size', '1920x1080')
    args.imgdur = args.imgdur or get_config_val('cues.imgdur', 20.0)
    args.cue_mode = args.cue_mode or get_config_val('cues.cue_mode', 'grouped')
    args.crossfade = args.crossfade or get_config_val('output.crossfade', 0.5)
    args.fps = args.fps or get_config_val('output.fps', 30)
    args.nanobanana = args.nanobanana or get_config_val('image_generation.nanobanana', 'direct')
    args.nanobanana_bin = ''  # deprecated
    args.nanobanana_timeout = args.nanobanana_timeout or get_config_val('image_generation.nanobanana_timeout', 300)
    
    default_prompt_template = str(video_pkg_root() / "templates" / "default.txt")
    args.prompt_template = args.prompt_template or get_config_val('image_generation.prompt_template', default_prompt_template)

    args.style = args.style or get_config_val('image_generation.style', '')
    args.negative = args.negative or get_config_val('image_generation.negative', '')
    args.concurrency = args.concurrency or get_config_val('image_generation.concurrency', 3)
    args.seed = args.seed or get_config_val('image_generation.seed', 0)
    
    # For boolean flags, we need to check if they are present in the args.
    # If they are not, we check the config file.
    if not 'force' in vars(args) or not vars(args)['force']:
        args.force = get_config_val('image_generation.force', False)
    if not 'use_aspect_guide' in vars(args) or not vars(args)['use_aspect_guide']:
        args.use_aspect_guide = get_config_val('image_generation.use_aspect_guide', False)
    if not 'retry_until_success' in vars(args) or not vars(args)['retry_until_success']:
        args.retry_until_success = get_config_val('robustness.retry_until_success', False)

    args.fit = args.fit or get_config_val('output.fit', 'cover')
    args.margin = args.margin or get_config_val('output.margin', 0)
    args.nanobanana_config = ''  # deprecated
    args.max_retries = args.max_retries or get_config_val('robustness.max_retries', 6)
    args.placeholder_text = args.placeholder_text or get_config_val('robustness.placeholder_text', '画像生成中…後で自動差し替え')
    
    # Required args
    if not args.srt:
        parser.error("the following arguments are required: --srt")
    if not args.out:
        parser.error("the following arguments are required: --out")
    
    return args
