#!/usr/bin/env python3
"""
pages_thumb_placeholders.py — GitHub Pages用「サムネ欠け」をplaceholderで埋める

目的:
- Script Viewer（docs/data/index.json）に載っている全回が、モバイルでも必ず一覧でサムネ表示される状態にする。
- 実サムネが未作成/未pushの回は、最低限 placeholder 画像を置く（“空白/壊れ画像”をなくす）。

出力:
- `docs/media/thumbs/CHxx/NNN.jpg`（存在しない場合のみ生成）

注意:
- 実サムネがある場合は上書きしない（--overwrite でのみ上書き）。
- placeholder は「未作成」を明示するための仮画像。実サムネの生成/選択は別のフローで行う。

Usage:
  # 生成（欠けている分だけ）
  python3 scripts/ops/pages_thumb_placeholders.py --write

  # 既存も含めて上書き（非推奨）
  python3 scripts/ops/pages_thumb_placeholders.py --write --overwrite
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from _bootstrap import bootstrap

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ScriptIndexItem:
    video_id: str
    channel: str
    video: str
    title: str


def _load_script_index_items(repo_root: Path) -> list[ScriptIndexItem]:
    path = repo_root / "docs" / "data" / "index.json"
    if not path.exists():
        raise SystemExit(f"missing: {path}")
    obj = json.loads(path.read_text(encoding="utf-8"))
    items = obj.get("items") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        return []
    out: list[ScriptIndexItem] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        video_id = str(it.get("video_id") or "").strip()
        channel = str(it.get("channel") or "").strip()
        video = str(it.get("video") or "").strip()
        title = str(it.get("title") or "").strip()
        if not (video_id and channel and video):
            continue
        out.append(ScriptIndexItem(video_id=video_id, channel=channel, video=video, title=title))
    return out


def _thumb_path(repo_root: Path, channel: str, video: str) -> Path:
    return repo_root / "docs" / "media" / "thumbs" / channel / f"{video}.jpg"


def _safe_title(title: str, max_len: int = 56) -> str:
    s = " ".join(str(title or "").split()).strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _load_font(size: int) -> "ImageFont.ImageFont":
    if ImageFont is None:  # pragma: no cover
        raise RuntimeError("Pillow is required")
    try:
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size=size)
    except Exception:
        return ImageFont.load_default()


def _draw_placeholder(*, video_id: str, title: str, dest: Path, width: int, height: int, quality: int) -> None:
    if Image is None or ImageDraw is None:  # pragma: no cover
        raise RuntimeError("Pillow is required to generate placeholders.")

    bg = Image.new("RGB", (int(width), int(height)), (20, 28, 55))
    d = ImageDraw.Draw(bg)

    # subtle diagonal stripes
    stripe = (36, 46, 88)
    step = 24
    for x in range(-height, width, step):
        d.line([(x, 0), (x + height, height)], fill=stripe, width=6)

    # top bar
    d.rectangle([0, 0, width, 64], fill=(15, 23, 48))
    d.rectangle([0, 0, width, 3], fill=(110, 168, 255))

    # text
    font_big = _load_font(44)
    font_mid = _load_font(22)
    font_small = _load_font(18)

    d.text((18, 14), "THUMBNAIL TBD", fill=(233, 238, 255), font=font_mid)
    d.text((18, 74), video_id, fill=(233, 238, 255), font=font_big)

    t = _safe_title(title)
    if t:
        d.text((18, 140), t, fill=(233, 238, 255), font=font_small)

    note = "この画像は placeholder（未作成）です。実サムネは別フローで生成/選択します。"
    d.text((18, height - 34), note, fill=(200, 210, 255), font=font_small)

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    bg.save(tmp, format="JPEG", quality=int(quality), optimize=True, progressive=True)
    tmp.replace(dest)


def _iter_missing(
    repo_root: Path,
    items: Iterable[ScriptIndexItem],
    *,
    overwrite: bool,
) -> Iterable[tuple[ScriptIndexItem, Path]]:
    for it in items:
        dest = _thumb_path(repo_root, it.channel, it.video)
        if dest.exists() and not overwrite:
            continue
        yield it, dest


def main() -> int:
    ap = argparse.ArgumentParser(description="Fill missing thumbnail previews with placeholders for GitHub Pages.")
    ap.add_argument("--width", type=int, default=640, help="Placeholder width (default: 640)")
    ap.add_argument("--height", type=int, default=360, help="Placeholder height (default: 360)")
    ap.add_argument("--quality", type=int, default=82, help="JPEG quality (default: 82)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing thumbs (NOT recommended)")
    ap.add_argument("--write", action="store_true", help="Write placeholder images (default: dry-run)")
    args = ap.parse_args()

    repo_root = Path(bootstrap(load_env=False))
    items = _load_script_index_items(repo_root)
    missing = list(_iter_missing(repo_root, items, overwrite=bool(args.overwrite)))
    if not missing:
        print("[pages_thumb_placeholders] no targets (all thumbs exist).")
        return 0

    if args.write:
        for it, dest in missing:
            _draw_placeholder(
                video_id=it.video_id,
                title=it.title,
                dest=dest,
                width=int(args.width),
                height=int(args.height),
                quality=int(args.quality),
            )

    mode = "WRITE" if args.write else "DRY"
    print(f"[pages_thumb_placeholders] mode={mode} targets={len(missing)}")
    if not args.write:
        print("Dry-run only. Re-run with --write to generate placeholders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

