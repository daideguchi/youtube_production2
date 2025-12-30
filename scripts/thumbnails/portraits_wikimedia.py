#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CH26 portrait helper (Wikimedia / Wikipedia).

This script downloads a real person's portrait image (no AI face generation),
then generates `20_portrait.png` (RGBA with a soft alpha mask) per video folder.

Output (per video):
  - 20_portrait_src.<ext>         (downloaded original)
  - 20_portrait.png               (prepared cutout)
  - 20_portrait.source.json       (source + license metadata)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

import requests
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps

def _discover_repo_root(start: Path) -> Path:
    cur = start if start.is_dir() else start.parent
    for candidate in (cur, *cur.parents):
        if (candidate / "pyproject.toml").exists():
            return candidate.resolve()
    raise RuntimeError("repo root not found (pyproject.toml). Run from inside the repo.")


try:
    from _bootstrap import bootstrap
except ModuleNotFoundError:
    repo_root = _discover_repo_root(Path(__file__).resolve())
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from _bootstrap import bootstrap

bootstrap()

from factory_common import paths as fpaths  # noqa: E402
from script_pipeline.tools import planning_store  # noqa: E402


USER_AGENT = "factory_commentary_thumb_bot/0.1 (local use; contact: none)"

# planning uses "思想枠" labels; map to a representative inventor/person.
NAME_OVERRIDES = {
    "トヨタ生産方式": "大野耐一",
    "スティーブン・コヴィー": "スティーブン・R・コヴィー",
}

# Some Wikipedia lead images are low-quality (e.g., GIF). Override to a better Commons file when known.
# NOTE: Use a Commons `File:<name>` title (case-sensitive as on Commons).
IMAGE_FILE_OVERRIDES: Dict[str, str] = {
    "ジョン・フォン・ノイマン": "File:Vonneumann-john r.jpg",
}

BAD_EXTS = {".gif", ".svg", ".tif", ".tiff", ".pdf", ".djvu"}
GOOD_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

BAD_FILENAME_KEYWORDS = {
    "logo",
    "icon",
    "symbol",
    "signature",
    "sig",
    "autograph",
    "tomb",
    "grave",
    "crater",
    "map",
    "diagram",
    "chart",
    "flow",
    "venn",
    "demo",
    "bomb",
    "animated",
    "flag",
    "coatofarms",
    "coat_of_arms",
    "commons-logo",
    "wikiquote",
    "people icon",
}

GOOD_FILENAME_KEYWORDS = {"portrait", "photo", "photograph", "mfo", "cropped"}


@dataclass(frozen=True)
class PortraitSource:
    person_raw: str
    person_title: str
    wikipedia_lang: str
    wikipedia_page_url: Optional[str]
    image_url: str
    image_filename: str
    downloaded_at: str
    license_short: Optional[str] = None
    license_url: Optional[str] = None
    artist: Optional[str] = None
    credit: Optional[str] = None
    attribution_required: Optional[str] = None
    description_url: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_channel(channel: str) -> str:
    return str(channel or "").strip().upper()


def _normalize_video(video: str) -> str:
    raw = str(video or "").strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        raise ValueError(f"invalid video: {video}")
    return digits.zfill(3)


def _strip_parens(name: str) -> str:
    s = str(name or "").strip()
    return re.sub(r"（.*?）", "", s).strip()


def _resolve_person_title(person_raw: str) -> str:
    base = _strip_parens(person_raw)
    return NAME_OVERRIDES.get(base, base)


def _wikipedia_api(lang: str) -> str:
    return f"https://{lang}.wikipedia.org/w/api.php"


def _commons_api() -> str:
    return "https://commons.wikimedia.org/w/api.php"


def _http_get_json(url: str, *, params: Dict[str, Any]) -> Dict[str, Any]:
    resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError("invalid json payload")
    return data


def fetch_wikipedia_page_image(*, title: str, lang: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns: (page_url, original_image_url)
    """
    data = _http_get_json(
        _wikipedia_api(lang),
        params={
            "action": "query",
            "titles": title,
            "prop": "pageimages",
            "piprop": "original",
            "redirects": 1,
            "format": "json",
        },
    )
    pages = (data.get("query") or {}).get("pages") or {}
    if not isinstance(pages, dict) or not pages:
        return None, None
    page = next(iter(pages.values()))
    if not isinstance(page, dict):
        return None, None
    pageid = page.get("pageid")
    page_url = f"https://{lang}.wikipedia.org/?curid={pageid}" if pageid else None
    original = page.get("original") or {}
    if not isinstance(original, dict):
        return page_url, None
    src = original.get("source")
    return page_url, str(src).strip() if isinstance(src, str) and src.strip() else None


def fetch_wikipedia_langlink_title(*, title: str, from_lang: str, to_lang: str) -> Optional[str]:
    """
    Resolve a Wikipedia page title to another language title via langlinks.
    Returns the target language title string, or None.
    """
    data = _http_get_json(
        _wikipedia_api(from_lang),
        params={
            "action": "query",
            "titles": title,
            "prop": "langlinks",
            "lllang": to_lang,
            "lllimit": 1,
            "redirects": 1,
            "format": "json",
        },
    )
    pages = (data.get("query") or {}).get("pages") or {}
    if not isinstance(pages, dict) or not pages:
        return None
    page = next(iter(pages.values()))
    if not isinstance(page, dict):
        return None
    lls = page.get("langlinks") or []
    if not isinstance(lls, list) or not lls:
        return None
    ll0 = lls[0]
    if not isinstance(ll0, dict):
        return None
    title_out = ll0.get("*")
    return str(title_out).strip() if isinstance(title_out, str) and title_out.strip() else None


def fetch_wikipedia_page_images(*, title: str, lang: str, limit: int = 80) -> List[str]:
    """
    Return a list of file titles (e.g., "File:Example.jpg") referenced by the page.
    """
    data = _http_get_json(
        _wikipedia_api(lang),
        params={
            "action": "query",
            "titles": title,
            "prop": "images",
            "imlimit": int(limit),
            "redirects": 1,
            "format": "json",
        },
    )
    pages = (data.get("query") or {}).get("pages") or {}
    if not isinstance(pages, dict) or not pages:
        return []
    page = next(iter(pages.values()))
    if not isinstance(page, dict):
        return []
    imgs = page.get("images") or []
    if not isinstance(imgs, list):
        return []
    out: List[str] = []
    for it in imgs:
        if not isinstance(it, dict):
            continue
        t = it.get("title")
        if isinstance(t, str) and t.startswith("File:"):
            out.append(t.strip())
    return out


def _is_commons_upload(url: str) -> bool:
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.netloc.lower() != "upload.wikimedia.org":
        return False
    return "/wikipedia/commons/" in (u.path or "").lower()


def _filename_from_image_url(url: str) -> str:
    name = (urlparse(url).path or "").rsplit("/", 1)[-1]
    return unquote(name)


def fetch_commons_fileinfo(*, title: str) -> Dict[str, Optional[str]]:
    data = _http_get_json(
        _commons_api(),
        params={
            "action": "query",
            "titles": title if title.startswith("File:") else f"File:{title}",
            "prop": "imageinfo",
            "iiprop": "url|size|extmetadata",
            "format": "json",
        },
    )
    pages = (data.get("query") or {}).get("pages") or {}
    if not isinstance(pages, dict) or not pages:
        return {}
    page = next(iter(pages.values()))
    if not isinstance(page, dict):
        return {}
    info = ((page.get("imageinfo") or [{}]) or [{}])[0]
    if not isinstance(info, dict):
        return {}
    meta = info.get("extmetadata") or {}
    if not isinstance(meta, dict):
        meta = {}

    def _v(key: str) -> Optional[str]:
        val = meta.get(key) or {}
        if isinstance(val, dict):
            out = val.get("value")
            return str(out) if out is not None else None
        return None

    return {
        "url": str(info.get("url") or "").strip() or None,
        "width": str(info.get("width") or "").strip() or None,
        "height": str(info.get("height") or "").strip() or None,
        "license_short": _v("LicenseShortName"),
        "license_url": _v("LicenseUrl"),
        "artist": _v("Artist"),
        "credit": _v("Credit"),
        "attribution_required": _v("AttributionRequired"),
        "description_url": str(info.get("descriptionurl") or "").strip() or None,
    }


def fetch_wikipedia_file_url(*, file_title: str, lang: str) -> Optional[str]:
    data = _http_get_json(
        _wikipedia_api(lang),
        params={
            "action": "query",
            "titles": file_title,
            "prop": "imageinfo",
            "iiprop": "url",
            "format": "json",
        },
    )
    pages = (data.get("query") or {}).get("pages") or {}
    if not isinstance(pages, dict) or not pages:
        return None
    page = next(iter(pages.values()))
    if not isinstance(page, dict):
        return None
    info = ((page.get("imageinfo") or [{}]) or [{}])[0]
    if not isinstance(info, dict):
        return None
    u = info.get("url")
    return str(u).strip() if isinstance(u, str) and u.strip() else None


def _title_tokens_en(title_en: str) -> List[str]:
    s = re.sub(r"[^a-zA-Z0-9]+", " ", str(title_en or "").lower()).strip()
    if not s:
        return []
    stop = {"von", "de", "da", "di", "of", "the", "and", "jr", "sr"}
    parts = [p for p in s.split() if len(p) >= 3 and p not in stop]
    out: List[str] = []
    seen: set[str] = set()
    for p in parts:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out[:6]


def _score_file_title(file_title: str, *, tokens: List[str]) -> int:
    name = str(file_title or "")
    if name.startswith("File:"):
        name = name[5:]
    lowered = name.lower()
    ext = Path(lowered).suffix.lower()

    if ext in BAD_EXTS:
        return -10_000
    if ext not in GOOD_EXTS:
        return -1_000

    score = 200  # good ext base
    for bad in BAD_FILENAME_KEYWORDS:
        if bad in lowered:
            score -= 250
    for good in GOOD_FILENAME_KEYWORDS:
        if good in lowered:
            score += 80
    for tok in tokens:
        if tok and tok in lowered:
            score += 90
    # Mild preference for shorter, cleaner names.
    score -= min(120, max(0, len(lowered) - 30))
    return score


def resolve_best_portrait_image_url(
    *,
    person_title: str,
    wikipedia_lang: str,
) -> Tuple[Optional[str], Optional[str], Optional[str], Dict[str, Optional[str]]]:
    """
    Returns (page_url, image_url, chosen_file_title, commons_meta)
    """
    page_url, lead_url = fetch_wikipedia_page_image(title=person_title, lang=wikipedia_lang)

    # Hard overrides first (quality + consistency).
    override = IMAGE_FILE_OVERRIDES.get(person_title)
    if override:
        info = fetch_commons_fileinfo(title=override)
        if info.get("url"):
            return page_url, info.get("url"), override, info

    # Gather candidates from page images (ja + langlink(en) if available).
    candidates: List[str] = []
    if lead_url:
        candidates.append(f"File:{_filename_from_image_url(lead_url)}")
    candidates.extend(fetch_wikipedia_page_images(title=person_title, lang=wikipedia_lang, limit=120))

    en_title = fetch_wikipedia_langlink_title(title=person_title, from_lang=wikipedia_lang, to_lang="en")
    if en_title:
        candidates.extend(fetch_wikipedia_page_images(title=en_title, lang="en", limit=120))
    tokens = _title_tokens_en(en_title or "")

    # De-dup (keep order).
    uniq: List[str] = []
    seen: set[str] = set()
    for c in candidates:
        if not isinstance(c, str) or not c.startswith("File:"):
            continue
        if c in seen:
            continue
        seen.add(c)
        uniq.append(c)

    best = None
    best_score = -10_000
    for c in uniq:
        score = _score_file_title(c, tokens=tokens)
        if score > best_score:
            best = c
            best_score = score

    if best:
        info = fetch_commons_fileinfo(title=best)
        if info.get("url"):
            return page_url, info.get("url"), best, info
        # Some files are locally uploaded (non-Commons).
        u = fetch_wikipedia_file_url(file_title=best, lang=wikipedia_lang)
        if u:
            return page_url, u, best, {}

    return page_url, lead_url, None, {}

def _download_binary(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    dest.write_bytes(resp.content)

def _detect_primary_face_bbox(img: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    """
    Best-effort face bbox detection using OpenCV Haar cascades.
    Returns (x0, y0, x1, y1) in the image coordinate space.
    """
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return None

    im = img.convert("RGB")
    arr = np.array(im)
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)

    cascade_names = (
        "haarcascade_frontalface_default.xml",
        "haarcascade_frontalface_alt2.xml",
        "haarcascade_profileface.xml",
    )

    candidates: List[Tuple[int, int, int, int]] = []
    for name in cascade_names:
        try:
            cascade = cv2.CascadeClassifier(cv2.data.haarcascades + name)
            faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60))
        except Exception:
            continue
        for x, y, w, h in faces:
            x0 = int(x)
            y0 = int(y)
            x1 = int(x + w)
            y1 = int(y + h)
            if x1 > x0 and y1 > y0:
                candidates.append((x0, y0, x1, y1))

    if not candidates:
        return None

    w, h = im.size
    center_x = w / 2.0
    center_y = h / 2.0

    def _score(b: Tuple[int, int, int, int]) -> Tuple[float, float]:
        x0, y0, x1, y1 = b
        area = float((x1 - x0) * (y1 - y0))
        cx = (x0 + x1) / 2.0
        cy = (y0 + y1) / 2.0
        dist2 = (cx - center_x) ** 2 + (cy - center_y) ** 2
        return area, -dist2

    return max(candidates, key=_score)


def _crop_to_aspect_face_anchored(
    img: Image.Image,
    *,
    target_aspect: float,
    face_bbox: Tuple[int, int, int, int],
    face_target_x: float = 0.5,
    face_target_y: float = 0.5,
    face_height_ratio: float = 0.25,
    allow_padding: bool = True,
) -> Image.Image:
    """
    Crop to `target_aspect` (w/h) while keeping the detected face near the target point.

    - `face_target_x/y`: where the face center should land within the crop (0..1).
    - `face_height_ratio`: desired face bbox height relative to crop height.
    - `allow_padding`: if True, pad the source image with border-median color so the face can be centered
      even when it is near edges.
    """
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    target = float(target_aspect)
    if target <= 0:
        return img

    x0, y0, x1, y1 = face_bbox
    if x1 <= x0 or y1 <= y0:
        return img

    face_h = float(y1 - y0)
    ratio = max(0.05, min(0.95, float(face_height_ratio)))
    crop_h = max(1, int(round(face_h / ratio)))
    crop_w = max(1, int(round(crop_h * target)))

    # Ensure crop fits within the image, while keeping aspect exact.
    if crop_w > w:
        crop_w = w
        crop_h = max(1, int(round(crop_w / target)))
    if crop_h > h:
        crop_h = h
        crop_w = max(1, int(round(crop_h * target)))
    crop_w = max(1, min(w, crop_w))
    crop_h = max(1, min(h, crop_h))

    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0
    tx = max(0.0, min(1.0, float(face_target_x)))
    ty = max(0.0, min(1.0, float(face_target_y)))

    left = int(round(cx - (tx * crop_w)))
    top = int(round(cy - (ty * crop_h)))
    right = left + crop_w
    bottom = top + crop_h

    if allow_padding:
        fill_rgb, _ = _border_color_stats(img)
        pad_left = max(0, -left)
        pad_top = max(0, -top)
        pad_right = max(0, right - w)
        pad_bottom = max(0, bottom - h)
        if pad_left or pad_top or pad_right or pad_bottom:
            img = ImageOps.expand(img, border=(pad_left, pad_top, pad_right, pad_bottom), fill=fill_rgb)
            left += pad_left
            top += pad_top

    w2, h2 = img.size
    left = max(0, min(w2 - crop_w, left))
    top = max(0, min(h2 - crop_h, top))
    return img.crop((left, top, left + crop_w, top + crop_h))


def _crop_to_aspect(img: Image.Image, *, target_aspect: float, y_bias: float = 0.45) -> Image.Image:
    """
    Crop to target aspect (w/h). When cropping height, bias slightly upwards.
    """
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    target = float(target_aspect)
    if target <= 0:
        return img

    cur = w / float(h)
    if abs(cur - target) < 1e-3:
        return img

    if cur > target:
        new_w = max(1, min(w, int(round(h * target))))
        left = int(round((w - new_w) / 2))
        return img.crop((left, 0, left + new_w, h))

    new_h = max(1, min(h, int(round(w / target))))
    center_y = int(round(h * float(y_bias)))
    top = int(round(center_y - (new_h / 2)))
    top = max(0, min(h - new_h, top))
    return img.crop((0, top, w, top + new_h))


def _crop_to_aspect_zoomed(
    img: Image.Image,
    *,
    target_aspect: float,
    y_bias: float,
    zoom: float,
) -> Image.Image:
    """
    Crop to `target_aspect` (w/h) and apply a mild zoom-in by cropping a smaller region then resizing.

    This is a heuristic to make head/face larger even when the source photo is full-body.
    """
    w, h = img.size
    if w <= 0 or h <= 0:
        return img
    target = float(target_aspect)
    if target <= 0:
        return img

    zoom_f = max(1.0, float(zoom))

    # Base crop size that matches the aspect.
    cur = w / float(h)
    if cur > target:
        base_w = max(1, min(w, int(round(h * target))))
        base_h = h
    else:
        base_w = w
        base_h = max(1, min(h, int(round(w / target))))

    # Zoom by cropping smaller then resizing back to base size.
    crop_w = max(1, int(round(base_w / zoom_f)))
    crop_h = max(1, int(round(base_h / zoom_f)))

    # Keep aspect exact.
    if abs((crop_w / float(crop_h)) - target) > 1e-3:
        crop_w = max(1, int(round(crop_h * target)))

    cx = w / 2.0
    cy = max(0.0, min(float(h), float(h) * float(y_bias)))
    left = int(round(cx - (crop_w / 2.0)))
    top = int(round(cy - (crop_h / 2.0)))
    left = max(0, min(w - crop_w, left))
    top = max(0, min(h - crop_h, top))

    cropped = img.crop((left, top, left + crop_w, top + crop_h))
    return cropped.resize((base_w, base_h), Image.LANCZOS)


def _soft_ellipse_mask(size: Tuple[int, int], *, margin_ratio: float = 0.02, blur_ratio: float = 0.03) -> Image.Image:
    w, h = size
    mx = int(round(w * float(margin_ratio)))
    my = int(round(h * float(margin_ratio)))
    mx = max(0, min(w // 4, mx))
    my = max(0, min(h // 4, my))

    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((mx, my, w - mx, h - my), fill=255)

    blur = max(2, int(round(min(w, h) * float(blur_ratio))))
    return mask.filter(ImageFilter.GaussianBlur(radius=blur))


def _soft_rect_mask(size: Tuple[int, int], *, margin_ratio: float = 0.02, blur_ratio: float = 0.03) -> Image.Image:
    w, h = size
    mx = int(round(w * float(margin_ratio)))
    my = int(round(h * float(margin_ratio)))
    mx = max(0, min(w // 3, mx))
    my = max(0, min(h // 3, my))

    mask = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(mask)
    d.rectangle((mx, my, w - mx, h - my), fill=255)

    blur = max(2, int(round(min(w, h) * float(blur_ratio))))
    return mask.filter(ImageFilter.GaussianBlur(radius=blur))


def _border_color_stats(im: Image.Image) -> Tuple[Tuple[int, int, int], float]:
    """
    Returns (median_rgb, mean_abs_deviation_from_median) based on border samples.
    """
    rgb = im.convert("RGB")
    w, h = rgb.size
    if w <= 2 or h <= 2:
        return (0, 0, 0), 999.0
    step = max(1, int(round(min(w, h) / 200)))
    px = rgb.load()
    samples: List[Tuple[int, int, int]] = []
    for x in range(0, w, step):
        samples.append(px[x, 0])
        samples.append(px[x, h - 1])
    for y in range(0, h, step):
        samples.append(px[0, y])
        samples.append(px[w - 1, y])
    if not samples:
        return (0, 0, 0), 999.0

    rs = sorted([p[0] for p in samples])
    gs = sorted([p[1] for p in samples])
    bs = sorted([p[2] for p in samples])
    mid = len(samples) // 2
    med = (rs[mid], gs[mid], bs[mid])

    mad = 0.0
    for r, g, b in samples:
        mad += (abs(r - med[0]) + abs(g - med[1]) + abs(b - med[2])) / 3.0
    mad /= max(1, len(samples))
    return med, mad


def _alpha_from_border_key(im: Image.Image, *, low: int = 18, high: int = 60) -> Image.Image:
    """
    Simple background key based on border median color.
    Good for portraits on relatively uniform backgrounds.
    """
    rgb = im.convert("RGB")
    bg, mad = _border_color_stats(rgb)
    if mad > 18.0:
        return _soft_rect_mask(rgb.size, margin_ratio=0.02, blur_ratio=0.03)

    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, bg))
    g = ImageOps.grayscale(diff)

    lo = int(max(0, min(255, low)))
    hi = int(max(1, min(255, high)))
    if hi <= lo:
        hi = min(255, lo + 1)

    def _map(p: int) -> int:
        if p <= lo:
            return 0
        if p >= hi:
            return 255
        return int(round((p - lo) * 255.0 / float(hi - lo)))

    a = g.point(_map)
    a = a.filter(ImageFilter.GaussianBlur(radius=max(2, int(round(min(rgb.size) * 0.012)))))
    edge = _soft_rect_mask(rgb.size, margin_ratio=0.012, blur_ratio=0.02)
    return ImageChops.multiply(a, edge)


def prepare_portrait_png(
    src_path: Path,
    dest_path: Path,
    *,
    target_aspect: float,
    brightness: float = 1.0,
    contrast: float = 1.0,
    color: float = 1.0,
    y_bias: float = 0.34,
    zoom: float = 1.18,
    use_face_crop: bool = True,
    face_target_x: float = 0.5,
    face_target_y: float = 0.5,
    face_height_ratio: float = 0.25,
    max_height_px: int = 1400,
) -> None:
    with Image.open(src_path) as im_in:
        im = ImageOps.exif_transpose(im_in)
        im = im.convert("RGB")

    face_bbox = _detect_primary_face_bbox(im) if use_face_crop else None
    if face_bbox:
        im = _crop_to_aspect_face_anchored(
            im,
            target_aspect=float(target_aspect),
            face_bbox=face_bbox,
            face_target_x=float(face_target_x),
            face_target_y=float(face_target_y),
            face_height_ratio=float(face_height_ratio),
            allow_padding=True,
        )
    else:
        im = _crop_to_aspect_zoomed(im, target_aspect=float(target_aspect), y_bias=float(y_bias), zoom=float(zoom))

    # Keep files lightweight; the compositor downsizes anyway (dest box ~820px tall).
    mh = int(max_height_px)
    if mh > 0 and im.size[1] > mh:
        new_h = mh
        new_w = max(1, int(round(new_h * float(target_aspect))))
        im = im.resize((new_w, new_h), Image.LANCZOS)
    if abs(float(brightness) - 1.0) >= 1e-6:
        im = ImageEnhance.Brightness(im).enhance(float(brightness))
    if abs(float(contrast) - 1.0) >= 1e-6:
        im = ImageEnhance.Contrast(im).enhance(float(contrast))
    if abs(float(color) - 1.0) >= 1e-6:
        im = ImageEnhance.Color(im).enhance(float(color))
    im = im.filter(ImageFilter.UnsharpMask(radius=2, percent=130, threshold=3))

    rgba = im.convert("RGBA")
    rgba.putalpha(_alpha_from_border_key(im, low=18, high=60))

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    rgba.save(dest_path, format="PNG", optimize=True)


def _iter_planning_people(channel: str) -> Dict[str, str]:
    ch = _normalize_channel(channel)
    mapping: Dict[str, str] = {}
    for row in planning_store.get_rows(ch, force_refresh=True):
        raw = row.raw if isinstance(row.raw, dict) else {}
        video = str(raw.get("動画番号") or row.video_number or "").strip()
        if not video:
            continue
        v = _normalize_video(video)
        person = str(raw.get("悩みタグ_サブ") or "").strip()
        if not person:
            continue
        mapping[v] = person
    return mapping


def _parse_videos_arg(videos: Optional[List[str]]) -> List[str]:
    if not videos:
        return []
    out: List[str] = []
    for raw in videos:
        if not raw:
            continue
        for part in str(raw).split(","):
            part = part.strip()
            if not part:
                continue
            out.append(_normalize_video(part))
    return sorted(set(out))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch CH26 portraits from Wikipedia/Wikimedia and prep 20_portrait.png")
    ap.add_argument("--channel", default="CH26", help="channel code (default: CH26)")
    ap.add_argument("--lang", default="ja", help="wikipedia language (default: ja)")
    ap.add_argument("--videos", action="append", help="target videos (e.g. --videos 001,002)")
    ap.add_argument("--overwrite", action="store_true", help="overwrite existing portrait files")
    ap.add_argument(
        "--reuse-src",
        action="store_true",
        help="use existing 20_portrait_src.* in each video folder and skip download (for manual sources)",
    )
    ap.add_argument("--no-face-crop", action="store_true", help="disable face-based crop and use heuristic crop only")
    ap.add_argument("--face-target-x", type=float, default=0.5, help="face center target X in crop (0..1, default 0.5)")
    ap.add_argument("--face-target-y", type=float, default=0.5, help="face center target Y in crop (0..1, default 0.5)")
    ap.add_argument(
        "--face-height-ratio",
        type=float,
        default=0.25,
        help="desired face bbox height / crop height (default 0.25; larger => tighter crop)",
    )
    ap.add_argument("--dry-run", action="store_true", help="print actions without writing files")
    ap.add_argument("--sleep-sec", type=float, default=0.25, help="sleep between requests (default: 0.25)")
    args = ap.parse_args(argv)

    ch = _normalize_channel(args.channel)
    lang = str(args.lang or "ja").strip().lower()
    targets = _parse_videos_arg(args.videos)

    people = _iter_planning_people(ch)
    if not people:
        print(f"No planning rows found for channel={ch} (or missing 悩みタグ_サブ).")
        return 2

    if not targets:
        targets = sorted(people.keys())

    # Keep in sync with: packages/script_pipeline/thumbnails/tools/layer_specs_builder.py
    target_aspect = 0.42 / 0.76

    ok = 0
    skipped = 0
    failed = 0
    for vid in targets:
        person_raw = people.get(vid)
        if not person_raw:
            skipped += 1
            continue

        video_dir = fpaths.thumbnail_assets_dir(ch, vid)
        png_out = video_dir / "20_portrait.png"
        if png_out.exists() and not args.overwrite:
            skipped += 1
            continue

        meta_out = video_dir / "20_portrait.source.json"

        local_src: Optional[Path] = None
        if args.reuse_src:
            candidates = sorted(video_dir.glob("20_portrait_src.*"))
            for candidate in candidates:
                if candidate.suffix.lower() in GOOD_EXTS:
                    local_src = candidate
                    break
            if not local_src and candidates:
                local_src = candidates[0]

        if local_src:
            src_path = local_src
            print(f"{ch}-{vid}: reuse {src_path.name} (skip download)")
        else:
            person_title = _resolve_person_title(person_raw)
            page_url, image_url, chosen_file, info = resolve_best_portrait_image_url(
                person_title=person_title, wikipedia_lang=lang
            )
            if not image_url:
                print(f"{ch}-{vid}: failed to resolve page image for '{person_title}' ({person_raw})")
                failed += 1
                continue

            filename = _filename_from_image_url(image_url)
            ext = Path(filename).suffix.lower() or ".jpg"
            src_path = video_dir / f"20_portrait_src{ext}"

            src = PortraitSource(
                person_raw=person_raw,
                person_title=person_title,
                wikipedia_lang=lang,
                wikipedia_page_url=page_url,
                image_url=image_url,
                image_filename=filename,
                downloaded_at=_now_iso(),
                license_short=info.get("license_short"),
                license_url=info.get("license_url"),
                artist=info.get("artist"),
                credit=info.get("credit"),
                attribution_required=info.get("attribution_required"),
                description_url=info.get("description_url"),
            )

            chosen_note = f" ({chosen_file})" if chosen_file else ""
            print(f"{ch}-{vid}: {person_title} -> {image_url}{chosen_note}")
        if args.dry_run:
            ok += 1
            continue

        try:
            if not local_src:
                _download_binary(image_url, src_path)
            prepare_portrait_png(
                src_path,
                png_out,
                target_aspect=float(target_aspect),
                brightness=1.0,
                contrast=1.0,
                color=1.0,
                y_bias=0.34,
                zoom=1.18,
                use_face_crop=not bool(args.no_face_crop),
                face_target_x=float(args.face_target_x),
                face_target_y=float(args.face_target_y),
                face_height_ratio=float(args.face_height_ratio),
            )
            if not local_src:
                meta_out.write_text(json.dumps(src.__dict__, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"{ch}-{vid}: ERROR {exc}")
            failed += 1

        time.sleep(max(0.0, float(args.sleep_sec)))

    print(f"done: ok={ok} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
