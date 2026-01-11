#!/usr/bin/env python3
"""
Manual smoke test: generate multiple video images via Fireworks FLUX.1 schnell.

Usage (repo root):
  PYTHONPATH=".:packages" python3 tests/manual_generate_fireworks_flux_schnell.py --n 3
  PYTHONPATH=".:packages" python3 tests/manual_generate_fireworks_flux_schnell.py --variety

Outputs:
  tests/_out/fireworks_flux_schnell/<timestamp>/img_XX.png
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import re

import yaml
from factory_common.image_client import ImageClient, ImageGenerationError, ImageTaskOptions
from factory_common.paths import repo_root


DEFAULT_PROMPT = (
    "Scene: a quiet living room at dusk, warm lamp light, subtle film grain. "
    "No text, no logos, no signage. "
    "Avoid extra people; include only the main subject if needed."
)

def _load_style_presets() -> dict[str, str]:
    path = repo_root() / "configs" / "image_style_presets.yaml"
    if not path.exists():
        return {}
    try:
        conf = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    presets = conf.get("presets") if isinstance(conf, dict) else None
    if not isinstance(presets, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in presets.items():
        if isinstance(value, str):
            out[str(key)] = value
            continue
        if not isinstance(value, dict):
            continue
        prompt = str(value.get("prompt") or "").strip()
        if prompt:
            out[str(key)] = prompt
    return out


STYLE_PRESETS: dict[str, str] = _load_style_presets()


def _default_out_dir() -> Path:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return repo_root() / "tests" / "_out" / "fireworks_flux_schnell" / ts


def _looks_rate_limited(msg: str) -> bool:
    upper = msg.upper()
    lower = msg.lower()
    return (
        " 429" in msg
        or "ERROR 429" in upper
        or "RATE_LIMIT" in upper
        or "TOO MANY REQUESTS" in upper
        or "rate limit" in lower
        or "cooldown" in lower
    )


def _recommended_sleep_sec(msg: str) -> int:
    m = re.search(r"cooldown for ~(\d+)s", msg, flags=re.I)
    if m:
        try:
            return max(1, int(m.group(1)))
        except Exception:
            return 30
    return 30


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--n", type=int, default=3, help="Number of images to generate (default: 3)")
    parser.add_argument("--prompt", type=str, default=DEFAULT_PROMPT, help="Text prompt for generation")
    parser.add_argument("--aspect-ratio", type=str, default="16:9", help="Aspect ratio (default: 16:9)")
    parser.add_argument("--size", type=str, default="1920x1080", help="Target size (default: 1920x1080)")
    parser.add_argument("--seed", type=int, default=None, help="Optional seed (int)")
    parser.add_argument(
        "--pace-sec",
        type=float,
        default=0.0,
        help="Sleep seconds between requests (default: 0; recommended ~4 for --variety)",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=8,
        help="Max retries per image when rate-limited (default: 8)",
    )
    parser.add_argument(
        "--variety",
        action="store_true",
        help="Generate multiple styles (one image per style preset by default)",
    )
    parser.add_argument(
        "--style",
        action="append",
        default=None,
        help="Style preset name (repeatable). Use with --variety. Use --list-styles to see options.",
    )
    parser.add_argument(
        "--per-style",
        type=int,
        default=1,
        help="Images per style when using --variety (default: 1)",
    )
    parser.add_argument(
        "--list-styles",
        action="store_true",
        help="List available style preset names and exit",
    )
    parser.add_argument(
        "--model-key",
        type=str,
        default="fireworks_flux_1_schnell_fp8",
        help="Force model_key (default: fireworks_flux_1_schnell_fp8)",
    )
    parser.add_argument(
        "--allow-fallback",
        action="store_true",
        help="Allow fallback to tier candidates if the forced model fails (default: off)",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="",
        help="Output directory (default: tests/_out/fireworks_flux_schnell/<timestamp>/)",
    )
    args = parser.parse_args(argv)

    if args.list_styles:
        for name in sorted(STYLE_PRESETS.keys()):
            print(name)
        return 0

    n = max(1, int(args.n))
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    extra = {"model_key": args.model_key, "allow_fallback": bool(args.allow_fallback)}

    pace_sec = float(args.pace_sec or 0.0)
    if args.variety and pace_sec <= 0:
        pace_sec = 4.0

    try:
        client = ImageClient()
        results = []

        def _gen_one(*, prompt: str, seed: int | None) -> "ImageResult":
            max_retries = max(1, int(args.max_retries))
            last_exc: Exception | None = None
            for _attempt in range(max_retries):
                if pace_sec > 0:
                    time.sleep(pace_sec)
                try:
                    return client.generate(
                        ImageTaskOptions(
                            task="visual_image_gen",
                            prompt=prompt,
                            aspect_ratio=str(args.aspect_ratio) if args.aspect_ratio else None,
                            size=str(args.size) if args.size else None,
                            n=1,
                            seed=seed,
                            extra=extra,
                        )
                    )
                except ImageGenerationError as exc:
                    last_exc = exc
                    msg = str(exc)
                    if _looks_rate_limited(msg):
                        time.sleep(_recommended_sleep_sec(msg))
                        continue
                    raise
            if last_exc is not None:
                raise last_exc
            raise ImageGenerationError("Unknown error while generating image")

        if args.variety:
            per_style = max(1, int(args.per_style))
            style_names = args.style or list(STYLE_PRESETS.keys())
            if not style_names:
                raise ImageGenerationError(
                    "No style presets available. Ensure configs/image_style_presets.yaml exists, "
                    "or pass --style <preset_name> explicitly."
                )
            unknown = [s for s in style_names if s not in STYLE_PRESETS]
            if unknown:
                raise ImageGenerationError(
                    "Unknown style preset(s): "
                    + ", ".join(unknown)
                    + " (use --list-styles to see available options)"
                )
            for style_idx, style_name in enumerate(style_names):
                style_dir = out_dir / style_name
                style_dir.mkdir(parents=True, exist_ok=True)
                style_prefix = STYLE_PRESETS[style_name]
                for i in range(per_style):
                    seed = None
                    if args.seed is not None:
                        seed = int(args.seed) + (style_idx * 100) + i
                    prompt = f"{style_prefix}. {args.prompt}"
                    result = _gen_one(prompt=prompt, seed=seed)
                    if not result.images:
                        raise ImageGenerationError("No image bytes returned from ImageClient")
                    results.append((style_dir / f"img_{i + 1:02d}.png", result))
        else:
            for i in range(n):
                seed = (args.seed + i) if args.seed is not None else None
                result = _gen_one(prompt=str(args.prompt), seed=seed)
                if not result.images:
                    raise ImageGenerationError("No image bytes returned from ImageClient")
                results.append((out_dir / f"img_{i + 1:02d}.png", result))
    except ImageGenerationError as exc:
        print(f"ERROR: image generation failed: {exc}", file=sys.stderr)
        print(
            "Hint: ensure Fireworks env vars are set (e.g. FIREWORKS_API_KEY) before running.",
            file=sys.stderr,
        )
        return 1

    root = repo_root()
    used_providers = []
    used_models = []
    for path, result in results:
        img = result.images[0]
        path.write_bytes(img)
        used_providers.append(result.provider)
        used_models.append(result.model)
        try:
            rel = path.relative_to(root)
            print(str(rel))
        except Exception:
            print(str(path))

    print(f"providers={sorted(set(used_providers))} models={sorted(set(used_models))} n={len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
