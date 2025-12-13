#!/usr/bin/env python3
"""
Patch an existing CapCut draft (already built from a template) by inserting:
  - voiceover audio (WAV) from timeline_manifest.json
  - subtitles_text track from timeline_manifest.json SRT

Design goals:
  - Use the run_dir's timeline_manifest.json as SoT (audio/srt/cues).
  - Idempotent: re-running replaces existing voiceover/subtitles_text content.
  - Keep draft_info.json and draft_content.json consistent (CapCut reads draft_info.json).

Usage:
  python3 tools/patch_draft_audio_subtitles_from_manifest.py \\
    --run commentary_02_srt2images_timeline/output/CH06-002_capcut_v1
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import wave
import warnings
from pathlib import Path
from typing import Any

# Paths for local imports (executed from commentary package CWD often)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from factory_common.timeline_manifest import MANIFEST_FILENAME, validate_timeline_manifest

# Reuse proven helpers from capcut_bulk_insert (add tools/ to import path)
TOOLS_DIR = PROJECT_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
from capcut_bulk_insert import (
    parse_srt_file,
    sync_draft_info_with_content,
    _apply_common_subtitle_style,
    _compute_audio_voice_index_below_bgm,
)

import pyJianYingDraft as draft
from pyJianYingDraft import (
    Track_type,
    Timerange,
    Clip_settings,
    Text_style,
    Text_background,
    Text_border,
    Text_segment,
    Audio_material,
    Audio_segment,
)

SEC = 1_000_000


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_valid_draft_content_json(draft_dir: Path) -> None:
    """
    Ensure draft_content.json exists and is valid JSON.

    CapCut sometimes leaves draft_content.json empty (0 bytes) if the app/tool
    crashes mid-write. pyJianYingDraft requires draft_content.json to load.

    Strategy:
      - If draft_content.json is empty/invalid, reconstruct it using draft_info.json
        as the authoritative track/material snapshot, while preserving other
        keys from the newest available backup (draft_content.json.bak*).
    """
    info_path = draft_dir / "draft_info.json"
    content_path = draft_dir / "draft_content.json"
    if not info_path.exists() or not content_path.exists():
        return

    raw = ""
    try:
        raw = content_path.read_text(encoding="utf-8")
    except Exception:
        raw = ""

    def _is_valid_json(text: str) -> bool:
        try:
            json.loads(text)
            return True
        except Exception:
            return False

    if raw.strip() and _is_valid_json(raw):
        return

    info = _read_json(info_path)

    base: dict[str, Any] = {}
    backups = sorted(draft_dir.glob("draft_content.json.bak*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for cand in backups:
        try:
            cand_raw = cand.read_text(encoding="utf-8")
            if not cand_raw.strip() or not _is_valid_json(cand_raw):
                continue
            base = json.loads(cand_raw)
            break
        except Exception:
            continue

    # Preserve base keys where possible, but snap tracks/materials/duration to draft_info.json.
    base["tracks"] = info.get("tracks", [])
    base["materials"] = info.get("materials", {})
    base["duration"] = info.get("duration", base.get("duration", 0))
    content_path.write_text(json.dumps(base, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _resolve_manifest_paths(manifest: dict[str, Any]) -> tuple[Path, Path]:
    root = Path(manifest.get("repo_root") or REPO_ROOT).expanduser().resolve()
    wav_p = Path(manifest["source"]["audio_wav"]["path"])
    srt_p = Path(manifest["source"]["audio_srt"]["path"])
    if not wav_p.is_absolute():
        wav_p = (root / wav_p).resolve()
    if not srt_p.is_absolute():
        srt_p = (root / srt_p).resolve()
    return wav_p, srt_p


def _wav_duration_us(path: Path) -> int:
    with wave.open(str(path), "rb") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate() or 1
        # CapCut/pyJianYingDraft durations are effectively millisecond-aligned.
        dur_us = int(frames / rate * SEC)
        return max(0, (dur_us // 1000) * 1000)


def _load_script(draft_dir: Path):
    # Prefer ScriptFile.load_template so the instance has a save path.
    # draft_content.json is the authoritative editable entry for pyJianYingDraft.
    return draft.ScriptFile.load_template(str(draft_dir / "draft_content.json"))


def _ensure_unique_track_names(draft_dir: Path, channel: str) -> None:
    """
    pyJianYingDraft represents tracks by name internally. If a draft has multiple tracks
    with empty/default names (common in templates like CH06), loading+saving may drop tracks.
    We pre-normalize the template tracks to unique, stable names in BOTH draft_info/content.
    """
    _ensure_valid_draft_content_json(draft_dir)
    info_path = draft_dir / "draft_info.json"
    content_path = draft_dir / "draft_content.json"
    if not info_path.exists() or not content_path.exists():
        return

    info = _read_json(info_path)
    content = _read_json(content_path)
    info_tracks = list(info.get("tracks") or [])
    content_tracks = list(content.get("tracks") or [])
    if not isinstance(info_tracks, list) or not isinstance(content_tracks, list):
        return

    id_to_name: dict[str, str] = {}

    if (channel or "").upper() == "CH06":
        # CH06 template has 2 video tracks and 1 text track with empty names.
        # Assign meaningful stable names so downstream automation can safely patch.
        video_tracks = [t for t in info_tracks if t.get("type") == "video"]
        image_track = next((t for t in video_tracks if len(t.get("segments") or []) > 1), None)
        logo_track = next((t for t in video_tracks if len(t.get("segments") or []) == 1), None)
        effect_track = next((t for t in info_tracks if t.get("type") == "effect"), None)
        # Belt is the template text overlay: single segment, default-name, or already named main_belt.
        text_tracks = [t for t in info_tracks if t.get("type") == "text"]
        belt_track = next(
            (
                t
                for t in text_tracks
                if (t.get("name") or "") in ("", "main_belt")
                and len(t.get("segments") or []) == 1
            ),
            None,
        )
        # BGM is the template audio bed: many segments, default-name, or already named bgm.
        audio_tracks = [t for t in info_tracks if t.get("type") == "audio"]
        bgm_track = next(
            (
                t
                for t in audio_tracks
                if (t.get("name") or "") in ("", "bgm")
                and len(t.get("segments") or []) > 1
            ),
            None,
        )

        mapping = [
            (image_track, "images"),
            (effect_track, "dreamy_confetti"),
            (logo_track, "logo"),
            (belt_track, "main_belt"),
            (bgm_track, "bgm"),
        ]
        for tr, new_name in mapping:
            if not isinstance(tr, dict):
                continue
            # Only rename template tracks (avoid clobbering subtitles_text/voiceover on reruns)
            cur_name = tr.get("name") or ""
            if cur_name in ("", new_name):
                tr["name"] = new_name
                tr["is_default_name"] = False
            if isinstance(tr.get("id"), str):
                id_to_name[tr["id"]] = new_name
    else:
        # Generic: only rename when (type,name) duplicates exist.
        seen: dict[tuple[str, str], int] = {}
        for tr in info_tracks:
            key = (tr.get("type") or "", tr.get("name") or "")
            seen[key] = seen.get(key, 0) + 1
        dup_keys = {k for k, c in seen.items() if c >= 2 and k[1] == ""}
        if dup_keys:
            counts: dict[tuple[str, str], int] = {}
            for tr in info_tracks:
                key = (tr.get("type") or "", tr.get("name") or "")
                if key not in dup_keys:
                    continue
                counts[key] = counts.get(key, 0) + 1
                new_name = f"{key[0]}_{counts[key]}"
                tr["name"] = new_name
                tr["is_default_name"] = False
                if isinstance(tr.get("id"), str):
                    id_to_name[tr["id"]] = new_name

    if id_to_name:
        # Apply the same names into draft_content.json by track id to keep info/content aligned.
        for tr in content_tracks:
            tid = tr.get("id")
            if isinstance(tid, str) and tid in id_to_name:
                tr["name"] = id_to_name[tid]
                tr["is_default_name"] = False

        info["tracks"] = info_tracks
        content["tracks"] = content_tracks
        info_path.write_text(json.dumps(info, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        content_path.write_text(json.dumps(content, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _dedupe_named_tracks(draft_dir: Path) -> None:
    """
    Remove duplicates ONLY for tracks we own (voiceover/subtitles_text).
    Do not touch template tracks (many are default-name duplicates like video/"").
    """
    targets = {("audio", "voiceover"), ("text", "subtitles_text")}
    for fname in ("draft_content.json", "draft_info.json"):
        path = draft_dir / fname
        if not path.exists():
            continue
        data = _read_json(path)
        tracks = list(data.get("tracks") or [])
        if not isinstance(tracks, list) or not tracks:
            continue
        keep_idx: dict[tuple[str, str], int] = {}
        for idx, tr in enumerate(tracks):
            key = (tr.get("type"), tr.get("name") or "")
            if key in targets:
                keep_idx[key] = idx  # keep last occurrence
        if not keep_idx:
            continue
        new_tracks = []
        for idx, tr in enumerate(tracks):
            key = (tr.get("type"), tr.get("name") or "")
            if key in targets and keep_idx.get(key) != idx:
                continue
            new_tracks.append(tr)
        data["tracks"] = new_tracks
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _replace_voiceover(script: draft.Script_file, draft_dir: Path, wav_path: Path, opening_offset_sec: float) -> None:
    audio_dir = draft_dir / "materials" / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    voice_dest = audio_dir / wav_path.name
    shutil.copy2(wav_path, voice_dest)

    voice_track = "voiceover"
    voice_index = _compute_audio_voice_index_below_bgm(draft_dir, fallback=10)
    try:
        if voice_track not in getattr(script, "tracks", {}):
            script.add_track(Track_type.audio, voice_track, absolute_index=voice_index)
    except Exception:
        pass
    try:
        script.tracks[voice_track].segments = []
    except Exception:
        pass

    dur_us = _wav_duration_us(wav_path)
    start_us = int(opening_offset_sec * SEC)
    amat = Audio_material(path=str(voice_dest), material_name=voice_dest.name)
    try:
        script.add_material(amat)
    except Exception:
        pass
    aseg = Audio_segment(amat, target_timerange=Timerange(start_us, dur_us))
    script.add_segment(aseg, track_name=voice_track)


def _replace_subtitles(script: draft.Script_file, srt_path: Path, opening_offset_sec: float) -> None:
    subs = parse_srt_file(srt_path)
    sub_track_name = "subtitles_text"
    try:
        if sub_track_name not in getattr(script, "tracks", {}):
            script.add_track(Track_type.text, sub_track_name, absolute_index=2_000_000)
    except Exception:
        pass
    try:
        script.tracks[sub_track_name].segments = []
    except Exception:
        pass

    # Basic style (final look is normalized by _apply_common_subtitle_style)
    subtitle_style = Text_style(size=5.0, color=(1.0, 1.0, 1.0), alpha=1.0, align=1, line_spacing=0.02)
    subtitle_background = Text_background(color=(0, 0, 0), alpha=1.0, round_radius=0.4, style=1, height=0.28, width=0.28, horizontal_offset=-1.0, vertical_offset=-1.0)
    subtitle_border = Text_border(width=0.06, alpha=1.0, color=(0, 0, 0))
    subtitle_clip = Clip_settings(transform_x=0.0, transform_y=-0.8, scale_x=1.0, scale_y=1.0)

    offset_us = int(opening_offset_sec * SEC)
    added = 0
    for ent in subs:
        start_us = int(ent["start_us"]) + offset_us
        dur_us = max(SEC // 60, int(ent["end_us"] - ent["start_us"]))
        text_val = ent.get("text", "")
        seg = Text_segment(
            text_val,
            Timerange(start_us, dur_us),
            style=subtitle_style,
            background=subtitle_background,
            border=subtitle_border,
            clip_settings=subtitle_clip,
        )
        script.add_segment(seg, track_name=sub_track_name)
        added += 1
    if added == 0:
        raise RuntimeError("Parsed 0 subtitle entries from SRT")


def _reorder_tracks(draft_dir: Path) -> None:
    """
    Heuristic track ordering to match CapCut UI expectations:
      - Keep template order, but ensure:
        - voiceover audio track comes right after base audio track
        - subtitles_text comes before the first default-name text overlay (e.g., belt)
    Applied to both draft_content.json and draft_info.json.
    """
    for fname in ("draft_content.json", "draft_info.json"):
        path = draft_dir / fname
        if not path.exists():
            continue
        data = _read_json(path)
        tracks = list(data.get("tracks") or [])
        if not isinstance(tracks, list) or not tracks:
            continue

        def pop_first(pred):
            for i, t in enumerate(tracks):
                if pred(t):
                    return tracks.pop(i)
            return None

        voice = pop_first(lambda t: t.get("type") == "audio" and (t.get("name") or "") == "voiceover")
        subs = pop_first(lambda t: t.get("type") == "text" and (t.get("name") or "") == "subtitles_text")

        # Insert voice after the first base audio track (default name)
        if voice:
            idx_base_audio = next(
                (
                    i
                    for i, t in enumerate(tracks)
                    if t.get("type") == "audio" and (t.get("name") or "") in ("", "bgm") and (t.get("is_default_name") is True or (t.get("name") or "") == "bgm")
                ),
                None,
            )
            if idx_base_audio is None:
                idx_base_audio = next((i for i, t in enumerate(tracks) if t.get("type") == "audio"), None)
            insert_at = (idx_base_audio + 1) if isinstance(idx_base_audio, int) else len(tracks)
            tracks.insert(min(max(insert_at, 0), len(tracks)), voice)

        # Insert subtitles before the first default text overlay (belt/title)
        if subs:
            idx_default_text = next(
                (
                    i
                    for i, t in enumerate(tracks)
                    if t.get("type") == "text"
                    and (
                        t.get("is_default_name") is True
                        or (t.get("name") or "") in ("main_belt", "title_text")
                    )
                ),
                None,
            )
            insert_at = idx_default_text if isinstance(idx_default_text, int) else len(tracks)
            tracks.insert(min(max(insert_at, 0), len(tracks)), subs)

        data["tracks"] = tracks
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main() -> None:
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run_dir containing timeline_manifest.json and capcut_draft symlink")
    ap.add_argument("--draft", default="", help="Optional explicit draft dir (overrides run_dir/capcut_draft)")
    ap.add_argument("--opening-offset", type=float, default=None, help="Opening offset seconds (default: try run_dir/channel_preset.json else 0)")
    ap.add_argument("--no-style", action="store_true", help="Skip subtitle style normalization (debug)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.run).expanduser().resolve()
    mf_path = run_dir / MANIFEST_FILENAME
    if not mf_path.exists():
        raise SystemExit(f"timeline manifest missing: {mf_path}")
    manifest = _read_json(mf_path)
    validate_timeline_manifest(manifest, run_dir=run_dir, tolerance_sec=1.0)
    wav_path, srt_path = _resolve_manifest_paths(manifest)

    if args.draft:
        draft_dir = Path(args.draft).expanduser().resolve()
    else:
        link = run_dir / "capcut_draft"
        if link.exists() and link.is_symlink():
            draft_dir = link.resolve()
        else:
            cap = (manifest.get("derived") or {}).get("capcut_draft") or {}
            draft_dir = Path(cap.get("path") or "").expanduser().resolve()
    if not draft_dir.exists():
        raise SystemExit(f"draft_dir not found: {draft_dir}")

    opening_offset = args.opening_offset
    if opening_offset is None:
        opening_offset = 0.0
        preset_path = run_dir / "channel_preset.json"
        if preset_path.exists():
            try:
                preset = _read_json(preset_path)
                belt = preset.get("belt") or {}
                if isinstance(belt, dict) and "opening_offset" in belt:
                    opening_offset = float(belt.get("opening_offset") or 0.0)
            except Exception:
                opening_offset = 0.0

    if args.dry_run:
        print("[DRY] would patch:", draft_dir)
        print("[DRY] wav:", wav_path)
        print("[DRY] srt:", srt_path)
        print("[DRY] opening_offset:", opening_offset)
        return

    _ensure_unique_track_names(draft_dir, channel=str(manifest.get("episode", {}).get("channel") or ""))
    _dedupe_named_tracks(draft_dir)
    script = _load_script(draft_dir)
    _replace_voiceover(script, draft_dir, wav_path, opening_offset_sec=float(opening_offset))
    _replace_subtitles(script, srt_path, opening_offset_sec=float(opening_offset))
    script.save()

    # Deduplicate only our injected tracks
    _dedupe_named_tracks(draft_dir)

    # CapCut reads draft_info.json → sync it from the (deduped) draft_content.json
    ok = sync_draft_info_with_content(draft_dir)
    if not ok:
        raise RuntimeError("sync_draft_info_with_content failed")

    # Reorder tracks for a stable visual stack (esp. CH06)
    _reorder_tracks(draft_dir)

    if not args.no_style:
        _apply_common_subtitle_style(draft_dir)

    print(f"✅ patched: {draft_dir.name}")


if __name__ == "__main__":
    main()
