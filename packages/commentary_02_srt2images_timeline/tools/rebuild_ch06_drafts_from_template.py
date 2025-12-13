#!/usr/bin/env python3
"""
Rebuild CH06 drafts by **directly copying** the CapCut template folder `CH06-テンプレ`
and then patching JSON in-place while preserving the template layer structure.

Important:
  - CapCut uses `draft_info.json` as the primary source for the track structure.
  - The current `CH06-テンプレ` contains extra tracks only in `draft_content.json`
    (subtitles, duplicate srt2images tracks, etc.) which must NOT leak into new drafts.

This script:
  1) Copies `CH06-テンプレ` for each target video.
  2) Patches `draft_info.json` (source of truth) to:
     - Replace the image track segments with `image_cues.json` timing.
     - Ensure the dreamy confetti effect, logo overlay, and main belt text span
       the full duration.
     - Set the main belt text to the CSV title.
     - Loop BGM segments to cover the duration.
     - Add image materials + required per-segment “extra materials” when cues exceed
       the template’s 30-image baseline.
  3) Overwrites `draft_content.json` tracks/materials/duration to match `draft_info.json`
     to prevent future “layer chaos”.

Usage:
  python tools/rebuild_ch06_drafts_from_template.py \
    --draft-root "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft" \
    --template "CH06-テンプレ" \
    --runs-root commentary_02_srt2images_timeline/output \
    --channel-csv progress/channels/CH06.csv \
    --videos 2-30
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

SEC = 1_000_000  # CapCut microseconds


def _uuid_hex() -> str:
    return uuid.uuid4().hex


def _uuid_upper() -> str:
    return str(uuid.uuid4()).upper()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Dict[str, Any]) -> None:
    # CapCut files are usually compact JSON; keep output compact to match ecosystem.
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def sanitize_filename(name: str) -> str:
    # keep JP chars but strip filesystem specials
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def parse_srt(srt_path: Path) -> List[Dict[str, Any]]:
    """
    Very small SRT parser.
    Returns list of {start_us,end_us,text}.
    """
    if not srt_path.exists():
        return []

    def _to_us(ts: str) -> int:
        h, m, rest = ts.split(":")
        s, ms = rest.split(",")
        total_ms = (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)
        return total_ms * 1000

    entries: List[Dict[str, Any]] = []
    block: List[str] = []
    for line in srt_path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "":
            if block:
                entries.append(block)
                block = []
            continue
        block.append(line.rstrip("\n"))
    if block:
        entries.append(block)

    out: List[Dict[str, Any]] = []
    for b in entries:
        if len(b) < 2:
            continue
        time_line = next((x for x in b if "-->" in x), None)
        if not time_line:
            continue
        m = re.match(r"\s*(\d\d:\d\d:\d\d,\d\d\d)\s*-->\s*(\d\d:\d\d:\d\d,\d\d\d)", time_line)
        if not m:
            continue
        start_us = _to_us(m.group(1))
        end_us = _to_us(m.group(2))
        text_lines = [x for x in b if x != time_line and not x.strip().isdigit()]
        text = "\n".join(text_lines).strip()
        if not text:
            continue
        out.append({"start_us": start_us, "end_us": end_us, "text": text})
    return out


def load_title_map(csv_path: Path) -> Dict[int, str]:
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    out: Dict[int, str] = {}
    for r in rows:
        try:
            vid = int(r.get("動画番号") or r.get("video") or r.get("No.") or 0)
        except Exception:
            continue
        title = (r.get("タイトル") or "").strip()
        if vid and title:
            out[vid] = title
    return out


def _capcut_us_from_frame(frame: int, fps: float) -> int:
    # CapCut timelines are frame-aligned; template uses floor(frame * 1e6 / fps).
    return int(frame * SEC / fps)


def _build_id_index(materials: Dict[str, Any]) -> Dict[str, Tuple[str, Dict[str, Any]]]:
    """
    Return id -> (category, object) for all list-typed material categories.
    """
    out: Dict[str, Tuple[str, Dict[str, Any]]] = {}
    for cat, items in (materials or {}).items():
        if not isinstance(items, list):
            continue
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("id"), str):
                out[it["id"]] = (cat, it)
    return out


def _reid_common_keyframes(seg: Dict[str, Any]) -> None:
    """
    Avoid duplicated IDs when cloning segments beyond template baseline.
    """
    for ck in seg.get("common_keyframes") or []:
        if isinstance(ck, dict) and isinstance(ck.get("id"), str):
            ck["id"] = _uuid_hex()
        for kf in ck.get("keyframe_list") or []:
            if isinstance(kf, dict) and isinstance(kf.get("id"), str):
                kf["id"] = _uuid_hex()


def _scale_keyframe_time_offsets(seg: Dict[str, Any], old_dur_us: int, new_dur_us: int) -> None:
    if old_dur_us <= 0 or new_dur_us <= 0:
        return
    if not seg.get("common_keyframes"):
        # Still keep render/source timers in sync.
        seg["render_timerange"] = {"start": 0, "duration": 0}
        return
    ratio = new_dur_us / old_dur_us
    max_off = 0
    for ck in seg.get("common_keyframes") or []:
        for kf in ck.get("keyframe_list") or []:
            off = int(kf.get("time_offset") or 0)
            new_off = int(off * ratio)
            if new_off < 0:
                new_off = 0
            if new_off > new_dur_us:
                new_off = new_dur_us
            kf["time_offset"] = new_off
            if new_off > max_off:
                max_off = new_off
    seg["render_timerange"] = {"start": 0, "duration": max_off}


def _find_ch06_template_tracks(info: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """
    CH06-テンプレ has exactly these tracks in draft_info.json:
      - video (many segments) : images timeline
      - effect (1)            : dreamy confetti
      - video (1)             : logo overlay
      - text  (1)             : main belt text
      - audio (many)          : BGM loop segments
    """
    tracks: List[Dict[str, Any]] = info.get("tracks") or []
    video_tracks = [t for t in tracks if t.get("type") == "video"]
    image_track = next((t for t in video_tracks if len(t.get("segments") or []) > 1), None)
    logo_track = next((t for t in video_tracks if len(t.get("segments") or []) == 1), None)
    effect_track = next((t for t in tracks if t.get("type") == "effect"), None)
    text_track = next((t for t in tracks if t.get("type") == "text"), None)
    audio_track = next((t for t in tracks if t.get("type") == "audio"), None)

    missing = [name for name, t in [("image_track", image_track), ("logo_track", logo_track), ("effect_track", effect_track), ("text_track", text_track), ("audio_track", audio_track)] if t is None]
    if missing:
        raise RuntimeError(f"Template draft_info.json missing tracks: {', '.join(missing)}")

    return image_track, effect_track, logo_track, text_track, audio_track


def _ensure_assets_images(draft_dir: Path, images_dir: Path, count: int) -> None:
    assets_img_dir = draft_dir / "assets" / "image"
    if assets_img_dir.exists():
        # Clean aggressively to avoid template leftovers like 0004_2.png etc.
        for p in assets_img_dir.glob("*.png"):
            p.unlink(missing_ok=True)
    assets_img_dir.mkdir(parents=True, exist_ok=True)

    for i in range(1, count + 1):
        src = images_dir / f"{i:04d}.png"
        if not src.exists():
            raise RuntimeError(f"Missing image: {src}")
        shutil.copy2(src, assets_img_dir / src.name)


def _append_material(materials: Dict[str, Any], category: str, proto: Dict[str, Any], new_id: str) -> str:
    items = materials.setdefault(category, [])
    if not isinstance(items, list):
        raise RuntimeError(f"materials.{category} is not a list")
    obj = deepcopy(proto)
    obj["id"] = new_id
    items.append(obj)
    return new_id


def _clone_video_material_for_image(v_proto: Dict[str, Any], placeholder_prefix: str, filename: str) -> Dict[str, Any]:
    mat = deepcopy(v_proto)
    new_id = _uuid_hex()
    mat["id"] = new_id
    mat["material_id"] = new_id
    mat["material_name"] = filename
    mat["path"] = f"{placeholder_prefix}/assets/image/{filename}"
    return mat


def _patch_ch06_draft_info(
    info: Dict[str, Any],
    cues_payload: Dict[str, Any],
    title: str,
) -> int:
    fps = float(cues_payload.get("fps") or 30.0)
    cues: List[Dict[str, Any]] = cues_payload.get("cues") or []
    if not cues:
        raise RuntimeError("image_cues.json has no cues")
    n_images = len(cues)

    image_track, effect_track, logo_track, text_track, audio_track = _find_ch06_template_tracks(info)
    mats: Dict[str, Any] = info.setdefault("materials", {})
    id_index = _build_id_index(mats)

    # Determine placeholder prefix used by existing image materials.
    placeholder_prefix = ""
    for v in mats.get("videos") or []:
        if isinstance(v, dict) and isinstance(v.get("material_name"), str) and v["material_name"].endswith(".png"):
            path = v.get("path") or ""
            if isinstance(path, str) and path.startswith("##_draftpath_placeholder_") and "/assets/image/" in path:
                placeholder_prefix = path.split("/assets/image/")[0]
                break
    if not placeholder_prefix:
        raise RuntimeError("Failed to detect draftpath placeholder prefix in template materials.videos")

    # Compute duration in microseconds from last cue end_frame.
    last_end_frame = int(cues[-1].get("end_frame") or 0)
    duration_us = _capcut_us_from_frame(last_end_frame, fps)
    info["duration"] = duration_us

    # Patch effect/logo/text to span full duration.
    def _span_full(track: Dict[str, Any]) -> None:
        if not (track.get("segments") or []):
            return
        seg = track["segments"][0]
        seg["target_timerange"] = {"start": 0, "duration": duration_us}
        if isinstance(seg.get("source_timerange"), dict):
            seg["source_timerange"] = {"start": 0, "duration": duration_us}

    _span_full(effect_track)
    _span_full(logo_track)
    _span_full(text_track)

    # Update main belt text material content.
    if (text_track.get("segments") or []) and isinstance(mats.get("texts"), list):
        mid = text_track["segments"][0].get("material_id")
        if isinstance(mid, str):
            for t in mats["texts"]:
                if isinstance(t, dict) and t.get("id") == mid:
                    try:
                        content_obj = json.loads(t.get("content") or "{}")
                        if isinstance(content_obj, dict):
                            content_obj["text"] = title
                            t["content"] = json.dumps(content_obj, ensure_ascii=False, separators=(",", ":"))
                    except Exception:
                        t["content"] = json.dumps({"text": title}, ensure_ascii=False, separators=(",", ":"))
                    t["base_content"] = title
                    break

    # --- Images track ---
    img_segs_tpl: List[Dict[str, Any]] = image_track.get("segments") or []
    if not img_segs_tpl:
        raise RuntimeError("Template image track has no segments")

    # Video material prototype for photo images: use segment0 material_id.
    first_seg_mid = img_segs_tpl[0].get("material_id")
    if not isinstance(first_seg_mid, str):
        raise RuntimeError("Template image segment has no material_id")
    v_cat, v_proto = id_index.get(first_seg_mid, (None, None))
    if v_cat != "videos" or not isinstance(v_proto, dict):
        raise RuntimeError("Template image segment material_id does not resolve to materials.videos")

    # Extra material prototypes for image segments (speed, placeholder, canvas, sound mapping, material_color, loudness, vocal_sep)
    img_extra_ids: List[str] = img_segs_tpl[0].get("extra_material_refs") or []
    if len(img_extra_ids) < 7:
        raise RuntimeError("Template image segment missing extra_material_refs (expected >=7)")
    img_extra_protos: Dict[str, Dict[str, Any]] = {}
    for mid in img_extra_ids:
        if not isinstance(mid, str) or mid not in id_index:
            continue
        cat, obj = id_index[mid]
        if cat in {"speeds", "placeholder_infos", "canvases", "sound_channel_mappings", "material_colors", "loudnesses", "vocal_separations"}:
            img_extra_protos[cat] = obj
    missing_cats = {"speeds", "placeholder_infos", "canvases", "sound_channel_mappings", "material_colors", "loudnesses", "vocal_separations"} - set(img_extra_protos.keys())
    if missing_cats:
        raise RuntimeError(f"Template image segment extra refs missing categories: {sorted(missing_cats)}")

    # Map existing image materials by filename (0001.png..0030.png).
    existing_img_mat_by_name: Dict[str, str] = {}
    for m in mats.get("videos") or []:
        if not isinstance(m, dict):
            continue
        name = m.get("material_name")
        mid = m.get("id")
        if isinstance(name, str) and isinstance(mid, str) and re.fullmatch(r"\d{4}\.png", name):
            existing_img_mat_by_name[name] = mid

    # Build new segments.
    new_img_segments: List[Dict[str, Any]] = []
    for i, cue in enumerate(cues, start=1):
        filename = f"{i:04d}.png"
        start_frame = int(cue.get("start_frame") or 0)
        end_frame = int(cue.get("end_frame") or start_frame)
        start_us = _capcut_us_from_frame(start_frame, fps)
        end_us = _capcut_us_from_frame(end_frame, fps)
        if end_us < start_us:
            end_us = start_us
        new_dur = max(1, end_us - start_us)

        if i - 1 < len(img_segs_tpl):
            seg = deepcopy(img_segs_tpl[i - 1])
            old_dur = int((seg.get("target_timerange") or {}).get("duration") or new_dur)
        else:
            # Clone from last template segment and re-id nested keyframes to avoid collisions.
            seg = deepcopy(img_segs_tpl[-1])
            old_dur = int((seg.get("target_timerange") or {}).get("duration") or new_dur)
            seg["id"] = _uuid_hex()
            _reid_common_keyframes(seg)

            # Create new per-segment extra materials and wire them.
            speed_id = _append_material(mats, "speeds", img_extra_protos["speeds"], _uuid_hex())
            placeholder_id = _append_material(mats, "placeholder_infos", img_extra_protos["placeholder_infos"], _uuid_upper())
            canvas_id = _append_material(mats, "canvases", img_extra_protos["canvases"], _uuid_upper())
            sound_id = _append_material(mats, "sound_channel_mappings", img_extra_protos["sound_channel_mappings"], _uuid_upper())
            color_id = _append_material(mats, "material_colors", img_extra_protos["material_colors"], _uuid_upper())
            loud_id = _append_material(mats, "loudnesses", img_extra_protos["loudnesses"], _uuid_upper())
            vocal_id = _append_material(mats, "vocal_separations", img_extra_protos["vocal_separations"], _uuid_upper())
            seg["extra_material_refs"] = [speed_id, placeholder_id, canvas_id, sound_id, color_id, loud_id, vocal_id]

        # Ensure material exists (create if beyond template baseline).
        mat_id = existing_img_mat_by_name.get(filename)
        if not mat_id:
            # Append new image video material.
            new_mat = _clone_video_material_for_image(v_proto, placeholder_prefix, filename)
            mats.setdefault("videos", []).append(new_mat)
            mat_id = new_mat["id"]
            existing_img_mat_by_name[filename] = mat_id
        seg["material_id"] = mat_id

        seg["target_timerange"] = {"start": start_us, "duration": new_dur}
        seg["source_timerange"] = {"start": 0, "duration": new_dur}
        _scale_keyframe_time_offsets(seg, old_dur, new_dur)
        new_img_segments.append(seg)

    image_track["segments"] = new_img_segments

    # --- Audio (BGM) track ---
    audio_segs_tpl: List[Dict[str, Any]] = audio_track.get("segments") or []
    if not audio_segs_tpl:
        raise RuntimeError("Template audio track has no segments")
    loop_us = int((audio_segs_tpl[0].get("target_timerange") or {}).get("duration") or 0)
    if loop_us <= 0:
        raise RuntimeError("Template audio loop duration invalid")
    need = max(1, (duration_us + loop_us - 1) // loop_us)

    # Prototypes for new audio segments/materials (only used when need > template segments)
    audio_mid0 = audio_segs_tpl[0].get("material_id")
    if not isinstance(audio_mid0, str):
        raise RuntimeError("Template audio segment has no material_id")
    a_cat, a_proto = id_index.get(audio_mid0, (None, None))
    if a_cat != "audios" or not isinstance(a_proto, dict):
        raise RuntimeError("Template audio segment material_id does not resolve to materials.audios")
    a_extra_ids = audio_segs_tpl[0].get("extra_material_refs") or []
    a_extra_protos: Dict[str, Dict[str, Any]] = {}
    for mid in a_extra_ids:
        if not isinstance(mid, str) or mid not in id_index:
            continue
        cat, obj = id_index[mid]
        if cat in {"speeds", "placeholder_infos", "beats", "sound_channel_mappings", "vocal_separations"}:
            a_extra_protos[cat] = obj
    missing_a = {"speeds", "placeholder_infos", "beats", "sound_channel_mappings", "vocal_separations"} - set(a_extra_protos.keys())
    if missing_a:
        raise RuntimeError(f"Template audio segment extra refs missing categories: {sorted(missing_a)}")

    new_audio_segments: List[Dict[str, Any]] = []
    for j in range(int(need)):
        start_us = j * loop_us
        if start_us >= duration_us:
            break
        seg_dur = min(loop_us, duration_us - start_us)
        if j < len(audio_segs_tpl):
            seg = deepcopy(audio_segs_tpl[j])
        else:
            seg = deepcopy(audio_segs_tpl[-1])
            seg["id"] = _uuid_hex()
            # New audio material + beat + extra refs
            new_audio_id = _uuid_upper()
            new_audio = deepcopy(a_proto)
            new_audio["id"] = new_audio_id
            mats.setdefault("audios", []).append(new_audio)
            beat_id = _append_material(mats, "beats", a_extra_protos["beats"], _uuid_upper())
            speed_id = _append_material(mats, "speeds", a_extra_protos["speeds"], _uuid_hex())
            placeholder_id = _append_material(mats, "placeholder_infos", a_extra_protos["placeholder_infos"], _uuid_upper())
            sound_id = _append_material(mats, "sound_channel_mappings", a_extra_protos["sound_channel_mappings"], _uuid_upper())
            vocal_id = _append_material(mats, "vocal_separations", a_extra_protos["vocal_separations"], _uuid_upper())
            seg["material_id"] = new_audio_id
            seg["extra_material_refs"] = [speed_id, placeholder_id, beat_id, sound_id, vocal_id]

        seg["target_timerange"] = {"start": int(start_us), "duration": int(seg_dur)}
        seg["source_timerange"] = {"start": 0, "duration": int(seg_dur)}
        # audio render_timerange stays as in template (usually 0)
        new_audio_segments.append(seg)

    audio_track["segments"] = new_audio_segments

    return duration_us


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft-root", required=True)
    ap.add_argument("--template", required=True, help="Template folder name under draft-root (must be CH06-テンプレ)")
    ap.add_argument("--runs-root", required=True, help="commentary output root containing CH06-XXX_capcut_v1 dirs")
    ap.add_argument("--channel-csv", required=True, help="progress/channels/CH06.csv")
    ap.add_argument("--videos", default="2-30", help="Range like 2-30 or comma list")
    args = ap.parse_args()

    draft_root = Path(args.draft_root).expanduser()
    tpl_dir = draft_root / args.template
    if not tpl_dir.exists():
        raise SystemExit(f"Template not found: {tpl_dir}")

    runs_root = Path(args.runs_root)
    title_map = load_title_map(Path(args.channel_csv))

    vids: List[int] = []
    if "-" in args.videos:
        a, b = args.videos.split("-", 1)
        vids = list(range(int(a), int(b) + 1))
    else:
        vids = [int(x) for x in args.videos.split(",") if x.strip()]

    for vid in vids:
        run_dir = runs_root / f"CH06-{vid:03d}_capcut_v1"
        cues_path = run_dir / "image_cues.json"
        images_dir = run_dir / "images"
        if not cues_path.exists() or not images_dir.exists():
            print(f"[SKIP] CH06-{vid:03d}: run assets missing")
            continue

        cues_payload = load_json(cues_path)
        cues = cues_payload.get("cues") or []
        title = title_map.get(vid, f"CH06-{vid:03d}")

        new_name = sanitize_filename(f"★CH06-{vid:03d}-{title}")
        draft_dir = draft_root / new_name
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        shutil.copytree(tpl_dir, draft_dir)

        # Ensure assets/images match cues exactly (avoid template leftovers).
        _ensure_assets_images(draft_dir, images_dir, len(cues))

        # Patch draft_info.json (source of truth)
        info_path = draft_dir / "draft_info.json"
        info_data = load_json(info_path)
        duration_us = _patch_ch06_draft_info(info_data, cues_payload, title)
        save_json(info_path, info_data)

        # Overwrite draft_content.json tracks/materials/duration to match draft_info.json
        content_path = draft_dir / "draft_content.json"
        if content_path.exists():
            content_data = load_json(content_path)
            content_data["tracks"] = info_data.get("tracks", [])
            content_data["materials"] = info_data.get("materials", {})
            content_data["duration"] = info_data.get("duration", duration_us)
            save_json(content_path, content_data)

        # Patch draft_meta_info.json (so CapCut registers this as a distinct project)
        meta_path = draft_dir / "draft_meta_info.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            meta["draft_fold_path"] = str(draft_dir)
            meta["draft_root_path"] = str(draft_root)
            meta["draft_name"] = new_name
            meta["draft_id"] = _uuid_upper()
            meta["tm_duration"] = int(duration_us)
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

        # Update run_dir symlink for convenience (optional, but helps future automation).
        link_path = run_dir / "capcut_draft"
        try:
            if link_path.is_symlink() or link_path.exists():
                link_path.unlink()
            link_path.symlink_to(draft_dir)
        except Exception:
            pass

        print(f"[OK] CH06-{vid:03d} -> {new_name}")


if __name__ == "__main__":
    main()
