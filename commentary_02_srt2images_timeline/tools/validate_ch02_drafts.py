#!/usr/bin/env python3
"""
CH02 CapCut draft integrity validator.

Purpose (user requirement):
- Ensure CH02 drafts are built from CH02-テンプレ without breaking the main belt design.
- Ensure voiceover audio is actually inserted (no silent failure).
- Ensure subtitles use CapCut default black-background style.

This script is intentionally strict: it returns non-zero on any failure so pipelines can fail-fast.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


CAPCUT_DRAFT_ROOT = Path(
    os.getenv("CAPCUT_DRAFT_ROOT")
    or (Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft")
)


DEFAULT_VIDEOS = [
    "014",
    "019",
    "020",
    "021",
    "022",
    "023",
    "024",
    "025",
    "026",
    "027",
    "028",
    "029",
    "030",
    "031",
    "032",
    "033",
]


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_latest_draft_dirs(draft_root: Path, channel: str, videos: List[str]) -> Tuple[List[Path], List[str]]:
    found: List[Path] = []
    missing: List[str] = []
    for video in videos:
        pat = re.compile(rf"^{re.escape(channel)}-{re.escape(video)}_regen_\d{{8}}_\d{{6}}_draft$")
        matches = [p for p in draft_root.iterdir() if p.is_dir() and pat.match(p.name)]
        matches.sort(key=lambda p: p.name)
        if not matches:
            missing.append(video)
            continue
        found.append(matches[-1])
    return found, missing


def _find_track(tracks: List[Dict[str, Any]], *, tid: str, tname: str) -> Optional[Dict[str, Any]]:
    for t in tracks:
        if isinstance(t, dict) and t.get("id") == tid:
            return t
    for t in tracks:
        if isinstance(t, dict) and t.get("name") == tname:
            return t
    return None


def _parse_text_content(content: Any) -> Tuple[str, Optional[Dict[str, Any]]]:
    """
    Returns (text, parsed_json_if_any)
    """
    if isinstance(content, dict):
        text = content.get("text") if isinstance(content.get("text"), str) else ""
        return text, content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except Exception:
            return content, None
        if isinstance(parsed, dict):
            text = parsed.get("text") if isinstance(parsed.get("text"), str) else ""
            return text, parsed
        return content, None
    return "", None


def _validate_belt(draft_info: Dict[str, Any], template_info: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    t_tracks = template_info.get("tracks") or []
    d_tracks = draft_info.get("tracks") or []
    if not isinstance(t_tracks, list) or not isinstance(d_tracks, list):
        return ["belt: tracks missing or invalid"]

    t_belt = _find_track(t_tracks, tid="belt_main_track", tname="belt_main")
    d_belt = _find_track(d_tracks, tid="belt_main_track", tname="belt_main")
    if not t_belt:
        return ["belt: template missing belt_main_track/belt_main"]
    if not d_belt:
        return ["belt: draft missing belt_main_track/belt_main"]

    t_seg0 = (t_belt.get("segments") or [None])[0]
    d_seg0 = (d_belt.get("segments") or [None])[0]
    if not isinstance(t_seg0, dict) or not isinstance(d_seg0, dict):
        return ["belt: belt segments missing"]

    t_refs = list(t_seg0.get("extra_material_refs") or [])
    d_refs = list(d_seg0.get("extra_material_refs") or [])
    if not t_refs:
        errors.append("belt: template extra_material_refs empty (unexpected)")
    if not d_refs:
        errors.append("belt: draft extra_material_refs empty (style reset)")
    if t_refs and d_refs and t_refs != d_refs:
        errors.append(f"belt: extra_material_refs mismatch (template={t_refs}, draft={d_refs})")

    # effects referenced by extra_material_refs must exist
    mats = draft_info.get("materials") or {}
    effs = mats.get("effects") if isinstance(mats, dict) else None
    eff_ids = {e.get("id") for e in (effs or []) if isinstance(e, dict) and e.get("id")}
    missing = [rid for rid in d_refs if rid not in eff_ids]
    if missing:
        errors.append(f"belt: missing effects for refs: {missing}")

    # clip must exist and have transform/scale
    clip = d_seg0.get("clip") or {}
    if not isinstance(clip, dict):
        errors.append("belt: segment.clip missing")
    else:
        tr = clip.get("transform")
        sc = clip.get("scale")
        if not isinstance(tr, dict) or "x" not in tr or "y" not in tr:
            errors.append("belt: clip.transform missing")
        if not isinstance(sc, dict) or "x" not in sc or "y" not in sc:
            errors.append("belt: clip.scale missing")

    # belt text must be non-empty
    texts = (draft_info.get("materials") or {}).get("texts") if isinstance(draft_info.get("materials"), dict) else None
    if isinstance(texts, list):
        belt_text = next((t for t in texts if isinstance(t, dict) and t.get("id") == "belt_main_text"), None)
        if not belt_text:
            errors.append("belt: missing materials.texts id=belt_main_text")
        else:
            tval, _ = _parse_text_content(belt_text.get("content"))
            if not tval.strip() and isinstance(belt_text.get("base_content"), str):
                tval = belt_text.get("base_content") or ""
            if not tval.strip():
                errors.append("belt: belt_main_text content empty")
    else:
        errors.append("belt: materials.texts missing")

    return errors


def _validate_subtitles(draft_json: Dict[str, Any], *, label: str) -> List[str]:
    errors: List[str] = []
    tracks = draft_json.get("tracks") or []
    if not isinstance(tracks, list):
        return [f"subtitles({label}): tracks missing"]
    sub = next((t for t in tracks if isinstance(t, dict) and t.get("name") == "subtitles_text"), None)
    if not sub:
        return [f"subtitles({label}): subtitles_text track missing"]
    segs = sub.get("segments") or []
    if not isinstance(segs, list) or not segs:
        return [f"subtitles({label}): subtitles_text track has no segments"]

    mid = None
    for s in segs:
        if isinstance(s, dict) and s.get("material_id"):
            mid = s.get("material_id")
            break
    if not mid:
        errors.append(f"subtitles({label}): no material_id found in segments")
        return errors

    mats = draft_json.get("materials") or {}
    texts = mats.get("texts") if isinstance(mats, dict) else None
    if not isinstance(texts, list):
        return [f"subtitles({label}): materials.texts missing"]
    mat = next((m for m in texts if isinstance(m, dict) and m.get("id") == mid), None)
    if not mat:
        return [f"subtitles({label}): subtitle text material missing (id={mid})"]

    # CapCut default black background subtitle checks
    if mat.get("background_style") != 1:
        errors.append(f"subtitles({label}): background_style != 1 (got {mat.get('background_style')})")
    if (mat.get("background_color") or "").upper() != "#000000":
        errors.append(f"subtitles({label}): background_color != #000000 (got {mat.get('background_color')})")
    try:
        bg_alpha = float(mat.get("background_alpha"))
        if abs(bg_alpha - 1.0) > 1e-6:
            errors.append(f"subtitles({label}): background_alpha != 1.0 (got {bg_alpha})")
    except Exception:
        errors.append(f"subtitles({label}): background_alpha invalid (got {mat.get('background_alpha')})")
    try:
        ls = float(mat.get("line_spacing"))
        if abs(ls - 0.12) > 1e-6:
            errors.append(f"subtitles({label}): line_spacing != 0.12 (got {ls})")
    except Exception:
        errors.append(f"subtitles({label}): line_spacing invalid (got {mat.get('line_spacing')})")

    text_val, parsed = _parse_text_content(mat.get("content"))
    if not text_val:
        errors.append(f"subtitles({label}): content text empty")
    if not isinstance(mat.get("content"), str):
        errors.append(f"subtitles({label}): content is not JSON string (type={type(mat.get('content'))})")
    if not isinstance(parsed, dict) or "styles" not in parsed:
        errors.append(f"subtitles({label}): content JSON missing styles[]")
    return errors


def _validate_voiceover(draft_content: Dict[str, Any], draft_dir: Path) -> List[str]:
    errors: List[str] = []
    tracks = draft_content.get("tracks") or []
    if not isinstance(tracks, list):
        return ["voiceover: tracks missing"]
    voice = next((t for t in tracks if isinstance(t, dict) and t.get("name") == "voiceover"), None)
    if not voice:
        return ["voiceover: voiceover track missing"]
    segs = voice.get("segments") or []
    if not isinstance(segs, list) or not segs:
        return ["voiceover: voiceover track has no segments (audio not inserted)"]

    mats = draft_content.get("materials") or {}
    audios = mats.get("audios") if isinstance(mats, dict) else None
    if not isinstance(audios, list):
        return ["voiceover: materials.audios missing"]
    audio_ids = {a.get("id") for a in audios if isinstance(a, dict) and a.get("id")}

    seg0 = next((s for s in segs if isinstance(s, dict)), None)
    if not seg0:
        return ["voiceover: no valid segment dict"]
    mid = seg0.get("material_id")
    if not mid:
        errors.append("voiceover: segment.material_id missing")
    elif mid not in audio_ids:
        errors.append(f"voiceover: segment.material_id not found in materials.audios (id={mid})")

    # Ensure at least one audio file exists in draft materials/audio
    audio_dir = draft_dir / "materials" / "audio"
    if not audio_dir.exists():
        errors.append("voiceover: draft materials/audio directory missing")
    else:
        wavs = list(audio_dir.glob("*.wav"))
        if not wavs:
            errors.append("voiceover: no .wav found under draft materials/audio")
    return errors


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft-root", type=Path, default=CAPCUT_DRAFT_ROOT)
    ap.add_argument("--template", default="CH02-テンプレ")
    ap.add_argument("--channel", default="CH02")
    ap.add_argument("--videos", default=",".join(DEFAULT_VIDEOS))
    ap.add_argument("--all-matching", action="store_true", help="Validate all matching drafts per video (not only latest)")
    args = ap.parse_args()

    draft_root = args.draft_root
    channel = args.channel
    videos = [v.strip() for v in args.videos.split(",") if v.strip()]
    if not videos:
        raise SystemExit("videos empty")
    if not draft_root.exists():
        raise SystemExit(f"draft_root not found: {draft_root}")

    template_dir = draft_root / args.template
    template_info_path = template_dir / "draft_info.json"
    if not template_info_path.exists():
        raise SystemExit(f"template draft_info.json not found: {template_info_path}")
    template_info = _load_json(template_info_path)

    if args.all_matching:
        targets: List[Path] = []
        for video in videos:
            pat = re.compile(rf"^{re.escape(channel)}-{re.escape(video)}_regen_\d{{8}}_\d{{6}}_draft$")
            matches = [p for p in draft_root.iterdir() if p.is_dir() and pat.match(p.name)]
            matches.sort(key=lambda p: p.name)
            targets.extend(matches)
        missing = []
    else:
        targets, missing = _find_latest_draft_dirs(draft_root, channel, videos)

    if missing:
        print(f"⚠️ Missing drafts for videos: {', '.join(missing)}")

    any_fail = False
    for d in targets:
        info_path = d / "draft_info.json"
        content_path = d / "draft_content.json"
        if not info_path.exists() or not content_path.exists():
            print(f"❌ {d.name}: draft_info.json or draft_content.json missing")
            any_fail = True
            continue
        info = _load_json(info_path)
        content = _load_json(content_path)

        errs: List[str] = []
        errs.extend(_validate_belt(info, template_info))
        errs.extend(_validate_subtitles(info, label="info"))
        errs.extend(_validate_subtitles(content, label="content"))
        errs.extend(_validate_voiceover(content, d))

        if errs:
            any_fail = True
            print(f"\n❌ {d.name}")
            for e in errs:
                print(f"  - {e}")
        else:
            print(f"✅ {d.name}")

    if any_fail:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
