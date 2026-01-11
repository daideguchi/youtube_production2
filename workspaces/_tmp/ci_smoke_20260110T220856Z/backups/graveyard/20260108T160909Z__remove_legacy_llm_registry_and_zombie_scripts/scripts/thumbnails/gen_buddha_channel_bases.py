#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate Buddha background base images for `buddha_3line` thumbnails.

Outputs (per channel):
  asset/thumbnails/{CH}/ch{NN}_buddha_bg_set{A|B|C}_1920x1080.png

Notes:
- These bases are meant to be mirrored (flip_base=true) so the Buddha lands on the left,
  leaving clean negative space for text on the right.
- Prompts are tuned to avoid any embedded text (text will be composited by the compiler).
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path
from typing import Dict, List

from PIL import Image


def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    raise RuntimeError("repo root not found (pyproject.toml). Run from inside the repo.")


try:
    from _bootstrap import bootstrap
except ModuleNotFoundError:
    repo_root: Path | None = None
    for start in (Path.cwd().resolve(), Path(__file__).resolve()):
        try:
            repo_root = _discover_repo_root(start)
            break
        except Exception:
            continue
    if repo_root is None:
        raise
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from _bootstrap import bootstrap

bootstrap()

from factory_common import paths as fpaths  # noqa: E402
from factory_common.image_client import (  # noqa: E402
    ImageClient,
    ImageGenerationError,
    ImageTaskOptions,
)


STYLE_BY_CHANNEL: Dict[str, str] = {
    # Subtle progression: realistic -> painterly -> watercolor -> ink/wash -> woodblock-ish.
    "CH13": "clean cinematic digital illustration, realistic metallic gold, crisp details",
    "CH14": "cinematic digital illustration with subtle painterly brush texture, slightly matte finish",
    "CH15": "high-contrast watercolor illustration feel, soft edges but still sharp subject silhouette",
    "CH16": "ink-wash illustration feel with gentle paper texture, gold-leaf highlights, dramatic lighting",
    "CH17": "illustration with slight woodblock/halftone texture, bold shapes, strong contrast, cinematic gold",
}

COMPOSITION_BY_SET: Dict[str, str] = {
    "setA": "extreme close-up of a Buddha face (3/4 view), placed on the RIGHT side, facing LEFT",
    "setB": "bust/torso of a Buddha statue with a halo/backlight, placed on the RIGHT side, facing LEFT",
    "setC": "seated Buddha statue (upper body visible), placed on the RIGHT side, facing LEFT",
}


def _normalize_channel(ch: str) -> str:
    return str(ch or "").strip().upper()


def _resize_cover(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """
    Resize while preserving aspect ratio, cropping to fully cover the target box.
    """
    tw, th = int(target_w), int(target_h)
    if tw <= 0 or th <= 0:
        return img
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    scale = max(tw / w, th / h)
    new_w = max(1, int(round(w * scale)))
    new_h = max(1, int(round(h * scale)))
    resized = img.resize((new_w, new_h), Image.LANCZOS)
    left = max(0, (new_w - tw) // 2)
    top = max(0, (new_h - th) // 2)
    return resized.crop((left, top, left + tw, top + th))


def _prompt_for(*, channel: str, style: str, composition: str) -> str:
    # IMPORTANT: Leave empty space on the LEFT because the compiler will mirror the base.
    return "\n".join(
        [
            "YouTube thumbnail background image (16:9). NO TEXT, NO LOGO, NO WATERMARK.",
            f"Subject: {composition}.",
            "Composition rules:",
            "- The LEFT half must be mostly empty, dark, and clean (negative space for large text overlay later).",
            "- Background: dark smoky gradient, subtle bokeh only, no clutter, no extra objects.",
            "- Lighting: dramatic warm rim light, high contrast, sacred/majestic atmosphere.",
            f"Style: {style}.",
            "Color palette: deep blacks + warm gold/orange highlights.",
            "Do not include any letters, numbers, captions, or symbols.",
        ]
    )


def _out_path(channel: str, set_name: str, *, size: str) -> Path:
    ch = _normalize_channel(channel)
    ch_num = ch.replace("CH", "").zfill(2)
    out_dir = fpaths.assets_root() / "thumbnails" / ch
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"ch{ch_num}_buddha_bg_{set_name}_{size}.png"


def _generate_one(
    *,
    client: ImageClient,
    channel: str,
    set_name: str,
    model_key: str,
    size: str,
    aspect_ratio: str,
    seed: int,
    force: bool,
    allow_fallback: bool,
    sleep_sec: float,
    max_attempts: int,
) -> Path:
    style = STYLE_BY_CHANNEL[channel]
    composition = COMPOSITION_BY_SET[set_name]
    prompt = _prompt_for(channel=channel, style=style, composition=composition)
    out_path = _out_path(channel, set_name, size=size)
    if out_path.exists() and not force:
        print(f"[SKIP] {channel} {set_name} -> {out_path}")
        return out_path

    last_exc: Exception | None = None
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        try:
            print(f"[GEN] {channel} {set_name} via {model_key} seed={seed} (attempt {attempt})")
            result = client.generate(
                ImageTaskOptions(
                    task="thumbnail_image_gen",
                    prompt=prompt,
                    aspect_ratio=aspect_ratio,
                    size=size,
                    n=1,
                    seed=int(seed),
                    extra={"model_key": model_key, "allow_fallback": bool(allow_fallback)},
                )
            )
            if not result.images:
                raise ImageGenerationError("No image bytes returned")
            img = Image.open(io.BytesIO(result.images[0])).convert("RGB")
            try:
                tw, th = (int(part) for part in str(size).lower().split("x", 1))
                img = _resize_cover(img, tw, th)
            except Exception:
                pass
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_path, format="PNG", optimize=True)
            print(f"[OK] {channel} {set_name} -> {out_path}")
            if sleep_sec > 0:
                time.sleep(float(sleep_sec))
            return out_path
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"[WARN] {channel} {set_name} failed: {exc}")
            if sleep_sec > 0:
                time.sleep(float(sleep_sec))

    raise ImageGenerationError(f"Failed to generate {channel} {set_name}: {last_exc}")


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate Buddha base images for CH13-CH17.")
    ap.add_argument("--channels", nargs="*", default=["CH13", "CH14", "CH15", "CH16", "CH17"])
    ap.add_argument("--sets", nargs="*", default=["setA", "setB", "setC"])
    ap.add_argument("--model-key", default="fireworks_flux_1_schnell_fp8")
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--aspect-ratio", default="16:9")
    ap.add_argument("--seed-base", type=int, default=130000)
    ap.add_argument("--sleep-sec", type=float, default=0.35)
    ap.add_argument("--max-attempts", type=int, default=3)
    ap.add_argument("--no-fallback", action="store_false", dest="allow_fallback", default=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    channels = [_normalize_channel(c) for c in (args.channels or []) if str(c).strip()]
    sets = [str(s).strip() for s in (args.sets or []) if str(s).strip()]
    for ch in channels:
        if ch not in STYLE_BY_CHANNEL:
            raise SystemExit(f"unsupported channel for this generator: {ch}")
    for s in sets:
        if s not in COMPOSITION_BY_SET:
            raise SystemExit(f"unsupported set: {s} (expected one of: {', '.join(COMPOSITION_BY_SET)})")

    client = ImageClient()
    for ch in channels:
        ch_num = int(ch.replace("CH", "") or 0)
        for idx, set_name in enumerate(sets, start=1):
            seed = int(args.seed_base) + (ch_num * 100) + idx
            _generate_one(
                client=client,
                channel=ch,
                set_name=set_name,
                model_key=str(args.model_key),
                size=str(args.size),
                aspect_ratio=str(args.aspect_ratio),
                seed=seed,
                force=bool(args.force),
                allow_fallback=bool(getattr(args, "allow_fallback", True)),
                sleep_sec=float(args.sleep_sec),
                max_attempts=int(args.max_attempts),
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
