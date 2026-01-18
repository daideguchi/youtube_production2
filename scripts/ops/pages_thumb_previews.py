#!/usr/bin/env python3
"""
pages_thumb_previews.py — GitHub Pages用「サムネ・プレビュー」をdocs/へ生成して配信可能にする

背景（なぜ必要か）:
- `workspaces/thumbnails/projects.json` の `image_url/image_path` の多くは
  ローカル資産 `workspaces/thumbnails/assets/**` を参照する
- `assets/` は容量が大きく gitignore 対象のため、GitHub Pages / raw から参照できず、
  「モバイルでサムネ確認」が崩れる
- そこで、Pagesで配信できる軽量プレビュー（選択サムネ中心）を `docs/media/thumbs/` に生成する

出力:
- `docs/media/thumbs/CHxx/NNN.jpg`（選択サムネの軽量プレビュー）
- `docs/data/thumbs_index.json`（Pages側が参照できる簡易インデックス）

Usage:
  # 全件（選択サムネ）を生成
  python3 scripts/ops/pages_thumb_previews.py --all --write

  # 一部だけ（例）
  python3 scripts/ops/pages_thumb_previews.py --channel CH01 --video 001 --write
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from _bootstrap import bootstrap

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except Exception:  # pragma: no cover
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]
    ImageFont = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]


CHANNEL_RE = re.compile(r"^CH\d{2}$")
VIDEO_RE = re.compile(r"^\d{3}$")


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_channel(raw: str) -> str:
    s = str(raw or "").strip().upper()
    if re.fullmatch(r"CH\d{2}", s):
        return s
    m = re.fullmatch(r"CH(\d+)", s)
    if m:
        return f"CH{int(m.group(1)):02d}"
    return s


def _normalize_video(raw: str) -> str:
    s = str(raw or "").strip()
    if re.fullmatch(r"\d{3}", s):
        return s
    try:
        return f"{int(s):03d}"
    except Exception:
        return s


def _video_id(channel: str, video: str) -> str:
    return f"{channel}-{video}"


def _projects_json_path(repo_root: Path) -> Path:
    return repo_root / "workspaces" / "thumbnails" / "projects.json"


def _assets_root(repo_root: Path) -> Path:
    return repo_root / "workspaces" / "thumbnails" / "assets"


def _thumbs_root(repo_root: Path) -> Path:
    return repo_root / "docs" / "media" / "thumbs"


def _thumb_preview_rel(channel: str, video: str) -> str:
    # docs/ is Pages root; use site-relative path from docs/index.html
    return f"media/thumbs/{channel}/{video}.jpg"


def _thumb_preview_path(repo_root: Path, channel: str, video: str) -> Path:
    return _thumbs_root(repo_root) / channel / f"{video}.jpg"


def _variant_image_path(variant: dict[str, Any]) -> str:
    """
    Prefer `image_path` (relative to workspaces/thumbnails/assets/).
    Fallback: derive from `image_url` like `/thumbnails/assets/CH01/001/x.png`.
    """
    ip = str(variant.get("image_path") or "").strip().lstrip("/")
    if ip:
        return ip
    url = str(variant.get("image_url") or "").strip()
    prefix = "/thumbnails/assets/"
    if url.startswith(prefix):
        return url[len(prefix) :].lstrip("/")
    return ""


def _load_projects(repo_root: Path) -> list[dict[str, Any]]:
    path = _projects_json_path(repo_root)
    if not path.exists():
        raise SystemExit(f"projects.json not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    projects = data.get("projects")
    return projects if isinstance(projects, list) else []


def _select_selected_variant(project: dict[str, Any]) -> dict[str, Any] | None:
    selected_id = str(project.get("selected_variant_id") or "").strip()
    variants = project.get("variants")
    if not selected_id or not isinstance(variants, list):
        return None
    for v in variants:
        if not isinstance(v, dict):
            continue
        if str(v.get("id") or "").strip() == selected_id:
            return v
    return None


def _write_preview_jpg(*, src: Path, dest: Path, width: int, quality: int) -> None:
    if Image is None or ImageOps is None:  # pragma: no cover
        raise RuntimeError("Pillow (PIL) is required to generate previews.")

    with Image.open(src) as im0:
        im = ImageOps.exif_transpose(im0)
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")

        im.thumbnail((int(width), 10_000_000), resample=Image.Resampling.LANCZOS)

        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        im.save(tmp, format="JPEG", quality=int(quality), optimize=True, progressive=True)
        tmp.replace(dest)


def _load_docs_index_video_items(repo_root: Path) -> list[dict[str, Any]]:
    """
    Load docs/data/index.json (pages script viewer index).

    This index is larger than thumbnails projects.json and is used to
    generate *placeholder* thumbs for episodes that don't have a thumbnail
    project yet (so mobile Pages doesn't show broken images).
    """
    path = repo_root / "docs" / "data" / "index.json"
    if not path.exists():
        return []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        items = obj.get("items") if isinstance(obj, dict) else None
        return items if isinstance(items, list) else []
    except Exception:
        return []


def _load_font(size: int):
    if ImageFont is None:  # pragma: no cover
        return None
    try:
        return ImageFont.truetype("DejaVuSans.ttf", size=size)
    except Exception:  # pragma: no cover
        try:
            return ImageFont.load_default()
        except Exception:
            return None


def _draw_centered_text(draw: Any, *, text: str, y: int, font: Any, fill: tuple[int, int, int], width: int) -> int:
    if not text:
        return y
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = int(bbox[2] - bbox[0])
        th = int(bbox[3] - bbox[1])
    except Exception:  # pragma: no cover
        tw, th = 0, 0
    x = max(0, int((width - tw) / 2))
    draw.text((x, int(y)), text, font=font, fill=fill)
    return int(y + th)


def _write_placeholder_thumb_jpg(*, dest: Path, video_id: str, title: str, width: int, quality: int) -> None:
    if Image is None or ImageDraw is None:  # pragma: no cover
        raise RuntimeError("Pillow (PIL) is required to generate placeholder previews.")

    # Generate at a larger base resolution then downscale to `width`.
    base_w = max(int(width) * 2, 1280)
    base_h = int(round(base_w * 9 / 16))
    bg = (19, 24, 32)  # dark
    fg = (231, 238, 247)
    muted = (155, 176, 198)

    im = Image.new("RGB", (base_w, base_h), bg)
    draw = ImageDraw.Draw(im)

    pad = int(base_w * 0.06)
    try:
        draw.rectangle((pad, pad, base_w - pad, base_h - pad), outline=(46, 57, 74), width=3)
    except Exception:
        pass

    font_main = _load_font(int(base_w * 0.10)) or _load_font(48)
    font_sub = _load_font(int(base_w * 0.045)) or _load_font(22)
    font_title = _load_font(int(base_w * 0.035)) or _load_font(18)

    y = int(base_h * 0.32)
    y = _draw_centered_text(draw, text=video_id, y=y, font=font_main, fill=fg, width=base_w) + int(base_h * 0.02)
    y = _draw_centered_text(draw, text="THUMBNAIL MISSING", y=y, font=font_sub, fill=muted, width=base_w) + int(
        base_h * 0.03
    )
    if title:
        one_line = " ".join(str(title).strip().split())
        if len(one_line) > 80:
            one_line = one_line[:77] + "..."
        _draw_centered_text(draw, text=one_line, y=y, font=font_title, fill=muted, width=base_w)

    im.thumbnail((int(width), 10_000_000), resample=Image.Resampling.LANCZOS)

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    im.save(tmp, format="JPEG", quality=int(quality), optimize=True, progressive=True)
    tmp.replace(dest)


def _fill_missing_placeholders(repo_root: Path, *, width: int, quality: int) -> tuple[int, int]:
    written = 0
    skipped_exists = 0
    for raw in _load_docs_index_video_items(repo_root):
        if not isinstance(raw, dict):
            continue
        ch = _normalize_channel(str(raw.get("channel") or ""))
        vv = _normalize_video(str(raw.get("video") or ""))
        if not (CHANNEL_RE.match(ch) and VIDEO_RE.match(vv)):
            continue
        dest = _thumb_preview_path(repo_root, ch, vv)
        if dest.exists():
            skipped_exists += 1
            continue
        vid = str(raw.get("video_id") or _video_id(ch, vv)).strip() or _video_id(ch, vv)
        title = str(raw.get("title") or "").strip()
        _write_placeholder_thumb_jpg(dest=dest, video_id=vid, title=title, width=int(width), quality=int(quality))
        written += 1
    return written, skipped_exists


@dataclass(frozen=True)
class ThumbIndexItem:
    video_id: str
    channel: str
    video: str
    status: str
    selected_variant_id: str
    selected_image_path: str
    src_rel: str
    src_exists: bool
    preview_rel: str
    preview_exists: bool


def _iter_targets(
    projects: list[dict[str, Any]],
    *,
    channels: set[str],
    videos: set[str],
    all_items: bool,
) -> Iterable[dict[str, Any]]:
    for p in projects:
        if not isinstance(p, dict):
            continue
        ch = _normalize_channel(str(p.get("channel") or ""))
        vv = _normalize_video(str(p.get("video") or ""))
        if not (CHANNEL_RE.match(ch) and VIDEO_RE.match(vv)):
            continue
        if not all_items:
            if channels and ch not in channels:
                continue
            if videos and vv not in videos:
                continue
        yield p


def build_thumb_index(
    repo_root: Path,
    *,
    channels: set[str],
    videos: set[str],
    all_items: bool,
) -> list[ThumbIndexItem]:
    assets_root = _assets_root(repo_root)
    items: list[ThumbIndexItem] = []
    for p in _iter_targets(_load_projects(repo_root), channels=channels, videos=videos, all_items=all_items):
        ch = _normalize_channel(str(p.get("channel") or ""))
        vv = _normalize_video(str(p.get("video") or ""))
        vid = _video_id(ch, vv)
        status = str(p.get("status") or "").strip()
        sel = str(p.get("selected_variant_id") or "").strip()
        v = _select_selected_variant(p)
        if not v:
            continue
        image_path = _variant_image_path(v)
        if not image_path:
            continue
        src = assets_root / image_path
        preview_rel = _thumb_preview_rel(ch, vv)
        preview = repo_root / "docs" / preview_rel
        items.append(
            ThumbIndexItem(
                video_id=vid,
                channel=ch,
                video=vv,
                status=status,
                selected_variant_id=sel,
                selected_image_path=image_path,
                src_rel=str(Path("workspaces") / "thumbnails" / "assets" / image_path),
                src_exists=src.exists(),
                preview_rel=preview_rel,
                preview_exists=preview.exists(),
            )
        )
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate publishable thumbnail previews for GitHub Pages.")
    ap.add_argument("--all", action="store_true", help="Process all projects (recommended for first run)")
    ap.add_argument("--channel", action="append", default=[], help="Channel code (repeatable). e.g. CH01")
    ap.add_argument("--video", action="append", default=[], help="Video number (repeatable). e.g. 001")
    ap.add_argument("--width", type=int, default=640, help="Preview max width (default: 640)")
    ap.add_argument("--quality", type=int, default=85, help="JPEG quality (default: 85)")
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing previews")
    ap.add_argument(
        "--fill-missing-placeholders",
        action="store_true",
        help="Also generate placeholder thumbs under docs/media/thumbs/ for episodes missing any preview (uses docs/data/index.json).",
    )
    ap.add_argument("--write", action="store_true", help="Write previews + thumbs_index.json (default: dry-run)")
    args = ap.parse_args()

    repo_root = bootstrap(load_env=False)

    channels = {_normalize_channel(x) for x in (args.channel or []) if str(x or "").strip()}
    videos = {_normalize_video(x) for x in (args.video or []) if str(x or "").strip()}
    all_items = bool(args.all)
    if not all_items and not (channels or videos):
        ap.error("Specify --all OR at least one of --channel/--video.")

    items = build_thumb_index(repo_root, channels=channels, videos=videos, all_items=all_items)
    if not items:
        print("[pages_thumb_previews] no targets (filtered out or missing selected_variant_id/image_path).")
        return 0

    assets_root = _assets_root(repo_root)
    written = 0
    skipped_exists = 0
    missing_src = 0
    placeholder_written = 0

    if args.write:
        for it in items:
            src = assets_root / it.selected_image_path
            if not src.exists():
                missing_src += 1
                continue
            dest = _thumb_preview_path(repo_root, it.channel, it.video)
            if dest.exists() and not args.overwrite:
                skipped_exists += 1
                continue
            _write_preview_jpg(src=src, dest=dest, width=int(args.width), quality=int(args.quality))
            written += 1
        if bool(args.fill_missing_placeholders):
            placeholder_written, _placeholder_skipped = _fill_missing_placeholders(
                repo_root, width=int(args.width), quality=int(args.quality)
            )

    # Always write index when --write (kept small; no secrets).
    out_payload = {
        "version": 1,
        "updated_at": _now_iso_utc(),
        "count": len(items),
        "items": [
            {
                "video_id": it.video_id,
                "channel": it.channel,
                "video": it.video,
                "status": it.status,
                "selected_variant_id": it.selected_variant_id,
                "selected_image_path": it.selected_image_path,
                "src_rel": it.src_rel,
                "src_exists": it.src_exists,
                "preview_rel": it.preview_rel,
                "preview_exists": (repo_root / "docs" / it.preview_rel).exists(),
            }
            for it in items
        ],
    }

    if args.write:
        idx_path = repo_root / "docs" / "data" / "thumbs_index.json"
        idx_path.parent.mkdir(parents=True, exist_ok=True)
        idx_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    mode = "WRITE" if args.write else "DRY"
    print(
        f"[pages_thumb_previews] mode={mode} targets={len(items)} written={written} placeholder_written={placeholder_written} skipped_exists={skipped_exists} missing_src={missing_src}"
    )
    if not args.write:
        print("Dry-run only. Re-run with --write to generate previews and index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
