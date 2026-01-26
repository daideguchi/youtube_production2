#!/usr/bin/env python3
"""
thumbnails_placeholders.py — create placeholder thumbnail PNGs for missing assets.

Why:
- UI expects thumbnail files under `workspaces/thumbnails/assets/{CH}/{NNN}/...`.
- When the file is missing, the local UI shows broken images and work stalls.

Policy:
- Never overwrite existing files.
- Default scope is **selected variants only** (small + practical).
- This is a *safety valve*; real thumbnails should be built via thumbnails pipeline.

SSOT:
- ssot/ops/OPS_IMAGE_DDD_STORAGE_MAP_AND_APPROVAL.md
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Iterable

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=True)

from factory_common import paths as repo_paths  # noqa: E402


REPORT_SCHEMA = "ytm.ops.thumbnails_placeholders.v1"


def _now_iso_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _now_compact_utc() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _logs_dir() -> Path:
    return repo_paths.logs_root() / "ops" / "thumbnails_placeholders"


def _report_path(stamp: str) -> Path:
    return _logs_dir() / f"thumbnails_placeholders__{stamp}.json"


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _iter_projects(path: Path) -> Iterable[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    for p in payload.get("projects") or []:
        if isinstance(p, dict):
            yield p


def _coerce_size(token: str) -> tuple[int, int]:
    raw = (token or "").lower().strip()
    if "x" not in raw:
        raise ValueError("expected WxH (e.g. 1920x1080)")
    w_s, h_s = raw.split("x", 1)
    w, h = int(w_s), int(h_s)
    if w <= 0 or h <= 0:
        raise ValueError("size must be positive")
    return w, h


def _make_placeholder_png(*, out_path: Path, size: tuple[int, int], lines: list[str]) -> None:
    from PIL import Image, ImageDraw, ImageFont

    width, height = size
    img = Image.new("RGBA", (width, height), (24, 24, 28, 255))
    draw = ImageDraw.Draw(img)

    # Border (visible even when zoomed out).
    border = 16
    draw.rectangle([0, 0, width - 1, height - 1], outline=(220, 60, 60, 255), width=border)

    # Fonts: try a few common macOS fonts, fall back to default.
    font_paths = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Verdana.ttf",
        "/System/Library/Fonts/Supplemental/Tahoma.ttf",
    ]
    font_big = None
    font_small = None
    for fp in font_paths:
        p = Path(fp)
        if not p.exists():
            continue
        try:
            font_big = ImageFont.truetype(str(p), 96)
            font_small = ImageFont.truetype(str(p), 44)
            break
        except Exception:
            continue
    if font_big is None or font_small is None:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Render centered.
    lines = [str(x).strip() for x in lines if str(x).strip()]
    if not lines:
        lines = ["MISSING THUMBNAIL"]

    y = int(height * 0.34)
    for i, text in enumerate(lines[:6]):
        font = font_big if i == 0 else font_small
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (width - text_w) // 2
        draw.text((x, y), text, font=font, fill=(245, 245, 250, 255))
        y += text_h + 18

    _ensure_dir(out_path.parent)
    img.save(out_path, format="PNG", optimize=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Create placeholder PNGs for missing thumbnail assets (dry-run by default).")
    ap.add_argument("--run", action="store_true", help="Write files (default: dry-run).")
    ap.add_argument("--channel", required=True, help="Channel code (e.g. CH02).")
    ap.add_argument("--videos", nargs="*", default=[], help="Optional video numbers to limit (e.g. 042 043).")
    ap.add_argument("--all-variants", action="store_true", help="Generate for all missing variants (default: selected only).")
    ap.add_argument("--size", default="1920x1080", help="Placeholder size WxH (default: 1920x1080).")
    args = ap.parse_args()

    channel = str(args.channel or "").strip().upper()
    if not channel.startswith("CH"):
        raise SystemExit("[POLICY] --channel must be like CH02")
    limit_videos = {str(v).strip().zfill(3) for v in (args.videos or []) if str(v).strip()}
    size = _coerce_size(str(args.size))

    thumbs_root = repo_paths.thumbnails_root()
    assets_root = thumbs_root / "assets"
    projects_path = thumbs_root / "projects.json"
    if not projects_path.exists():
        raise SystemExit(f"[MISSING] projects.json: {projects_path}")
    if not assets_root.exists():
        raise SystemExit(f"[MISSING] thumbnails assets root: {assets_root}")

    stamp = _now_compact_utc()
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "generated_at": _now_iso_utc(),
        "run": bool(args.run),
        "channel": channel,
        "size": {"w": size[0], "h": size[1]},
        "paths": {"repo_root": str(REPO_ROOT), "projects_path": str(projects_path), "assets_root": str(assets_root)},
        "created": [],
        "skipped": [],
    }

    created_count = 0
    skipped_count = 0
    for project in _iter_projects(projects_path):
        if str(project.get("channel") or "").strip().upper() != channel:
            continue
        video = str(project.get("video") or "").strip()
        video_norm = video.zfill(3) if video.isdigit() else video
        if limit_videos and video_norm not in limit_videos:
            continue

        selected_id = str(project.get("selected_variant_id") or "").strip()
        for variant in project.get("variants") or []:
            if not isinstance(variant, dict):
                continue
            variant_id = str(variant.get("id") or "").strip()
            if not variant_id:
                continue
            is_selected = bool(selected_id) and (variant_id == selected_id)
            if (not bool(args.all_variants)) and (not is_selected):
                continue

            image_path = str(variant.get("image_path") or "").strip()
            if not image_path:
                continue

            out_path = assets_root / Path(image_path)
            if out_path.exists():
                skipped_count += 1
                report["skipped"].append({"video": video_norm, "variant_id": variant_id, "path": str(out_path), "reason": "exists"})
                continue

            lines = [
                f"{channel} {video_norm}",
                "MISSING THUMBNAIL (placeholder)",
                out_path.name,
            ]
            if bool(args.run):
                _make_placeholder_png(out_path=out_path, size=size, lines=lines)
            created_count += 1
            report["created"].append({"video": video_norm, "variant_id": variant_id, "path": str(out_path), "selected": is_selected})

    _ensure_dir(_logs_dir())
    rp = _report_path(stamp)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mode = "RUN" if bool(args.run) else "DRY"
    print(f"[thumbnails_placeholders] {mode} report={rp} created={created_count} skipped={skipped_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

