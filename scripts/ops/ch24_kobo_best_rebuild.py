#!/usr/bin/env python3
"""
ch24_kobo_best_rebuild.py — rebuild CH24 "kobo_best" thumbnail PNGs from Planning SSOT.

Policy:
- NO placeholders (no red-border dummy images).
- Deterministic + fast (PIL only; no external image generation).
- Write only the expected canonical paths:
  - workspaces/thumbnails/assets/CH24/{NNN}/{NNN}_kobo_best.png
  - workspaces/thumbnails/assets/CH24/kobo_text_layer_spec_30.json
- Update workspaces/thumbnails/projects.json timestamps for CH24 so the UI busts cached 404s.

SSOT:
- workspaces/planning/channels/CH24.csv
- workspaces/planning/personas/CH24_PERSONA.md
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from _bootstrap import bootstrap

REPO_ROOT = bootstrap(load_env=True)

from factory_common import paths as repo_paths  # noqa: E402


REPORT_SCHEMA = "ytm.ops.thumbnails.ch24_kobo_best_rebuild.v1"


def _now_iso_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now_compact_utc() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _logs_dir() -> Path:
    return repo_paths.logs_root() / "ops" / "thumbnails_ch24_kobo_best_rebuild"


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _report_path(stamp: str) -> Path:
    return _logs_dir() / f"ch24_kobo_best_rebuild__{stamp}.json"


def _normalize_channel(ch: str) -> str:
    return str(ch or "").strip().upper()


def _normalize_video(v: str) -> str:
    digits = "".join(ch for ch in str(v or "").strip() if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {v}")
    return digits.zfill(3)


def _planning_csv_path(channel: str) -> Path:
    return repo_paths.planning_root() / "channels" / f"{_normalize_channel(channel)}.csv"


def _thumb_assets_root(channel: str) -> Path:
    return repo_paths.thumbnails_root() / "assets" / _normalize_channel(channel)


def _projects_json_path() -> Path:
    return repo_paths.thumbnails_root() / "projects.json"


def _load_projects_json() -> dict[str, Any]:
    return json.loads(_projects_json_path().read_text(encoding="utf-8"))


def _write_projects_json(doc: dict[str, Any]) -> None:
    path = _projects_json_path()
    doc["version"] = int(doc.get("version") or 1)
    doc["updated_at"] = _now_iso_utc()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _font_path_candidates() -> list[str]:
    # Prefer CH24 policy: Noto Sans JP Black (variable font) if available in workspace,
    # otherwise fall back to heavy Japanese system fonts available on macOS by default.
    return [
        str(repo_paths.thumbnails_root() / "assets" / "_fonts" / "NotoSansJP_wght.ttf"),
        "/System/Library/Fonts/ヒラギノ角ゴシック W8.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W7.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]


def _pick_font_spec() -> tuple[Optional[str], Optional[str]]:
    """
    Return (font_path, variation_name).
    variation_name is used only for variable fonts like NotoSansJP_wght.ttf.
    """
    for fp in _font_path_candidates():
        if not fp:
            continue
        p = Path(fp).expanduser()
        if p.exists():
            if p.name == "NotoSansJP_wght.ttf":
                return (str(p), "Black")
            return (str(p), None)
    return (None, None)


def _apply_font_variation(font: Any, variation: Optional[str]) -> None:
    if not variation:
        return
    v = str(variation).strip()
    if not v:
        return
    if not hasattr(font, "set_variation_by_name"):
        return
    try:
        font.set_variation_by_name(v)
    except Exception:
        return


@dataclass(frozen=True)
class Ch24TextSpecItem:
    video: str
    top: str
    bottom: str
    red_words: tuple[str, ...]


def _extract_red_words(text: str) -> tuple[str, ...]:
    raw = str(text or "")
    m = re.search(r"赤ワード[:：]\\s*([^。\\n\\r]+)", raw)
    if not m:
        return ()
    token = m.group(1).strip()
    if not token:
        return ()
    out: list[str] = []
    for part in re.split(r"[、,\\s]+", token):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return tuple(out)


def _iter_ch24_text_spec_from_planning(*, limit_videos: set[str] | None = None) -> Iterable[Ch24TextSpecItem]:
    path = _planning_csv_path("CH24")
    if not path.exists():
        raise SystemExit(f"[MISSING] planning csv: {path}")

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if _normalize_channel(row.get("チャンネル") or row.get("channel") or "CH24") != "CH24":
                continue
            video = _normalize_video(row.get("動画番号") or row.get("video") or "")
            if limit_videos and video not in limit_videos:
                continue
            top = str(row.get("サムネタイトル上") or "").strip()
            bottom = str(row.get("サムネタイトル下") or "").strip()
            if not top and not bottom:
                continue
            red_words = _extract_red_words(str(row.get("企画意図") or ""))
            yield Ch24TextSpecItem(video=video, top=top, bottom=bottom, red_words=red_words)


def _write_ch24_text_layer_spec_30_json(*, items: Sequence[Ch24TextSpecItem], out_path: Path, run: bool) -> None:
    payload: dict[str, Any] = {
        "schema": "ytm.thumb_text_layer_spec.ch24.v1",
        "generated_at": _now_iso_utc(),
        "channel": "CH24",
        "items": [
            {
                "video": it.video,
                "top": it.top,
                "bottom": it.bottom,
                "red_words": list(it.red_words),
            }
            for it in items
        ],
    }
    if not run:
        return
    _ensure_dir(out_path.parent)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out_path)


def _split_first_match(line: str, words: Sequence[str]) -> tuple[str, Optional[str], str]:
    if not words:
        return (line, None, "")
    best = None
    for w in words:
        w = str(w or "").strip()
        if not w:
            continue
        idx = line.find(w)
        if idx < 0:
            continue
        if best is None or idx < best[0]:
            best = (idx, w)
    if best is None:
        return (line, None, "")
    idx, w = best
    return (line[:idx], w, line[idx + len(w) :])


def _render_text_block(
    *,
    img: Any,
    x: int,
    y: int,
    max_w: int,
    lines: list[str],
    font_path: str,
    font_variation: Optional[str],
    start_size: int,
    fill_rgba: tuple[int, int, int, int],
    stroke_fill_rgba: tuple[int, int, int, int],
    stroke_width: int,
    line_gap_px: int,
    red_words: Sequence[str],
) -> None:
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(img)
    size = int(start_size)
    while size >= 24:
        font = ImageFont.truetype(font_path, size)
        _apply_font_variation(font, font_variation)
        ok = True
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font, stroke_width=stroke_width)
            if (bbox[2] - bbox[0]) > max_w:
                ok = False
                break
        if ok:
            break
        size -= 4
    font = ImageFont.truetype(font_path, max(24, size))
    _apply_font_variation(font, font_variation)

    cur_y = int(y)
    for raw in lines:
        line = str(raw or "").rstrip("\n")
        if not line.strip():
            cur_y += int(font.size * 0.90) + int(line_gap_px)
            continue
        pre, hit, post = _split_first_match(line, red_words)
        cur_x = int(x)
        if hit is None:
            draw.text(
                (cur_x, cur_y),
                line,
                font=font,
                fill=fill_rgba,
                stroke_width=stroke_width,
                stroke_fill=stroke_fill_rgba,
            )
        else:
            if pre:
                draw.text(
                    (cur_x, cur_y),
                    pre,
                    font=font,
                    fill=fill_rgba,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_fill_rgba,
                )
                pre_bbox = draw.textbbox((0, 0), pre, font=font, stroke_width=stroke_width)
                pre_w = pre_bbox[2] - pre_bbox[0]
                cur_x += int(pre_w)

            draw.text(
                (cur_x, cur_y),
                hit,
                font=font,
                fill=(214, 31, 31, 255),
                stroke_width=int(stroke_width + 2),
                stroke_fill=stroke_fill_rgba,
            )
            hit_bbox = draw.textbbox((0, 0), hit, font=font, stroke_width=int(stroke_width + 2))
            hit_w = hit_bbox[2] - hit_bbox[0]
            cur_x += int(hit_w)

            if post:
                draw.text(
                    (cur_x, cur_y),
                    post,
                    font=font,
                    fill=fill_rgba,
                    stroke_width=stroke_width,
                    stroke_fill=stroke_fill_rgba,
                )

        cur_y += int(font.size * 0.90) + int(line_gap_px)


def _make_kobo_best_png(*, out_path: Path, top: str, bottom: str, red_words: Sequence[str], run: bool) -> None:
    from PIL import Image, ImageChops

    w, h = 1920, 1080
    text_w = int(w * 0.35)
    pad = 64

    # Background (deterministic-ish, no network).
    base = Image.new("RGBA", (w, h), (12, 18, 32, 255))

    # Subtle gradient band to the right.
    grad = Image.linear_gradient("L").resize((w, h))
    tint = Image.new("RGBA", (w, h), (38, 54, 92, 255))
    base = Image.composite(tint, base, grad)

    # Darken left text area for readability.
    text_panel = Image.new("RGBA", (text_w + pad, h), (0, 0, 0, 110))
    base.alpha_composite(text_panel, (0, 0))

    # Add a faint diagonal highlight on the right (gives it a "designed" look).
    highlight = Image.new("RGBA", (w, h), (255, 255, 255, 0))
    for i in range(0, h, 6):
        x0 = int(w * 0.52 + (i * 0.18))
        x1 = x0 + int(w * 0.08)
        if x0 >= w:
            continue
        for x in range(max(0, x0), min(w, x1), 3):
            highlight.putpixel((x, i), (255, 255, 255, 10))
    base = Image.alpha_composite(base, highlight)

    # Soft vignette.
    vig = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    for i in range(0, 120):
        a = int(140 * (i / 120.0))
        ImageChops.add(vig, Image.new("RGBA", (w, h), (0, 0, 0, 0)))  # keep type checker happy
        # border rectangles
        from PIL import ImageDraw

        d = ImageDraw.Draw(vig)
        d.rectangle([i, i, w - i - 1, h - i - 1], outline=(0, 0, 0, a))
    base = Image.alpha_composite(base, vig)

    font_path, font_variation = _pick_font_spec()
    if not font_path:
        raise RuntimeError("Japanese-capable font not found on this host")

    top_lines = [x.strip() for x in str(top or "").splitlines() if x.strip()]
    bottom_lines = [x.strip() for x in str(bottom or "").splitlines() if x.strip()]
    if not top_lines:
        top_lines = ["（未設定）"]
    if not bottom_lines:
        bottom_lines = ["（未設定）"]

    # Title blocks.
    _render_text_block(
        img=base,
        x=pad,
        y=int(h * 0.16),
        max_w=text_w - pad * 2,
        lines=top_lines,
        font_path=font_path,
        font_variation=font_variation,
        start_size=92,
        fill_rgba=(250, 250, 252, 255),
        stroke_fill_rgba=(0, 0, 0, 255),
        stroke_width=10,
        line_gap_px=18,
        red_words=red_words,
    )
    _render_text_block(
        img=base,
        x=pad,
        y=int(h * 0.36),
        max_w=text_w - pad * 2,
        lines=bottom_lines,
        font_path=font_path,
        font_variation=font_variation,
        start_size=140,
        fill_rgba=(250, 250, 252, 255),
        stroke_fill_rgba=(0, 0, 0, 255),
        stroke_width=12,
        line_gap_px=22,
        red_words=red_words,
    )

    # Fixed vertical tag (subtle; does not include episode-specific text).
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(base)
    v_font = ImageFont.truetype(font_path, 64)
    _apply_font_variation(v_font, font_variation)
    v_text = "弘\n法\n大\n師"
    draw.multiline_text(
        (int(w * 0.90), int(h * 0.18)),
        v_text,
        font=v_font,
        fill=(245, 210, 120, 160),
        stroke_width=6,
        stroke_fill=(0, 0, 0, 140),
        spacing=6,
        align="center",
    )

    if not run:
        return
    _ensure_dir(out_path.parent)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    base.save(tmp, format="PNG", optimize=True)
    tmp.replace(out_path)


def _update_projects_json_timestamps(*, channel: str, videos: set[str], run: bool) -> tuple[int, int]:
    if not videos:
        return (0, 0)
    doc = _load_projects_json()
    now = _now_iso_utc()
    projects = doc.get("projects")
    if not isinstance(projects, list):
        return (0, 0)
    touched_projects = 0
    touched_variants = 0
    for p in projects:
        if not isinstance(p, dict):
            continue
        if _normalize_channel(p.get("channel") or "") != _normalize_channel(channel):
            continue
        vid = _normalize_video(p.get("video") or "")
        if vid not in videos:
            continue
        if p.get("updated_at") != now:
            p["updated_at"] = now
            touched_projects += 1
        variants = p.get("variants")
        if not isinstance(variants, list):
            continue
        for v in variants:
            if not isinstance(v, dict):
                continue
            if v.get("updated_at") != now:
                v["updated_at"] = now
                touched_variants += 1
    if run and (touched_projects or touched_variants):
        _write_projects_json(doc)
    return (touched_projects, touched_variants)


def main() -> int:
    ap = argparse.ArgumentParser(description="Rebuild CH24 kobo_best thumbnails from Planning SSOT (dry-run by default).")
    ap.add_argument("--run", action="store_true", help="Write files (default: dry-run).")
    ap.add_argument("--videos", nargs="*", default=[], help="Optional video numbers to limit (e.g. 001 002 030).")
    args = ap.parse_args()

    limit_videos = {_normalize_video(v) for v in (args.videos or []) if str(v).strip()} or None

    items = sorted(
        _iter_ch24_text_spec_from_planning(limit_videos=limit_videos),
        key=lambda it: it.video,
    )
    if not items:
        raise SystemExit("[OK] no CH24 rows found (nothing to do)")

    assets_root = _thumb_assets_root("CH24")
    spec_path = assets_root / "kobo_text_layer_spec_30.json"

    created = 0
    skipped = 0
    for it in items:
        out = assets_root / it.video / f"{it.video}_kobo_best.png"
        if out.exists():
            skipped += 1
            continue
        _make_kobo_best_png(out_path=out, top=it.top, bottom=it.bottom, red_words=it.red_words, run=bool(args.run))
        created += 1

    _write_ch24_text_layer_spec_30_json(items=items, out_path=spec_path, run=bool(args.run))
    touched_p, touched_v = _update_projects_json_timestamps(channel="CH24", videos={it.video for it in items}, run=bool(args.run))

    stamp = _now_compact_utc()
    report = {
        "schema": REPORT_SCHEMA,
        "generated_at": _now_iso_utc(),
        "run": bool(args.run),
        "channel": "CH24",
        "paths": {
            "repo_root": str(REPO_ROOT),
            "planning_csv": str(_planning_csv_path("CH24")),
            "assets_root": str(assets_root),
            "kobo_text_layer_spec_30": str(spec_path),
            "projects_json": str(_projects_json_path()),
        },
        "limit_videos": sorted(limit_videos) if limit_videos else None,
        "counts": {
            "items": len(items),
            "png_created": int(created),
            "png_skipped_exists": int(skipped),
            "projects_touched": int(touched_p),
            "variants_touched": int(touched_v),
        },
    }

    _ensure_dir(_logs_dir())
    rp = _report_path(stamp)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    mode = "RUN" if bool(args.run) else "DRY"
    print(f"[ch24_kobo_best_rebuild] {mode} report={rp} created={created} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
