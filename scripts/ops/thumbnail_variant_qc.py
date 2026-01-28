#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
thumbnail_variant_qc.py — build a QC contactsheet for thumbnail *variants* of a single video.

Why
----
CH32（等）では「同じ動画に対して複数案」を作り、目視で比較するのが重要。
このスクリプトは、`assets/{CH}/{NNN}/` 配下の variant 画像を集めて、
`assets/{CH}/library/qc/` に contactsheet を書き出す。

Safety
------
- 外部LLM/API を呼ばない（課金事故を避ける）
- `projects.json` は触らない（他エージェントと衝突しやすいSoTのため）
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

from _bootstrap import bootstrap

bootstrap(load_env=True)

from factory_common import paths as fpaths  # noqa: E402
from script_pipeline.thumbnails.io_utils import save_png_atomic  # noqa: E402

from PIL import Image, ImageDraw, ImageFont, ImageOps  # noqa: E402


def _normalize_channel(ch: str) -> str:
    return str(ch or "").strip().upper()


def _normalize_video(v: str) -> str:
    digits = "".join(c for c in str(v or "").strip() if c.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {v}")
    return digits.zfill(3)


def _pick_font(size_px: int) -> ImageFont.ImageFont:
    size = max(10, min(64, int(size_px)))
    for cand in (
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ):
        try:
            return ImageFont.truetype(cand, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _iter_glob(base_dir: Path, pattern: str) -> Iterable[Path]:
    # Path.glob does not support absolute patterns. We keep everything relative.
    pat = str(pattern or "").strip()
    if not pat:
        return []
    return base_dir.glob(pat)


def _collect_variant_images(*, base_dir: Path, includes: List[str]) -> List[Tuple[str, Path]]:
    items: List[Tuple[str, Path]] = []
    seen: set[str] = set()
    for pat in includes:
        for p in sorted(_iter_glob(base_dir, pat), key=lambda x: x.as_posix()):
            if not p.is_file():
                continue
            if p.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            key = str(p.resolve())
            if key in seen:
                continue
            seen.add(key)
            label = p.relative_to(base_dir).as_posix()
            items.append((label, p))
    return items


def _build_contactsheet(
    *,
    items: List[Tuple[str, Path]],
    out_path: Path,
    cols: int,
    tile_w: int,
    tile_h: int,
    pad: int,
) -> Path:
    cols = max(1, int(cols))
    tile_w = max(120, int(tile_w))
    tile_h = max(90, int(tile_h))
    pad = max(0, int(pad))

    rows = (len(items) + cols - 1) // cols
    W = cols * tile_w + (cols + 1) * pad
    H = rows * tile_h + (rows + 1) * pad
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _pick_font(max(16, int(round(tile_h * 0.08))))

    for i, (label, path) in enumerate(items):
        r = i // cols
        c = i % cols
        x = pad + c * (tile_w + pad)
        y = pad + r * (tile_h + pad)

        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im = im.resize((tile_w, tile_h), Image.Resampling.LANCZOS)
        except Exception:
            im = Image.new("RGB", (tile_w, tile_h), (30, 30, 30))
            canvas.paste(im, (x, y))
            draw.text((x + 10, y + 10), f"ERROR {Path(label).name}", fill=(255, 80, 80), font=font)
            continue

        canvas.paste(im, (x, y))

        # Short label: keep last 2 path components if needed.
        parts = label.split("/")
        short = parts[-1]
        if len(parts) >= 2:
            short = f"{parts[-2]}/{parts[-1]}"
        short = short.replace("__with_text", "").replace(".png", "").replace(".jpg", "").replace(".jpeg", "")

        bbox = draw.textbbox((0, 0), short, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        lx = x + tile_w - 8 - tw
        ly = y + tile_h - 8 - th
        draw.rectangle((lx - 6, ly - 4, lx + tw + 6, ly + th + 4), fill=(0, 0, 0))
        draw.text((lx, ly), short, fill=(255, 255, 255), font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_png_atomic(canvas, out_path, mode="final", verify=True)
    return out_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build a contactsheet for thumbnail variants of a single video.")
    ap.add_argument("--channel", required=True)
    ap.add_argument("--video", required=True, help="video number (e.g. 037)")
    ap.add_argument(
        "--include",
        action="append",
        default=[],
        help="glob pattern relative to assets/{CH}/{NNN}/ (repeatable). default includes common variant patterns",
    )
    ap.add_argument("--out", default="", help="output PNG path (default: assets/{CH}/library/qc/contactsheet_variants_{NNN}.png)")
    ap.add_argument("--tile-w", type=int, default=480)
    ap.add_argument("--tile-h", type=int, default=270)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--pad", type=int, default=12)
    args = ap.parse_args(argv)

    ch = _normalize_channel(args.channel)
    vid = _normalize_video(args.video)
    base_dir = fpaths.thumbnail_assets_dir(ch, vid)
    if not base_dir.exists():
        raise SystemExit(f"assets dir not found: {base_dir}")

    includes = [str(x) for x in (args.include or []) if str(x).strip()]
    if not includes:
        includes = [
            "00_thumb*.png",
            "variants/**/*.png",
            "compiler/**/out_01.png",
            "compiler/**/out_02.png",
        ]

    items = _collect_variant_images(base_dir=base_dir, includes=includes)
    if not items:
        raise SystemExit(f"no images matched under {base_dir} (include={includes})")

    out = Path(str(args.out)).expanduser() if str(args.out or "").strip() else None
    if out is None:
        out = fpaths.thumbnails_root() / "assets" / ch / "library" / "qc" / f"contactsheet_variants_{vid}.png"

    wrote = _build_contactsheet(
        items=items,
        out_path=out,
        cols=int(args.cols),
        tile_w=int(args.tile_w),
        tile_h=int(args.tile_h),
        pad=int(args.pad),
    )
    print(f"[QC] wrote {wrote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

