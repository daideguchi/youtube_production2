from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .fetcher import DEFAULT_CACHE_TTL_SECONDS, fetch_best_stock_video, normalize_query


@dataclass(frozen=True)
class BrollInjectionSummary:
    provider: str
    target_count: int
    injected_count: int
    manifest_path: str
    cues_path: str


def _cue_start_end(cue: Dict[str, Any]) -> tuple[float, float]:
    try:
        start = float(cue.get("start_sec") or 0.0)
        end = float(cue.get("end_sec") or 0.0)
    except Exception:
        return 0.0, 0.0
    return start, end


def _cue_duration_sec(cue: Dict[str, Any]) -> float:
    start, end = _cue_start_end(cue)
    return max(0.0, end - start)


def _cue_mid_sec(cue: Dict[str, Any]) -> float:
    start, end = _cue_start_end(cue)
    return (start + end) / 2.0 if (start or end) else 0.0


def _pick_query(cue: Dict[str, Any]) -> str:
    visual_focus = str(cue.get("visual_focus") or "").strip()
    summary = str(cue.get("summary") or "").strip()
    text = str(cue.get("text") or "").strip()

    base = visual_focus if (visual_focus and re.search(r"[A-Za-z]", visual_focus)) else (summary or text)
    q = normalize_query(base)
    q = re.sub(r"[\"'`<>]", "", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q[:90]


_COVERR_STOPWORDS = {
    "a",
    "an",
    "the",
    "in",
    "on",
    "at",
    "of",
    "to",
    "from",
    "with",
    "without",
    "into",
    "out",
    "for",
    "and",
    "or",
    "as",
    "by",
    "while",
    "during",
    "looking",
    "close",
    "up",
    "closeup",
    "view",
    "shot",
    "person",
    "people",
    "man",
    "woman",
    "hands",
    "hand",
}


def _pick_query_for_provider(cue: Dict[str, Any], provider_dir: str) -> str:
    """
    Provider-specific query shaping.

    - Pexels/Pixabay: allow descriptive phrases.
    - Coverr: prefer short keyword queries (long phrases often return 0 hits).
    """
    visual_focus = str(cue.get("visual_focus") or "").strip()
    summary = str(cue.get("summary") or "").strip()
    text = str(cue.get("text") or "").strip()

    # Prefer concise, drawable hints; avoid full raw text unless needed.
    base = visual_focus or summary or text
    base = normalize_query(base)

    if provider_dir != "coverr":
        base = _maybe_translate_to_english_query(base)
        base = re.sub(r"[\"'`<>]", "", base).strip()
        return base[:90]

    # Coverr: extract a few strong keywords from the English parts.
    hint = _maybe_translate_to_english_query(f"{visual_focus} {summary}".strip())
    words = re.findall(r"[A-Za-z]+", hint.lower())
    kept: List[str] = []
    for w in words:
        if w in _COVERR_STOPWORDS:
            continue
        if w not in kept:
            kept.append(w)
        if len(kept) >= 3:
            break
    q = " ".join(kept).strip()
    if q:
        return q[:60]
    # Fallback: short phrase
    return base[:60]


def _coverr_query_candidates(cue: Dict[str, Any]) -> List[str]:
    visual_focus = str(cue.get("visual_focus") or "").strip()
    summary = str(cue.get("summary") or "").strip()
    base = normalize_query(_maybe_translate_to_english_query(visual_focus or summary or ""))
    base = re.sub(r"[\"'`<>]", "", base).strip()

    words = re.findall(r"[A-Za-z]+", base.lower())
    kept: List[str] = []
    for w in words:
        if w in _COVERR_STOPWORDS:
            continue
        if w not in kept:
            kept.append(w)
    candidates: List[str] = []

    def _add(q: str) -> None:
        qn = normalize_query(q)[:60]
        if qn and qn not in candidates:
            candidates.append(qn)

    if kept:
        _add(" ".join(kept[:3]))
        _add(" ".join(kept[:2]))
        # Single keywords (reverse tends to keep the “subject” last)
        for w in reversed(kept):
            _add(w)
            if len(candidates) >= 6:
                break

    if base:
        _add(base)

    return candidates[:6]


def _score_cue_for_broll(cue: Dict[str, Any]) -> float:
    visual_focus = str(cue.get("visual_focus") or "").strip()
    summary = str(cue.get("summary") or "").strip()
    role = str(cue.get("role_tag") or "").strip().lower()
    dur = _cue_duration_sec(cue)

    score = 0.0

    if visual_focus:
        score += 28.0
        if re.search(r"[A-Za-z]", visual_focus):
            score += 6.0
    else:
        score -= 8.0

    if summary:
        score += 10.0

    # Avoid meta/title-ish cues when present.
    if role in {"title", "meta", "outro", "intro"}:
        score -= 16.0

    # Prefer concrete visuals; penalize “text/diagram/UI” style directions.
    combined = f"{visual_focus} {summary}"
    if re.search(r"\b(text|subtitle|caption|diagram|chart|graph|logo|ui)\b", combined, flags=re.IGNORECASE):
        score -= 22.0
    if re.search(
        r"\b(monks?|temple|shrine|writing|book|paper|letter|desk|candle|incense|bed|window|street)\b",
        combined,
        flags=re.IGNORECASE,
    ):
        score += 8.0

    # Duration: favor segments where “one b-roll clip” can plausibly cover the cue.
    if dur <= 0:
        score -= 40.0
    elif dur > 40.0:
        score -= 34.0
    elif dur > 34.0:
        score -= 22.0
    elif dur > 28.0:
        score -= 12.0
    elif dur < 6.0:
        score -= 10.0

    return score


def _provider_norm(provider: str) -> str:
    p = str(provider or "").strip().lower()
    if p in {"pixel", "pexels"}:
        return "pexels"
    return p


_BROLL_QUERY_CACHE: Dict[str, str] = {}


def _maybe_translate_to_english_query(text: str) -> str:
    """
    Stock providers (pexels/pixabay) are heavily English-indexed. When cues are Japanese-only,
    translate into a short English keyword query for higher hit rates.

    This is NOT a visual-motif shortcut: it should preserve the concrete scene described by the cue.
    """
    raw = normalize_query(text)
    if not raw:
        return ""
    if re.search(r"[A-Za-z]", raw):
        return raw

    key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    cached = _BROLL_QUERY_CACHE.get(key)
    if cached:
        return cached

    # Lazy import to keep injector lightweight for non-LLM workflows.
    try:
        from factory_common.llm_router import get_router  # noqa: WPS433 (runtime import)
    except Exception:
        return raw

    prompt = "\n".join(
        [
            "Convert the following Japanese scene description into an English stock video search query.",
            "Output rules:",
            "- Return ONLY the query (no quotes, no punctuation).",
            "- 2–5 simple words, concrete visible objects/actions/places.",
            "- Avoid metaphors/symbols; avoid 'illustration', 'painting', 'anime'.",
            "- No brand names, no text, no UI.",
            "",
            f"Japanese: {raw}",
        ]
    )

    try:
        # IMPORTANT:
        # - OpenRouter (standard tier) may be out-of-credits in this repo.
        # - Fireworks script keys can be suspended depending on the machine/state.
        # Use a stable, SSOT-provisioned Azure model explicitly for this small translation.
        q = str(
            get_router().call(
                task="broll_query",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=128,
                timeout=60,
                model_keys=["azure_gpt5_mini"],
            )
            or ""
        ).strip()
    except BaseException:
        return raw

    q = q.splitlines()[0].strip()
    q = re.sub(r"[\"'`<>]", "", q)
    q = re.sub(r"[^A-Za-z0-9 _-]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    if not q:
        return raw

    # Cache and return.
    _BROLL_QUERY_CACHE[key] = q
    return q


def inject_broll_into_run(
    *,
    run_dir: Path,
    provider: str,
    ratio: float = 0.2,
    min_gap_sec: float = 90.0,
    min_cover_ratio: float = 0.65,
    min_w: int = 1280,
    min_h: int = 720,
    prefer_ar: str = "16:9",
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> BrollInjectionSummary:
    """
    Inject stock B-roll MP4 assets into an existing srt2images run_dir.

    - Reads run_dir/image_cues.json
    - Picks ~ratio of cues using contextual scoring (NOT uniform spacing)
    - Downloads a best-effort stock video per selected cue
    - Writes:
        - run_dir/image_cues.json (adds asset_kind/asset_relpath/broll_meta per cue)
        - run_dir/broll_manifest.json (credits/debug)
    """
    provider_in = str(provider or "").strip()
    if not provider_in or provider_in.lower() == "none":
        raise ValueError("provider must be set (not 'none')")

    run_dir = Path(run_dir).expanduser().resolve()
    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        raise FileNotFoundError(f"image_cues.json not found: {cues_path}")

    data = json.loads(cues_path.read_text(encoding="utf-8"))
    cues: List[Dict[str, Any]] = list(data.get("cues") or [])
    if not cues:
        manifest_path = run_dir / "broll_manifest.json"
        manifest_path.write_text(
            json.dumps(
                {"provider": provider_in, "target_count": 0, "injected": [], "note": "no cues"},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return BrollInjectionSummary(
            provider=provider_in,
            target_count=0,
            injected_count=0,
            manifest_path=str(manifest_path),
            cues_path=str(cues_path),
        )

    ratio_f = float(ratio)
    ratio_f = 0.0 if ratio_f < 0 else ratio_f
    target = int(round(len(cues) * ratio_f)) if ratio_f > 0 else 0
    target = max(0, min(len(cues), target))

    provider_dir = _provider_norm(provider_in)
    out_dir = run_dir / "broll" / provider_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build scored candidate list
    scored: List[tuple[float, int]] = []
    for idx, cue in enumerate(cues):
        if not isinstance(cue, dict):
            continue
        sc = _score_cue_for_broll(cue)
        scored.append((sc, idx))
    scored.sort(key=lambda t: t[0], reverse=True)

    injected: List[Dict[str, Any]] = []
    selected_mids: List[float] = []

    for sc, idx in scored:
        if len(injected) >= target:
            break
        cue = cues[idx]
        if not isinstance(cue, dict):
            continue

        cue_mid = _cue_mid_sec(cue)
        if selected_mids and min_gap_sec > 0:
            if any(abs(cue_mid - m) < float(min_gap_sec) for m in selected_mids):
                continue

        cue_dur = _cue_duration_sec(cue)
        if cue_dur <= 0.0:
            continue

        query_candidates = (
            _coverr_query_candidates(cue) if provider_dir == "coverr" else [_pick_query_for_provider(cue, provider_dir)]
        )
        query_candidates = [q for q in query_candidates if q]
        if not query_candidates:
            continue

        # Prefer clips around ~20s for long cues; allow mild slow-down by min_cover_ratio.
        desired = min(20.0, cue_dur)
        min_required = cue_dur * float(min_cover_ratio)

        out_path = out_dir / f"{idx+1:04d}.mp4"
        meta = None
        used_query = None
        for query in query_candidates:
            try:
                res = fetch_best_stock_video(
                    provider=provider_in,
                    query=query,
                    out_path=out_path,
                    desired_duration_sec=desired,
                    max_duration_sec=max(60.0, cue_dur),
                    min_w=min_w,
                    min_h=min_h,
                    prefer_ar=prefer_ar,
                    cache_ttl_seconds=cache_ttl_seconds,
                )
            except Exception:
                res = None
            if not res:
                continue
            _, meta = res
            used_query = query
            break
        if not meta:
            continue

        try:
            cand_dur = float(meta.get("duration_sec") or 0.0)
        except Exception:
            cand_dur = 0.0
        if cand_dur > 0.0 and cand_dur + 0.25 < min_required:
            # Too short for this cue (would require extreme slow-down). Skip.
            try:
                if out_path.exists():
                    out_path.unlink()
            except Exception:
                pass
            continue

        rel = out_path.relative_to(run_dir).as_posix()
        cue["asset_kind"] = "video"
        cue["asset_relpath"] = rel
        cue["broll_meta"] = meta

        injected.append(
            {
                "index": int(cue.get("index") or idx),
                "cue_list_index": idx,
                "start_sec": float(cue.get("start_sec") or 0.0),
                "end_sec": float(cue.get("end_sec") or 0.0),
                "duration_sec": cue_dur,
                "asset_relpath": rel,
                "query": normalize_query(used_query or ""),
                "meta": meta,
            }
        )
        selected_mids.append(cue_mid)

    # Persist updates
    data["cues"] = cues
    cues_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_path = run_dir / "broll_manifest.json"
    manifest = {
        "provider": provider_in,
        "provider_dir": provider_dir,
        "ratio": ratio_f,
        "target_count": target,
        "injected_count": len(injected),
        "min_gap_sec": float(min_gap_sec),
        "min_cover_ratio": float(min_cover_ratio),
        "prefer_ar": prefer_ar,
        "min_w": int(min_w),
        "min_h": int(min_h),
        "injected": injected,
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return BrollInjectionSummary(
        provider=provider_in,
        target_count=target,
        injected_count=len(injected),
        manifest_path=str(manifest_path),
        cues_path=str(cues_path),
    )
