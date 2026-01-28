#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
thumbnail_styleguide.py — YouTubeサムネを「量産可能な設計図（Styleguide/Template）」に落とす。

狙い:
- 既存のベンチマーク収集（yt-dlp）とサムネ言語化（Vision LLM）を入力として、
  1) チャンネルごとのサムネ特徴を集約（styleguide.json）
  2) Thumbnail Compiler（layer_specs v3）用のテンプレ雛形を生成（text_layout/image_prompts）
  3) 必要なら `workspaces/thumbnails/templates.json` に登録/配線
  を安全に行う。

安全設計:
- デフォルトはdry-run（書き込みなし）。`--apply` でのみ書き込み。
- 既存SoTのスキーマは壊さず「追加/追記」だけを行う。
- “完全再現”は禁止前提。出力は「特徴の抽象化 + 量産テンプレ」まで。

前提入力（SoT）:
- `scripts/ops/yt_dlp_benchmark_analyze.py` が生成した
  `workspaces/research/YouTubeベンチマーク（yt-dlp）/<UC...>/report.json`
- （任意）`scripts/ops/yt_dlp_thumbnail_analyze.py` が付与した `thumbnail_insights`

出力:
- `workspaces/research/thumbnail_styleguides/<UC...>/styleguide.json`
- `workspaces/research/thumbnail_styleguides/<UC...>/styleguide.md`
- （scaffold + --apply）:
  - `workspaces/thumbnails/compiler/layer_specs/<spec_id>.yaml`
  - `workspaces/thumbnails/templates.json` の registry/channels 追記
"""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

from _bootstrap import bootstrap

bootstrap(load_env=False)

try:
    import numpy as np  # type: ignore
    import yaml  # type: ignore
    from PIL import Image, ImageDraw, ImageFont, ImageOps  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependencies for thumbnail_styleguide.\n"
        "Install (repo venv): pip install pillow numpy pyyaml\n"
        f"error={exc}"
    )

try:
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore[assignment]

from factory_common import paths as fpaths
from factory_common.youtube_handle import (
    YouTubeHandleResolutionError,
    resolve_youtube_channel_id_from_handle,
)


YT_DLP_GENRE_DIR = "YouTubeベンチマーク（yt-dlp）"

SCHEMA_STYLEGUIDE_V1 = "ytm.thumbnail.styleguide.v1"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_norm_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return int(value) if value.is_integer() else int(value)
    s = _safe_norm_str(value)
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = _safe_norm_str(value)
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _ytimg_hqdefault_url(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _download(url: str, *, out_path: Path, timeout_sec: int = 25) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        },
    )
    with urlopen(req, timeout=timeout_sec) as resp:  # nosec B310
        data = resp.read()
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(out_path)


def _load_pil_rgb(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        return img.convert("RGB")


def _write_png_atomic(path: Path, img_rgb: Image.Image) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    img_rgb.save(tmp, format="PNG", optimize=True)
    tmp.replace(path)


def _pick_contactsheet_font(size_px: int) -> ImageFont.ImageFont:
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


def _fmt_views_short(v: Optional[int]) -> str:
    if v is None:
        return "—"
    n = int(v)
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}億"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    if n >= 1_000:
        return f"{n / 1_000:.1f}千"
    return str(n)


def _build_contactsheet(
    *,
    features: List["VideoFeature"],
    out_path: Path,
    cols: int,
    tile_w: int,
    tile_h: int,
    pad: int,
) -> Optional[Path]:
    cols = max(1, int(cols))
    tile_w = max(120, int(tile_w))
    tile_h = max(90, int(tile_h))
    pad = max(0, int(pad))

    if not any(f.thumb_path and Path(f.thumb_path).exists() for f in features):
        return None

    rows = (len(features) + cols - 1) // cols
    W = cols * tile_w + (cols + 1) * pad
    H = rows * tile_h + (rows + 1) * pad
    canvas = Image.new("RGB", (W, H), (0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    font = _pick_contactsheet_font(max(16, int(round(tile_h * 0.08))))

    for i, f in enumerate(features):
        r = i // cols
        c = i % cols
        x = pad + c * (tile_w + pad)
        y = pad + r * (tile_h + pad)
        src = Path(f.thumb_path) if f.thumb_path else None
        if not src or not src.exists():
            tile = Image.new("RGB", (tile_w, tile_h), (30, 30, 30))
            canvas.paste(tile, (x, y))
            draw.text((x + 10, y + 10), f"MISSING {f.video_id}", fill=(255, 80, 80), font=font)
            continue
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im).convert("RGB").resize((tile_w, tile_h), Image.Resampling.LANCZOS)
            canvas.paste(im, (x, y))

        label = f"{f.video_id}  {_fmt_views_short(f.view_count)}"
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        lx = x + tile_w - 8 - tw
        ly = y + tile_h - 8 - th
        draw.rectangle((lx - 6, ly - 4, lx + tw + 6, ly + th + 4), fill=(0, 0, 0))
        draw.text((lx, ly), label, fill=(255, 255, 255), font=font)

    _write_png_atomic(out_path, canvas)
    return out_path


def _hex(rgb: Tuple[int, int, int]) -> str:
    r, g, b = rgb
    return f"#{r:02x}{g:02x}{b:02x}"


def _extract_palette_kmeans(img_rgb: Image.Image, *, k: int = 10) -> List[Dict[str, Any]]:
    k = max(2, min(24, int(k)))
    if cv2 is None:
        # Fallback: PIL adaptive palette (no OpenCV dependency).
        pal = img_rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=k)
        palette = pal.getpalette() or []
        counts = pal.getcolors(maxcolors=256) or []
        total = sum(int(c) for c, _idx in counts) or 1
        # idx -> count
        idx_counts = {int(idx): int(c) for c, idx in counts}
        order = sorted(idx_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        out: List[Dict[str, Any]] = []
        for idx, cnt in order[:k]:
            base = idx * 3
            if base + 2 >= len(palette):
                continue
            r, g, b = int(palette[base]), int(palette[base + 1]), int(palette[base + 2])
            out.append({"rgb": [r, g, b], "hex": _hex((r, g, b)), "ratio": float(cnt / total)})
        return out
    small = img_rgb.resize((256, 256), resample=Image.Resampling.BILINEAR)
    arr = np.array(small, dtype=np.uint8).reshape((-1, 3))
    Z = np.float32(arr)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 1.0)
    _compactness, labels, centers = cv2.kmeans(Z, k, None, criteria, 10, cv2.KMEANS_RANDOM_CENTERS)
    centers_u8 = np.clip(centers, 0, 255).astype(np.uint8)
    labels = labels.reshape((-1,))
    counts = np.bincount(labels, minlength=k)
    total = int(counts.sum()) or 1
    order = list(np.argsort(-counts))
    out: List[Dict[str, Any]] = []
    for idx in order:
        r, g, b = (int(x) for x in centers_u8[idx].tolist())
        out.append({"rgb": [r, g, b], "hex": _hex((r, g, b)), "ratio": float(counts[idx] / total)})
    return out


def _nms_boxes(boxes: List[List[int]], *, overlap: float = 0.6) -> List[List[int]]:
    out: List[List[int]] = []
    for b in sorted(boxes, key=lambda x: ((x[1] // 50) * 50, x[0], -(x[2] - x[0]) * (x[3] - x[1]))):
        keep = True
        x0, y0, x1, y1 = b
        area_b = max(1, (x1 - x0) * (y1 - y0))
        for p in out:
            xa = max(x0, p[0])
            ya = max(y0, p[1])
            xb = min(x1, p[2])
            yb = min(y1, p[3])
            inter = max(0, xb - xa) * max(0, yb - ya)
            area_p = max(1, (p[2] - p[0]) * (p[3] - p[1]))
            if inter / max(1, min(area_b, area_p)) >= overlap:
                keep = False
                break
        if keep:
            out.append(b)
    return out


def _find_text_like_regions(img_bgr: "np.ndarray") -> List[List[int]]:
    """
    サムネ向けの簡易テキスト領域候補検出（best-effort）。
    """
    if cv2 is None:
        return []
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]
    edges = cv2.Canny(gray, 80, 200)
    kx = max(25, int(round(w * 0.03)))
    ky = max(3, int(round(h * 0.004)))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kx, ky))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    dil = cv2.dilate(closed, cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)), iterations=1)
    contours, _ = cv2.findContours(dil, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes: List[List[int]] = []
    for c in contours:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < max(120, int(round(w * 0.08))) or bh < max(22, int(round(h * 0.02))):
            continue
        area = bw * bh
        if area > 0.6 * (w * h):
            continue
        ar = bw / max(bh, 1)
        if ar < 1.6:
            continue
        pad = max(8, int(round(min(bw, bh) * 0.05)))
        x0 = max(0, x - pad)
        y0 = max(0, y - pad)
        x1 = min(w, x + bw + pad)
        y1 = min(h, y + bh + pad)
        boxes.append([x0, y0, x1, y1])
    return _nms_boxes(boxes, overlap=0.6)


def _union_bbox_norm(boxes: List[List[int]], *, w: int, h: int) -> Optional[List[float]]:
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return [
        float(x0) / max(1, w),
        float(y0) / max(1, h),
        float(x1) / max(1, w),
        float(y1) / max(1, h),
    ]


def _mean_luma(img_rgb: Image.Image, *, box_norm: Tuple[float, float, float, float]) -> float:
    w, h = img_rgb.size
    x0 = int(round(box_norm[0] * w))
    y0 = int(round(box_norm[1] * h))
    x1 = int(round(box_norm[2] * w))
    y1 = int(round(box_norm[3] * h))
    crop = img_rgb.crop((max(0, x0), max(0, y0), min(w, x1), min(h, y1)))
    arr = np.array(crop.convert("L"), dtype=np.float32)
    return float(arr.mean()) if arr.size else 0.0


def _quantize_hex(hex_color: str, *, step: int = 16) -> str:
    s = str(hex_color or "").strip().lower()
    if not re.fullmatch(r"#[0-9a-f]{6}", s):
        return "#000000"
    step = max(1, min(64, int(step)))
    r = int(s[1:3], 16)
    g = int(s[3:5], 16)
    b = int(s[5:7], 16)
    rq = int(round(r / step) * step)
    gq = int(round(g / step) * step)
    bq = int(round(b / step) * step)
    rq = max(0, min(255, rq))
    gq = max(0, min(255, gq))
    bq = max(0, min(255, bq))
    return _hex((rq, gq, bq))


def _weighted_count_add(dst: Dict[str, float], key: str, w: float) -> None:
    k = str(key or "").strip()
    if not k:
        return
    dst[k] = float(dst.get(k, 0.0)) + float(w)


def _top_k_weighted(dst: Dict[str, float], k: int) -> List[Dict[str, Any]]:
    return [{"value": kk, "score": float(vv)} for kk, vv in sorted(dst.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]


def _collect_target_videos(report: Dict[str, Any], *, target: str, limit: int) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if target in {"top", "both"}:
        raw = report.get("top_by_views")
        if isinstance(raw, list):
            candidates.extend([x for x in raw if isinstance(x, dict)])
    if target in {"recent", "both"}:
        raw = report.get("recent")
        if isinstance(raw, list):
            candidates.extend([x for x in raw if isinstance(x, dict)])
    if target == "all":
        raw = report.get("videos")
        if isinstance(raw, list):
            candidates = [x for x in raw if isinstance(x, dict)]

    seen: set[str] = set()
    out: List[Dict[str, Any]] = []
    for item in candidates:
        vid = _safe_norm_str(item.get("id"))
        if not vid or vid in seen:
            continue
        seen.add(vid)
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


def _resolve_report_path(*, channel_id: str) -> Path:
    base = fpaths.research_root() / YT_DLP_GENRE_DIR / str(channel_id).strip()
    return base / "report.json"


def _styleguides_root(*, channel_id: str) -> Path:
    return fpaths.research_root() / "thumbnail_styleguides" / str(channel_id).strip()


def _phash_hex(img_rgb: Image.Image) -> str:
    if cv2 is not None:
        # pHash via DCT (OpenCV). 32x32 -> top-left 8x8.
        gray = np.array(img_rgb.convert("L").resize((32, 32), resample=Image.Resampling.BILINEAR), dtype=np.float32)
        dct = cv2.dct(gray)
        low = dct[:8, :8].copy()
        low[0, 0] = 0.0
        med = float(np.median(low))
        bits = (low > med).astype(np.uint8).reshape(-1).tolist()
        out = 0
        for b in bits:
            out = (out << 1) | int(b)
        return f"{out:016x}"

    # Fallback: dHash (no DCT/OpenCV).
    small = img_rgb.convert("L").resize((9, 8), resample=Image.Resampling.BILINEAR)
    arr = np.array(small, dtype=np.uint8)
    diff = arr[:, 1:] > arr[:, :-1]
    bits = diff.astype(np.uint8).reshape(-1).tolist()
    out = 0
    for b in bits:
        out = (out << 1) | int(b)
    return f"{out:016x}"


@dataclass(frozen=True)
class VideoFeature:
    video_id: str
    title: str
    view_count: Optional[int]
    thumbnail_url: str
    thumb_path: Optional[str]
    phash: Optional[str]
    palette: Optional[List[Dict[str, Any]]]
    text_union_bbox: Optional[List[float]]
    top_darkness: Optional[float]
    left_darkness: Optional[float]
    insight: Optional[Dict[str, Any]]


def _analyze_one_thumbnail(
    *,
    video_id: str,
    title: str,
    view_count: Optional[int],
    thumbnail_url: str,
    cache_dir: Path,
    download: bool,
    palette_k: int,
    max_text_boxes: int,
) -> Tuple[Optional[str], Optional[str], Optional[List[Dict[str, Any]]], Optional[List[float]], Optional[float], Optional[float]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    thumb_path = cache_dir / f"{video_id}.jpg"
    if download and not thumb_path.exists():
        _download(thumbnail_url, out_path=thumb_path)
    if not thumb_path.exists():
        return (None, None, None, None, None, None)

    img = _load_pil_rgb(thumb_path)
    phash = _phash_hex(img)

    palette = _extract_palette_kmeans(img, k=palette_k) if palette_k > 0 else None

    # text regions (best-effort)
    union: Optional[List[float]] = None
    if cv2 is not None:
        bgr = cv2.cvtColor(np.array(img, dtype=np.uint8), cv2.COLOR_RGB2BGR)
        boxes = _find_text_like_regions(bgr)
        if max_text_boxes and len(boxes) > max_text_boxes:
            boxes = boxes[: int(max_text_boxes)]
        union = _union_bbox_norm(boxes, w=img.width, h=img.height)

    # simple overlay signals
    top_dark = _mean_luma(img, box_norm=(0.0, 0.0, 1.0, 0.30))
    mid_luma = _mean_luma(img, box_norm=(0.0, 0.35, 1.0, 0.75))
    top_darkness = float(mid_luma - top_dark)
    left_luma = _mean_luma(img, box_norm=(0.0, 0.0, 0.35, 1.0))
    right_luma = _mean_luma(img, box_norm=(0.65, 0.0, 1.0, 1.0))
    left_darkness = float(right_luma - left_luma)

    return (str(thumb_path), phash, palette, union, top_darkness, left_darkness)


def build_styleguide(
    *,
    report: Dict[str, Any],
    report_path: Path,
    channel_id: str,
    target: str,
    limit: int,
    download_thumbs: bool,
    palette_k: int,
    max_text_boxes: int,
) -> Tuple[Dict[str, Any], List[VideoFeature]]:
    candidates = _collect_target_videos(report, target=target, limit=limit)
    insights = report.get("thumbnail_insights")
    insights_by_vid = insights if isinstance(insights, dict) else {}

    cache_dir = fpaths.workspace_root() / "tmp" / "thumbnail_styleguide_cache" / str(channel_id).strip()

    features: List[VideoFeature] = []
    for item in candidates:
        vid = _safe_norm_str(item.get("id"))
        if not vid:
            continue
        title = _safe_norm_str(item.get("title")) or ""
        view_count = _safe_int(item.get("view_count"))
        thumb_url = _ytimg_hqdefault_url(vid)
        try:
            thumb_path, phash, palette, union, top_darkness, left_darkness = _analyze_one_thumbnail(
                video_id=vid,
                title=title,
                view_count=view_count,
                thumbnail_url=thumb_url,
                cache_dir=cache_dir,
                download=download_thumbs,
                palette_k=palette_k,
                max_text_boxes=max_text_boxes,
            )
        except URLError:
            thumb_path, phash, palette, union, top_darkness, left_darkness = (None, None, None, None, None, None)
        except Exception:
            thumb_path, phash, palette, union, top_darkness, left_darkness = (None, None, None, None, None, None)

        insight_rec = insights_by_vid.get(vid) if isinstance(insights_by_vid, dict) else None
        analysis = insight_rec.get("analysis") if isinstance(insight_rec, dict) else None
        analysis_norm = analysis if isinstance(analysis, dict) else None

        features.append(
            VideoFeature(
                video_id=vid,
                title=title,
                view_count=view_count,
                thumbnail_url=thumb_url,
                thumb_path=thumb_path,
                phash=phash,
                palette=palette,
                text_union_bbox=union,
                top_darkness=top_darkness,
                left_darkness=left_darkness,
                insight=analysis_norm,
            )
        )

    # weighted aggregation (by log(view_count))
    hook_scores: Dict[str, float] = {}
    elem_scores: Dict[str, float] = {}
    tag_scores: Dict[str, float] = {}
    color_scores: Dict[str, float] = {}

    unions: List[List[float]] = []
    top_dark_scores: List[float] = []
    left_dark_scores: List[float] = []

    for f in features:
        w = 1.0
        if f.view_count is not None and f.view_count > 0:
            w = max(1.0, math.log10(float(f.view_count) + 10.0))

        if f.insight:
            ht = _safe_norm_str(f.insight.get("hook_type")) or ""
            _weighted_count_add(hook_scores, ht, w)
            for x in (f.insight.get("design_elements") or []):
                _weighted_count_add(elem_scores, _safe_norm_str(x) or "", w)
            for x in (f.insight.get("tags") or []):
                _weighted_count_add(tag_scores, _safe_norm_str(x) or "", w)

        if f.palette:
            for c in f.palette[: min(10, len(f.palette))]:
                if not isinstance(c, dict):
                    continue
                hx = _safe_norm_str(c.get("hex")) or ""
                ratio = _safe_float(c.get("ratio")) or 0.0
                q = _quantize_hex(hx, step=16)
                _weighted_count_add(color_scores, q, w * float(max(0.0, min(1.0, ratio))))

        if f.text_union_bbox:
            unions.append(f.text_union_bbox)
        if f.top_darkness is not None:
            top_dark_scores.append(float(f.top_darkness))
        if f.left_darkness is not None:
            left_dark_scores.append(float(f.left_darkness))

    def _median(xs: List[float]) -> Optional[float]:
        if not xs:
            return None
        ys = sorted(xs)
        mid = len(ys) // 2
        return float(ys[mid]) if len(ys) % 2 == 1 else float((ys[mid - 1] + ys[mid]) / 2.0)

    union_median: Optional[List[float]] = None
    if unions:
        union_median = [
            float(_median([u[i] for u in unions]) or 0.0) for i in range(4)
        ]

    top_dark_med = _median(top_dark_scores) or 0.0
    left_dark_med = _median(left_dark_scores) or 0.0

    # layout suggestion (best-effort):
    # - left_tsz if text union is mostly left OR left side is darker than right
    # - top_band if union is mostly top OR top is darker than mid
    layout_hint = "unknown"
    safe_left_x1 = 0.55
    top_band_y1 = 0.30
    if union_median:
        _x0, _y0, x1, y1 = union_median
        if x1 <= 0.68:
            layout_hint = "left_tsz"
            safe_left_x1 = max(0.45, min(0.75, float(x1 + 0.06)))
        elif y1 <= 0.40:
            layout_hint = "top_band"
            top_band_y1 = max(0.18, min(0.40, float(y1 + 0.05)))
        else:
            layout_hint = "center"
    else:
        if left_dark_med >= 12.0:
            layout_hint = "left_tsz"
        elif top_dark_med >= 14.0:
            layout_hint = "top_band"
        else:
            layout_hint = "center"

    # keep sample small
    samples: List[Dict[str, Any]] = []
    for f in sorted(features, key=lambda x: (-(x.view_count or 0), x.video_id))[: min(12, len(features))]:
        samples.append(
            {
                "video_id": f.video_id,
                "title": f.title,
                "view_count": f.view_count,
                "thumbnail_url": f.thumbnail_url,
                "phash": f.phash,
                "text_union_bbox": f.text_union_bbox,
                "palette_top": (f.palette or [])[:5],
                "hook_type": (_safe_norm_str((f.insight or {}).get("hook_type")) if f.insight else None),
                "design_elements": (f.insight or {}).get("design_elements") if f.insight else None,
            }
        )

    ch = report.get("channel") if isinstance(report.get("channel"), dict) else {}

    styleguide: Dict[str, Any] = {
        "schema": SCHEMA_STYLEGUIDE_V1,
        "generated_at": _utc_now_iso(),
        "source": {
            "report_json": str(report_path.resolve()),
            "yt_dlp_genre_dir": YT_DLP_GENRE_DIR,
            "target": target,
            "limit": int(limit),
            "download_thumbs": bool(download_thumbs),
        },
        "channel": {
            "playlist_channel_id": str(channel_id),
            "playlist_channel": _safe_norm_str(ch.get("playlist_channel")),
            "playlist_uploader_id": _safe_norm_str(ch.get("playlist_uploader_id")),
            "source_url": _safe_norm_str(ch.get("source_url")),
            "avatar_url": _safe_norm_str(ch.get("avatar_url")),
        },
        "stats": {
            "video_features_count": len(features),
            "has_thumbnail_insights": bool(isinstance(insights, dict) and len(insights) > 0),
        },
        "signals": {
            "layout_hint": layout_hint,
            "union_bbox_median": union_median,
            "top_darkness_median": float(top_dark_med),
            "left_darkness_median": float(left_dark_med),
        },
        "recommendations": {
            "safe_left_tsz": {"x0": 0.0, "x1": round(float(safe_left_x1), 4)} if layout_hint == "left_tsz" else None,
            "top_band": {"y0": 0.0, "y1": round(float(top_band_y1), 4)} if layout_hint == "top_band" else None,
        },
        "top": {
            "hook_types": _top_k_weighted(hook_scores, 12),
            "design_elements": _top_k_weighted(elem_scores, 30),
            "tags": _top_k_weighted(tag_scores, 40),
            "palette_quantized": _top_k_weighted(color_scores, 18),
        },
        "samples": samples,
        "notes": {
            "copyright_safety": (
                "この出力は“特徴の抽象化”のための材料。特定サムネの完全再現（配置/色/素材/文字の一致）は避け、"
                "テンプレは必ずレンジ（可変）で運用すること。"
            ),
            "deps": (
                "opencv-python-headless が入っていない場合はテキストbbox検出をスキップし、"
                "画像ハッシュはdHashにフォールバックします。"
            ),
        },
    }
    return styleguide, features


def _render_styleguide_md(doc: Dict[str, Any]) -> str:
    ch = doc.get("channel") if isinstance(doc.get("channel"), dict) else {}
    signals = doc.get("signals") if isinstance(doc.get("signals"), dict) else {}
    rec = doc.get("recommendations") if isinstance(doc.get("recommendations"), dict) else {}
    top = doc.get("top") if isinstance(doc.get("top"), dict) else {}
    samples = doc.get("samples") if isinstance(doc.get("samples"), list) else []

    def fmt_list(items: Any, *, k: int) -> str:
        if not isinstance(items, list):
            return "（なし）"
        lines = []
        for it in items[:k]:
            if not isinstance(it, dict):
                continue
            val = str(it.get("value") or "").strip()
            score = it.get("score")
            if not val:
                continue
            if isinstance(score, (int, float)):
                lines.append(f"- {val} ({float(score):.2f})")
            else:
                lines.append(f"- {val}")
        return "\n".join(lines) if lines else "（なし）"

    header = ch.get("playlist_channel") or ch.get("playlist_uploader_id") or ch.get("playlist_channel_id") or "—"
    lines: List[str] = []
    lines.append(f"# Thumbnail Styleguide（自動集約）— {header}")
    lines.append("")
    lines.append(f"- generated_at: {doc.get('generated_at')}")
    lines.append(f"- channel_id: {ch.get('playlist_channel_id')}")
    lines.append(f"- source_url: {ch.get('source_url')}")
    lines.append("")
    lines.append("## Signals")
    lines.append(f"- layout_hint: {signals.get('layout_hint')}")
    lines.append(f"- union_bbox_median: {signals.get('union_bbox_median')}")
    lines.append(f"- top_darkness_median: {signals.get('top_darkness_median')}")
    lines.append(f"- left_darkness_median: {signals.get('left_darkness_median')}")
    lines.append("")
    lines.append("## Recommendations")
    lines.append(f"- safe_left_tsz: {rec.get('safe_left_tsz')}")
    lines.append(f"- top_band: {rec.get('top_band')}")
    lines.append("")
    lines.append("## Top hook_types")
    lines.append(fmt_list(top.get("hook_types"), k=12))
    lines.append("")
    lines.append("## Top design_elements")
    lines.append(fmt_list(top.get("design_elements"), k=20))
    lines.append("")
    lines.append("## Top palette (quantized)")
    lines.append(fmt_list(top.get("palette_quantized"), k=12))
    lines.append("")
    if samples:
        lines.append("## Samples (top views)")
        for s in samples[:8]:
            if not isinstance(s, dict):
                continue
            lines.append(f"- {s.get('video_id')}  views={s.get('view_count')}  {s.get('title')}")
            lines.append(f"  - url: {s.get('thumbnail_url')}")
            tu = s.get("text_union_bbox")
            if tu is not None:
                lines.append(f"  - text_union_bbox: {tu}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _normalize_channel_code(channel: str) -> str:
    ch = str(channel or "").strip().upper()
    if not re.fullmatch(r"CH\d{2}", ch):
        raise ValueError("channel_code must be like CH01")
    return ch


def _spec_id(channel_code: str, *, kind: str, suffix: str) -> str:
    ch = channel_code.lower()
    return f"{ch}_{kind}_{suffix}"


def _yaml_path_for_spec_id(spec_id: str) -> Path:
    return fpaths.thumbnails_root() / "compiler" / "layer_specs" / f"{spec_id}.yaml"


def _ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    _ensure_dir(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tmp.replace(path)


def _build_text_layout_payload(
    *,
    channel_code: str,
    styleguide: Dict[str, Any],
    suffix: str,
) -> Dict[str, Any]:
    rec = styleguide.get("recommendations") if isinstance(styleguide.get("recommendations"), dict) else {}
    safe_left = rec.get("safe_left_tsz") if isinstance(rec.get("safe_left_tsz"), dict) else None
    top_band = rec.get("top_band") if isinstance(rec.get("top_band"), dict) else None

    # Defaults that are broadly usable (and easy to tweak later).
    safe_left_x1 = float(safe_left.get("x1")) if safe_left and isinstance(safe_left.get("x1"), (int, float)) else 0.55
    safe_left_x1 = max(0.45, min(0.78, safe_left_x1))

    top_band_y1 = float(top_band.get("y1")) if top_band and isinstance(top_band.get("y1"), (int, float)) else 0.30
    top_band_y1 = max(0.18, min(0.45, top_band_y1))

    x_left = 0.05
    w_left = max(0.20, safe_left_x1 - x_left - 0.02)

    overlays: Dict[str, Any] = {}
    # Prefer left_tsz if available; otherwise allow top_band if hinted.
    if safe_left:
        overlays["left_tsz"] = {
            "enabled": True,
            "color": "#000000",
            "x0": 0.0,
            "x1": round(float(safe_left_x1 + 0.02), 4),
            "alpha_left": 0.62,
            "alpha_right": 0.00,
        }
    if (not safe_left) and top_band:
        overlays["top_band"] = {
            "enabled": True,
            "color": "#000000",
            "y0": 0.0,
            "y1": round(float(top_band_y1), 4),
            "alpha_top": 0.92,
            "alpha_bottom": 0.92,
        }

    global_cfg: Dict[str, Any] = {
        "fonts": {
            "headline_sans_priority": [
                "Noto Sans JP Black",
                "Source Han Sans JP Heavy",
                "Hiragino Sans W8",
                "Yu Gothic UI Bold",
            ],
            "headline_serif_priority": [
                "Noto Serif JP Black",
                "Source Han Serif JP Heavy",
                "Hiragino Mincho ProN W6",
                "Shippori Mincho B1 Bold",
            ],
            "latin_priority": ["Cinzel Bold", "Trajan Pro", "Times New Roman Bold"],
        },
        "effects_defaults": {
            "stroke": {"color": "#000000", "width_px": 18, "join": "round"},
            "shadow": {"color": "#000000", "alpha": 0.85, "offset_px": [12, 12], "blur_px": 16},
            "glow": {"color": "#ffffff", "alpha": 0.12, "blur_px": 22},
            "white_fill": {"mode": "solid", "color": "#FFFFFF"},
            "yellow_fill": {"mode": "solid", "color": "#FFD84A"},
            "red_fill": {"mode": "solid", "color": "#D61A1A"},
            "gold_fill": {
                "mode": "linear_gradient",
                "stops": [["#FFF3B0", 0.0], ["#F2C14E", 0.55], ["#D99A2B", 1.0]],
            },
        },
        "fit_rules": [
            "溢れたら自動縮小（fit=contain）。それでも溢れる場合は短いコピーへ。",
            "完全再現は禁止: 配置/配色/素材はレンジで運用し、毎回微差を入れる。",
        ],
    }
    if safe_left:
        global_cfg["safe_zones"] = {"left_TSZ": {"x0": 0.0, "x1": round(float(safe_left_x1), 4)}}
    if overlays:
        global_cfg["overlays"] = overlays

    templates: Dict[str, Any] = {
        # default: alphabetical first
        "A01_default_left_stack": {
            "description": f"{channel_code}: 左スタック（上/主/下）v1",
            "slots": {
                "upper": {
                    "box": [round(x_left, 4), 0.05, round(w_left, 4), 0.14],
                    "font": "headline_sans_priority",
                    "fill": "white_fill",
                    "base_size_px": 78,
                    "align": "left",
                    "tracking": 0,
                    "max_lines": 1,
                    "stroke": True,
                    "shadow": True,
                    "glow": False,
                },
                "title": {
                    "box": [round(x_left, 4), 0.20, round(w_left, 4), 0.46],
                    "font": "headline_serif_priority",
                    "fill": "white_fill",
                    "base_size_px": 190,
                    "align": "left",
                    "tracking": 0,
                    "max_lines": 3,
                    "stroke": True,
                    "shadow": True,
                    "glow": False,
                },
                "lower": {
                    "box": [round(x_left, 4), 0.70, round(w_left, 4), 0.18],
                    "font": "headline_sans_priority",
                    "fill": "yellow_fill",
                    "base_size_px": 120,
                    "align": "left",
                    "tracking": 0,
                    "max_lines": 1,
                    "stroke": True,
                    "shadow": True,
                    "glow": False,
                },
            },
            "fallbacks": ["A02_top_band_center", "A03_center_big"],
        },
        "A02_top_band_center": {
            "description": f"{channel_code}: 上帯センター1行 v1",
            "slots": {
                "title": {
                    "box": [0.02, 0.02, 0.96, round(float(top_band_y1 - 0.03), 4)],
                    "font": "headline_sans_priority",
                    "fill": "white_fill",
                    "base_size_px": 260,
                    "align": "center",
                    "tracking": 0,
                    "max_lines": 1,
                    "stroke": False,
                    "shadow": True,
                    "glow": False,
                }
            },
            "fallbacks": ["A01_default_left_stack", "A03_center_big"],
        },
        "A03_center_big": {
            "description": f"{channel_code}: センター大見出し v1",
            "slots": {
                "title": {
                    "box": [0.08, 0.30, 0.84, 0.40],
                    "font": "headline_sans_priority",
                    "fill": "gold_fill",
                    "base_size_px": 210,
                    "align": "center",
                    "tracking": 0,
                    "max_lines": 2,
                    "stroke": True,
                    "shadow": True,
                    "glow": False,
                }
            },
            "fallbacks": ["A01_default_left_stack", "A02_top_band_center"],
        },
    }

    return {
        "version": 3,
        "name": f"{channel_code}_text_layout_{suffix}",
        "canvas": {"w": 1920, "h": 1080, "aspect": "16:9"},
        "coordinate_system": "normalized_0_to_1",
        "global": global_cfg,
        "templates": templates,
        "items": [],
    }


def _build_image_prompts_payload(*, channel_code: str, styleguide: Dict[str, Any], suffix: str) -> Dict[str, Any]:
    rec = styleguide.get("recommendations") if isinstance(styleguide.get("recommendations"), dict) else {}
    safe_left = rec.get("safe_left_tsz") if isinstance(rec.get("safe_left_tsz"), dict) else None
    safe_left_x1 = float(safe_left.get("x1")) if safe_left and isinstance(safe_left.get("x1"), (int, float)) else 0.55
    safe_left_x1 = max(0.45, min(0.78, safe_left_x1))
    policy: Dict[str, Any] = {
        "forbid_text": True,
        "brightness": "bright_finish_no_crushed_blacks",
    }
    if safe_left:
        policy["left_TSZ"] = {"x0": 0.0, "x1": round(float(safe_left_x1), 4), "rule": "文字エリアは暗く滑らか（物体/強発光/粒子/模様禁止）"}
        policy["person_bbox_target"] = {"x0_min": 0.60, "x1_max": 0.98, "y0_min": 0.05, "y1_max": 0.72}
    return {
        "version": 3,
        "name": f"{channel_code}_image_prompts_{suffix}",
        "canvas": {"w": 1920, "h": 1080, "aspect": "16:9"},
        "policy": policy,
        "items": [],
    }


def _build_bg_template_prompt(*, channel_code: str, styleguide: Dict[str, Any]) -> str:
    rec = styleguide.get("recommendations") if isinstance(styleguide.get("recommendations"), dict) else {}
    safe_left = rec.get("safe_left_tsz") if isinstance(rec.get("safe_left_tsz"), dict) else None
    top_band = rec.get("top_band") if isinstance(rec.get("top_band"), dict) else None

    lines: List[str] = []
    lines.append("YouTube thumbnail background image, 16:9 (1920x1080).")
    lines.append("")
    lines.append("STYLE (GUIDE, NOT COPYING):")
    lines.append("- High clarity, strong contrast, cinematic lighting.")
    lines.append("- Distinct from any specific existing thumbnail; avoid brand-like signatures.")
    lines.append("")
    lines.append("COMPOSITION (GUIDE):")
    if safe_left and isinstance(safe_left.get("x1"), (int, float)):
        x1 = float(safe_left["x1"])
        lines.append(f"- Keep LEFT {int(round(x1 * 100))}% as typography-safe area: dark smooth gradient only, low detail.")
        lines.append("- Place main subject on the RIGHT side with strong rim light; keep subject away from the left safe area.")
    elif top_band and isinstance(top_band.get("y1"), (int, float)):
        y1 = float(top_band["y1"])
        lines.append(f"- Keep TOP {int(round(y1 * 100))}% as typography-safe area: simple dark band/space, low detail.")
        lines.append("- Put main subject lower area with clear separation from the top band.")
    else:
        lines.append("- Leave generous negative space for typography; keep one side cleaner than the subject side.")
    lines.append("")
    lines.append("SUBJECT / SCENE (OVERRIDE):")
    lines.append("{{thumbnail_prompt}}")
    lines.append("")
    lines.append("ABSOLUTE RESTRICTIONS:")
    lines.append("NO text, NO letters, NO numbers, NO watermark, NO logo, NO signature, NO UI.")
    lines.append("")
    return "\n".join(lines)


def _upsert_channel_template(
    *,
    templates_doc: Dict[str, Any],
    channel_code: str,
    bg_template_id: str,
    image_model_key: str,
    prompt_template: str,
) -> None:
    channels = templates_doc.get("channels")
    if not isinstance(channels, dict):
        channels = {}
        templates_doc["channels"] = channels
    ch_doc = channels.get(channel_code)
    if not isinstance(ch_doc, dict):
        ch_doc = {}
        channels[channel_code] = ch_doc

    # ensure structural fields exist (do not delete user fields)
    layer = ch_doc.get("layer_specs")
    if not isinstance(layer, dict):
        layer = {}
        ch_doc["layer_specs"] = layer

    tpl_list = ch_doc.get("templates")
    if not isinstance(tpl_list, list):
        tpl_list = []
        ch_doc["templates"] = tpl_list

    # upsert template
    existing: Optional[Dict[str, Any]] = None
    for it in tpl_list:
        if isinstance(it, dict) and str(it.get("id") or "").strip() == bg_template_id:
            existing = it
            break
    if existing is None:
        existing = {"id": bg_template_id, "created_at": _utc_now_iso()}
        tpl_list.insert(0, existing)

    existing["name"] = existing.get("name") or f"{channel_code}: bg_template {bg_template_id}"
    existing["image_model_key"] = str(image_model_key).strip()
    existing["prompt_template"] = prompt_template
    existing["negative_prompt"] = existing.get("negative_prompt") or "text, letters, numbers, watermark, logo, signature, UI"
    existing["notes"] = existing.get("notes") or "styleguide scaffold (do not 1:1 copy any existing thumbnail)"
    existing["updated_at"] = _utc_now_iso()


def _upsert_layer_specs_registry(
    *,
    templates_doc: Dict[str, Any],
    spec_id: str,
    kind: str,
    path: str,
    version: int,
) -> None:
    layer_specs = templates_doc.get("layer_specs")
    if not isinstance(layer_specs, dict):
        layer_specs = {"defaults": {"image_prompts_id": None, "text_layout_id": None}, "registry": {}}
        templates_doc["layer_specs"] = layer_specs
    registry = layer_specs.get("registry")
    if not isinstance(registry, dict):
        registry = {}
        layer_specs["registry"] = registry
    registry[spec_id] = {"kind": kind, "version": int(version), "path": path, "name": spec_id}


def _apply_scaffold(
    *,
    channel_code: str,
    styleguide: Dict[str, Any],
    suffix: str,
    apply: bool,
    force: bool,
    update_templates_json: bool,
    set_default_template: bool,
    image_model_key: str,
) -> int:
    ch = _normalize_channel_code(channel_code)
    image_spec_id = _spec_id(ch, kind="image_prompts", suffix=suffix)
    text_spec_id = _spec_id(ch, kind="text_layout", suffix=suffix)
    bg_template_id = f"{ch.lower()}_bg_{suffix}"

    image_yaml_path = _yaml_path_for_spec_id(image_spec_id)
    text_yaml_path = _yaml_path_for_spec_id(text_spec_id)

    planned = [
        ("layer_specs:image_prompts", str(image_yaml_path)),
        ("layer_specs:text_layout", str(text_yaml_path)),
    ]
    if update_templates_json:
        planned.append(("templates_json", str(fpaths.thumbnails_root() / "templates.json")))

    if not apply:
        print("[dry-run] scaffold outputs:")
        for k, p in planned:
            print(f"- {k}: {p}")
        print("\nUse --apply to write.")
        return 0

    if not force:
        for p in (image_yaml_path, text_yaml_path):
            if p.exists():
                raise SystemExit(f"already exists: {p} (use --force to overwrite)")

    _write_yaml(image_yaml_path, _build_image_prompts_payload(channel_code=ch, styleguide=styleguide, suffix=suffix))
    _write_yaml(text_yaml_path, _build_text_layout_payload(channel_code=ch, styleguide=styleguide, suffix=suffix))

    if update_templates_json:
        templates_path = fpaths.thumbnails_root() / "templates.json"
        templates_doc = json.loads(templates_path.read_text(encoding="utf-8"))
        _upsert_layer_specs_registry(
            templates_doc=templates_doc,
            spec_id=image_spec_id,
            kind="image_prompts",
            version=3,
            path=f"workspaces/thumbnails/compiler/layer_specs/{image_spec_id}.yaml",
        )
        _upsert_layer_specs_registry(
            templates_doc=templates_doc,
            spec_id=text_spec_id,
            kind="text_layout",
            version=3,
            path=f"workspaces/thumbnails/compiler/layer_specs/{text_spec_id}.yaml",
        )

        channels = templates_doc.get("channels")
        if not isinstance(channels, dict):
            channels = {}
            templates_doc["channels"] = channels
        ch_doc = channels.get(ch)
        if not isinstance(ch_doc, dict):
            ch_doc = {}
            channels[ch] = ch_doc
        layer = ch_doc.get("layer_specs")
        if not isinstance(layer, dict):
            layer = {}
            ch_doc["layer_specs"] = layer
        layer["image_prompts_id"] = image_spec_id
        layer["text_layout_id"] = text_spec_id

        prompt_template = _build_bg_template_prompt(channel_code=ch, styleguide=styleguide)
        _upsert_channel_template(
            templates_doc=templates_doc,
            channel_code=ch,
            bg_template_id=bg_template_id,
            image_model_key=image_model_key,
            prompt_template=prompt_template,
        )
        if set_default_template:
            ch_doc["default_template_id"] = bg_template_id

        _write_json(templates_path, templates_doc)

    print("wrote:")
    print(f"- {text_yaml_path}")
    print(f"- {image_yaml_path}")
    if update_templates_json:
        print(f"- {fpaths.thumbnails_root() / 'templates.json'}")
    return 0


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Build thumbnail styleguides and scaffold thumbnail templates.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_build = sub.add_parser("build", help="Build styleguide from yt-dlp report.json (+ optional thumbnail_insights).")
    sp_build.add_argument("--report-json", type=str, default=None, help="Path to report.json (preferred if known).")
    sp_build.add_argument("--channel-id", type=str, default=None, help="playlist_channel_id (UC...).")
    sp_build.add_argument("--handle", type=str, default=None, help="YouTube handle like @name (resolves to UC...).")
    sp_build.add_argument("--target", type=str, default="both", choices=["top", "recent", "both", "all"])
    sp_build.add_argument("--limit", type=int, default=20, help="max videos to sample (default: 20)")
    sp_build.add_argument("--no-download-thumbs", action="store_true", help="do not download thumbnails (LLM-only)")
    sp_build.add_argument("--palette-k", type=int, default=10, help="palette k-means size (default: 10)")
    sp_build.add_argument("--max-text-boxes", type=int, default=18, help="cap per-thumb text boxes (default: 18)")
    sp_build.add_argument("--contactsheet", action="store_true", help="write contactsheet.png under styleguide dir")
    sp_build.add_argument("--contactsheet-cols", type=int, default=5, help="contactsheet grid cols (default: 5)")
    sp_build.add_argument("--contactsheet-tile-w", type=int, default=480, help="contactsheet tile width (default: 480)")
    sp_build.add_argument("--contactsheet-tile-h", type=int, default=360, help="contactsheet tile height (default: 360)")
    sp_build.add_argument("--contactsheet-pad", type=int, default=12, help="contactsheet padding (default: 12)")
    sp_build.add_argument("--apply", action="store_true", help="write styleguide.json/md")

    sp_sc = sub.add_parser("scaffold", help="Scaffold layer_specs and optionally register in templates.json.")
    sp_sc.add_argument("--styleguide", type=str, default=None, help="Path to styleguide.json (preferred).")
    sp_sc.add_argument("--channel-id", type=str, default=None, help="If styleguide not provided, load from channel_id.")
    sp_sc.add_argument("--handle", type=str, default=None, help="If styleguide not provided, resolve from handle.")
    sp_sc.add_argument("--channel-code", type=str, required=True, help="Target internal channel code (CHxx).")
    sp_sc.add_argument("--suffix", type=str, default="styleguide_v1", help="spec/template suffix (default: styleguide_v1)")
    sp_sc.add_argument("--image-model-key", type=str, default="img-gemini-flash-1", help="default image_model_key")
    sp_sc.add_argument("--no-update-templates-json", action="store_true", help="do not touch templates.json")
    sp_sc.add_argument("--set-default-template", action="store_true", help="set channel default_template_id to the new bg template")
    sp_sc.add_argument("--force", action="store_true", help="overwrite existing spec files")
    sp_sc.add_argument("--apply", action="store_true", help="write outputs (default is dry-run)")

    return ap.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)

    if args.cmd == "build":
        channel_id = _safe_norm_str(args.channel_id)
        report_path: Optional[Path] = Path(args.report_json).expanduser() if args.report_json else None
        if report_path is None:
            handle = _safe_norm_str(args.handle)
            if handle and not channel_id:
                try:
                    res = resolve_youtube_channel_id_from_handle(handle)
                    channel_id = res.channel_id
                except YouTubeHandleResolutionError as exc:
                    raise SystemExit(f"failed to resolve handle: {handle} ({exc})") from exc
            if not channel_id:
                raise SystemExit("need --report-json or (--channel-id / --handle)")
            report_path = _resolve_report_path(channel_id=channel_id)
        if not report_path.exists():
            raise SystemExit(f"report.json not found: {report_path}")
        report = _read_json(report_path)
        ch = report.get("channel") if isinstance(report.get("channel"), dict) else {}
        ch_id = str(ch.get("playlist_channel_id") or channel_id or "").strip()
        if not ch_id:
            raise SystemExit("playlist_channel_id is missing in report")

        doc, features = build_styleguide(
            report=report,
            report_path=report_path,
            channel_id=ch_id,
            target=str(args.target),
            limit=int(args.limit),
            download_thumbs=not bool(args.no_download_thumbs),
            palette_k=int(args.palette_k),
            max_text_boxes=int(args.max_text_boxes),
        )
        out_root = _styleguides_root(channel_id=ch_id)
        out_json = out_root / "styleguide.json"
        out_md = out_root / "styleguide.md"
        out_png = out_root / "contactsheet.png"
        if not args.apply:
            print("[dry-run] would write:")
            print(f"- {out_json}")
            print(f"- {out_md}")
            if bool(args.contactsheet):
                print(f"- {out_png}")
            print("\nUse --apply to write.")
            return 0
        _write_json(out_json, doc)
        _write_text(out_md, _render_styleguide_md(doc))
        print("wrote:")
        print(f"- {out_json}")
        print(f"- {out_md}")
        if bool(args.contactsheet):
            wrote = _build_contactsheet(
                features=features,
                out_path=out_png,
                cols=int(args.contactsheet_cols),
                tile_w=int(args.contactsheet_tile_w),
                tile_h=int(args.contactsheet_tile_h),
                pad=int(args.contactsheet_pad),
            )
            if wrote:
                print(f"- {wrote}")
            else:
                print("[warn] contactsheet: no local thumbnails available (try without --no-download-thumbs)")
        return 0

    if args.cmd == "scaffold":
        styleguide_path: Optional[Path] = Path(args.styleguide).expanduser() if args.styleguide else None
        channel_id = _safe_norm_str(args.channel_id)
        if styleguide_path is None:
            handle = _safe_norm_str(args.handle)
            if handle and not channel_id:
                try:
                    res = resolve_youtube_channel_id_from_handle(handle)
                    channel_id = res.channel_id
                except YouTubeHandleResolutionError as exc:
                    raise SystemExit(f"failed to resolve handle: {handle} ({exc})") from exc
            if not channel_id:
                raise SystemExit("need --styleguide or (--channel-id / --handle)")
            styleguide_path = _styleguides_root(channel_id=channel_id) / "styleguide.json"
        if not styleguide_path.exists():
            raise SystemExit(f"styleguide.json not found: {styleguide_path} (run build --apply first)")
        styleguide = _read_json(styleguide_path)
        return _apply_scaffold(
            channel_code=str(args.channel_code),
            styleguide=styleguide,
            suffix=str(args.suffix),
            apply=bool(args.apply),
            force=bool(args.force),
            update_templates_json=not bool(args.no_update_templates_json),
            set_default_template=bool(args.set_default_template),
            image_model_key=str(args.image_model_key),
        )

    raise SystemExit(f"unknown cmd: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
