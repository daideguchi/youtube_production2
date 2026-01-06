from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from factory_common.paths import video_state_root


DEFAULT_CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h (Pixabay required; others recommended)


def _now() -> int:
    return int(time.time())


def normalize_query(query: str) -> str:
    q = str(query or "").strip()
    q = re.sub(r"\s+", " ", q)
    return q


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _cache_key(provider: str, params: Dict[str, Any]) -> str:
    raw = json.dumps({"provider": provider, "params": params}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _cache_dir(provider: str) -> Path:
    return video_state_root() / "stock_broll_cache" / provider


def _load_cache(provider: str, key: str, ttl_seconds: int) -> Optional[Dict[str, Any]]:
    p = _cache_dir(provider) / f"{key}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ts = int(data.get("_cached_at", 0))
        if _now() - ts <= ttl_seconds:
            return data.get("payload")
    except Exception:
        return None
    return None


def _save_cache(provider: str, key: str, payload: Dict[str, Any]) -> None:
    d = _cache_dir(provider)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{key}.json"
    obj = {"_cached_at": _now(), "payload": payload}
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _http_get_json(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    r = requests.get(url, headers=headers, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _http_download(
    url: str,
    *,
    out_path: Path,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 180,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, headers=headers, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with out_path.open("wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def _aspect_ratio_bucket(width: int, height: int) -> str:
    if height <= 0:
        return "unknown"
    ratio = float(width) / float(height)
    if 1.70 <= ratio <= 1.82:
        return "16:9"
    if 1.30 <= ratio <= 1.37:
        return "4:3"
    if 0.54 <= ratio <= 0.58:
        return "9:16"
    if 0.95 <= ratio <= 1.05:
        return "1:1"
    return "other"


@dataclass(frozen=True)
class StockVideoCandidate:
    provider: str
    provider_id: str
    page_url: str
    creator: str
    width: int
    height: int
    duration_sec: float
    download_url: str
    preview_url: str
    license_note: str
    raw: Dict[str, Any]


def _score_candidate(
    c: StockVideoCandidate,
    *,
    min_w: int,
    min_h: int,
    prefer_ar: str,
    desired_duration_sec: float,
    max_duration_sec: float,
) -> float:
    score = 0.0

    # Resolution
    # Prefer sufficient resolution, but avoid oversized sources (e.g. 4K) which waste bandwidth/disk.
    # Cap is operator-configurable to match the timeline export (default: 1920x1080).
    max_w = _env_int("YTM_BROLL_MAX_W", 1920)
    max_h = _env_int("YTM_BROLL_MAX_H", 1080)
    eff_w = min(int(c.width or 0), max_w) if max_w > 0 else int(c.width or 0)
    eff_h = min(int(c.height or 0), max_h) if max_h > 0 else int(c.height or 0)
    score += min(eff_w / max(min_w, 1), 4.0) * 10.0
    score += min(eff_h / max(min_h, 1), 4.0) * 6.0
    if max_w > 0 and max_h > 0 and (int(c.width or 0) > max_w or int(c.height or 0) > max_h):
        over = max((int(c.width or 0) / max_w) if max_w else 1.0, (int(c.height or 0) / max_h) if max_h else 1.0)
        score -= 12.0 * max(0.0, over - 1.0)

    # Aspect ratio
    ar = _aspect_ratio_bucket(c.width, c.height)
    if prefer_ar and ar == prefer_ar:
        score += 18.0
    elif prefer_ar and ar != "unknown":
        score -= 4.0

    # Duration: prefer candidates that can cover the cue without stretching.
    if c.duration_sec <= 0:
        score -= 20.0
    else:
        # Hard cap
        if max_duration_sec > 0 and c.duration_sec > max_duration_sec:
            score -= 25.0
        # Prefer >= desired
        if desired_duration_sec > 0:
            if c.duration_sec + 0.5 >= desired_duration_sec:
                score += 14.0
                score += max(0.0, 6.0 - abs(c.duration_sec - desired_duration_sec))
            else:
                score -= 10.0 + (desired_duration_sec - c.duration_sec)

    return score


def pick_best_candidate(
    candidates: Iterable[StockVideoCandidate],
    *,
    min_w: int,
    min_h: int,
    prefer_ar: str,
    desired_duration_sec: float,
    max_duration_sec: float,
) -> Optional[StockVideoCandidate]:
    best: Optional[Tuple[float, StockVideoCandidate]] = None
    for c in candidates:
        if c.width and c.height and (c.width < min_w or c.height < min_h):
            continue
        sc = _score_candidate(
            c,
            min_w=min_w,
            min_h=min_h,
            prefer_ar=prefer_ar,
            desired_duration_sec=desired_duration_sec,
            max_duration_sec=max_duration_sec,
        )
        if best is None or sc > best[0]:
            best = (sc, c)
    return best[1] if best else None


# -----------------------
# Providers
# -----------------------


def search_pexels_videos(
    *,
    query: str,
    per_page: int = 80,
    page: int = 1,
    orientation: str = "landscape",
    size: str = "medium",
    locale: str = "en-US",
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> Dict[str, Any]:
    api_key = (os.getenv("PEXELS_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY is missing")

    url = "https://api.pexels.com/videos/search"
    params: Dict[str, Any] = {
        "query": normalize_query(query),
        "per_page": int(per_page),
        "page": int(page),
    }
    if orientation:
        params["orientation"] = orientation
    if size:
        params["size"] = size
    if locale:
        params["locale"] = locale

    key = _cache_key("pexels", params)
    cached = _load_cache("pexels", key, cache_ttl_seconds)
    if cached is not None:
        return cached

    headers = {"Authorization": api_key}
    payload = _http_get_json(url, headers=headers, params=params)
    _save_cache("pexels", key, payload)
    return payload


def pexels_video_candidates(payload: Dict[str, Any]) -> List[StockVideoCandidate]:
    out: List[StockVideoCandidate] = []
    for v in payload.get("videos", []) or []:
        vid = str(v.get("id") or "")
        if not vid:
            continue
        duration = float(v.get("duration") or 0.0)
        page_url = str(v.get("url") or "")
        user = v.get("user") or {}
        creator = str(user.get("name") or "")
        preview = str(v.get("image") or "")

        files = v.get("video_files", []) or []
        for f in files:
            if str(f.get("file_type") or "").lower() != "video/mp4":
                continue
            w = int(f.get("width") or 0)
            h = int(f.get("height") or 0)
            link = str(f.get("link") or "")
            if not link:
                continue
            out.append(
                StockVideoCandidate(
                    provider="pexels",
                    provider_id=vid,
                    page_url=page_url,
                    creator=creator,
                    width=w,
                    height=h,
                    duration_sec=duration,
                    download_url=link,
                    preview_url=preview,
                    license_note="Pexels: show source/credit when displaying search results (see docs).",
                    raw=v,
                )
            )
    return out


def search_pixabay_videos(
    *,
    query: str,
    per_page: int = 50,
    page: int = 1,
    lang: str = "en",
    min_w: int = 1280,
    min_h: int = 720,
    safesearch: bool = True,
    order: str = "popular",
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> Dict[str, Any]:
    api_key = (os.getenv("PIXABAY_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("PIXABAY_API_KEY is missing")

    url = "https://pixabay.com/api/videos/"
    params: Dict[str, Any] = {
        "key": api_key,
        "q": normalize_query(query),
        "per_page": int(per_page),
        "page": int(page),
        "min_width": int(min_w),
        "min_height": int(min_h),
        "order": order,
        "safesearch": "true" if safesearch else "false",
    }
    if lang:
        params["lang"] = lang

    # do not mix key into cache key
    cache_params = {k: v for k, v in params.items() if k != "key"}
    key = _cache_key("pixabay", cache_params)
    cached = _load_cache("pixabay", key, cache_ttl_seconds)
    if cached is not None:
        return cached

    payload = _http_get_json(url, params=params)
    _save_cache("pixabay", key, payload)
    return payload


def pixabay_video_candidates(payload: Dict[str, Any]) -> List[StockVideoCandidate]:
    out: List[StockVideoCandidate] = []
    for hit in payload.get("hits", []) or []:
        vid = str(hit.get("id") or "")
        if not vid:
            continue
        duration = float(hit.get("duration") or 0.0)
        page_url = str(hit.get("pageURL") or "")
        creator = str(hit.get("user") or "")
        tags = str(hit.get("tags") or "")

        videos = hit.get("videos") or {}
        # prefer medium/large that exists
        chosen = None  # (w,h,url,thumb)
        for k in ("medium", "large", "small", "tiny"):
            v = videos.get(k) or {}
            url = str(v.get("url") or "")
            if not url:
                continue
            w = int(v.get("width") or 0)
            h = int(v.get("height") or 0)
            thumb = str(v.get("thumbnail") or "")
            chosen = (w, h, url, thumb)
            # medium is available for all; stop at first match
            break
        if not chosen:
            continue

        dl = chosen[2]
        dl = dl + ("&download=1" if "?" in dl else "?download=1")

        out.append(
            StockVideoCandidate(
                provider="pixabay",
                provider_id=vid,
                page_url=page_url,
                creator=creator,
                width=chosen[0],
                height=chosen[1],
                duration_sec=duration,
                download_url=dl,
                preview_url=chosen[3],
                license_note=f"Pixabay: cache 24h, avoid systematic mass downloads. tags={tags}",
                raw=hit,
            )
        )
    return out


def search_coverr_videos(
    *,
    query: str,
    page: int = 0,
    page_size: int = 20,
    sort: str = "popular",
    include_urls: bool = True,
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> Dict[str, Any]:
    api_key = (os.getenv("COVERR_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("COVERR_API_KEY is missing")

    url = "https://api.coverr.co/videos"
    params: Dict[str, Any] = {
        "page": int(page),
        "page_size": int(page_size),
        "sort": sort,
    }
    if query:
        params["query"] = normalize_query(query)
    if include_urls:
        params["urls"] = "true"

    key = _cache_key("coverr", params)
    cached = _load_cache("coverr", key, cache_ttl_seconds)
    if cached is not None:
        return cached

    headers = {"Authorization": f"Bearer {api_key}"}
    payload = _http_get_json(url, headers=headers, params=params)
    _save_cache("coverr", key, payload)
    return payload


def coverr_video_candidates(payload: Dict[str, Any]) -> List[StockVideoCandidate]:
    out: List[StockVideoCandidate] = []
    for hit in payload.get("hits", []) or []:
        vid = str(hit.get("id") or "")
        if not vid:
            continue
        title = str(hit.get("title") or "")
        duration = float(hit.get("duration") or 0.0)
        w = int(hit.get("max_width") or 0)
        h = int(hit.get("max_height") or 0)
        urls = hit.get("urls") or {}
        mp4 = str(urls.get("mp4") or "")
        mp4_preview = str(urls.get("mp4_preview") or "")
        mp4_download = str(urls.get("mp4_download") or "")
        dl = mp4 or mp4_download
        if not dl:
            continue

        out.append(
            StockVideoCandidate(
                provider="coverr",
                provider_id=vid,
                page_url=f"https://coverr.co/videos/{vid}",
                creator="",
                width=w,
                height=h,
                duration_sec=duration,
                download_url=dl,
                preview_url=mp4_preview,
                license_note=f"Coverr: register downloads via API. title={title}",
                raw=hit,
            )
        )
    return out


def coverr_register_download(coverr_id: str) -> None:
    api_key = (os.getenv("COVERR_API_KEY") or "").strip()
    if not api_key or not coverr_id:
        return
    url = f"https://api.coverr.co/videos/{coverr_id}/stats/downloads"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        r = requests.patch(url, headers=headers, timeout=20)
        # docs: 204 expected
        _ = r.status_code
    except Exception:
        return


def fetch_best_stock_video(
    *,
    provider: str,
    query: str,
    out_path: Path,
    desired_duration_sec: float,
    max_duration_sec: float,
    min_w: int,
    min_h: int,
    prefer_ar: str = "16:9",
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> Optional[Tuple[Path, Dict[str, Any]]]:
    provider_norm = str(provider or "").strip().lower()
    if provider_norm in {"pixel", "pexels"}:
        payload = search_pexels_videos(query=query, cache_ttl_seconds=cache_ttl_seconds)
        cand = pick_best_candidate(
            pexels_video_candidates(payload),
            min_w=min_w,
            min_h=min_h,
            prefer_ar=prefer_ar,
            desired_duration_sec=desired_duration_sec,
            max_duration_sec=max_duration_sec,
        )
        if not cand:
            return None
        _http_download(cand.download_url, out_path=out_path, headers={"Authorization": (os.getenv("PEXELS_API_KEY") or "").strip()})
        meta = {
            "provider": cand.provider,
            "provider_id": cand.provider_id,
            "page_url": cand.page_url,
            "creator": cand.creator,
            "width": cand.width,
            "height": cand.height,
            "duration_sec": cand.duration_sec,
            "query": normalize_query(query),
            "license_note": cand.license_note,
        }
        return out_path, meta

    if provider_norm == "pixabay":
        payload = search_pixabay_videos(query=query, min_w=min_w, min_h=min_h, cache_ttl_seconds=cache_ttl_seconds)
        cand = pick_best_candidate(
            pixabay_video_candidates(payload),
            min_w=min_w,
            min_h=min_h,
            prefer_ar=prefer_ar,
            desired_duration_sec=desired_duration_sec,
            max_duration_sec=max_duration_sec,
        )
        if not cand:
            return None
        _http_download(cand.download_url, out_path=out_path)
        meta = {
            "provider": cand.provider,
            "provider_id": cand.provider_id,
            "page_url": cand.page_url,
            "creator": cand.creator,
            "width": cand.width,
            "height": cand.height,
            "duration_sec": cand.duration_sec,
            "query": normalize_query(query),
            "license_note": cand.license_note,
        }
        return out_path, meta

    if provider_norm == "coverr":
        payload = search_coverr_videos(query=query, cache_ttl_seconds=cache_ttl_seconds)
        cand = pick_best_candidate(
            coverr_video_candidates(payload),
            min_w=min_w,
            min_h=min_h,
            prefer_ar=prefer_ar,
            desired_duration_sec=desired_duration_sec,
            max_duration_sec=max_duration_sec,
        )
        if not cand:
            return None
        headers = {"Authorization": f"Bearer {(os.getenv('COVERR_API_KEY') or '').strip()}"}
        _http_download(cand.download_url, out_path=out_path, headers=headers)
        coverr_register_download(cand.provider_id)
        meta = {
            "provider": cand.provider,
            "provider_id": cand.provider_id,
            "page_url": cand.page_url,
            "creator": cand.creator,
            "width": cand.width,
            "height": cand.height,
            "duration_sec": cand.duration_sec,
            "query": normalize_query(query),
            "license_note": cand.license_note,
        }
        return out_path, meta

    raise ValueError(f"Unknown broll provider: {provider}")
