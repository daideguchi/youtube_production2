#!/usr/bin/env python3
"""
Rebuild CH06 drafts by **directly copying** the CapCut template folder `CH06-テンプレ`
and then patching JSON in-place while preserving all template layers, including
duplicate track names (which pyJianYingDraft cannot represent).

This script:
  1) Copies `CH06-テンプレ` for each target video.
  2) Replaces BOTH `srt2images_*` video tracks with new image segments/materials
     using `image_cues.json` + run_dir images.
  3) Replaces BOTH `subtitles_text` tracks with new subtitle segments/materials
     using the run_dir SRT file while preserving style.
  4) Updates the main belt text track (single-seg non-subtitle text track)
     with the CSV title.

After running this, you can optionally run:
  tools/inject_ch06_template_layers.py --apply
to add dreamy confetti effect (template has none on disk).

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
from typing import Any, Dict, List, Tuple

SEC = 1_000_000  # CapCut microseconds


def _uuid_hex() -> str:
    return uuid.uuid4().hex


def _uuid_upper() -> str:
    return str(uuid.uuid4()).upper()


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def _find_tracks(data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any] | None]:
    tracks = data.get("tracks") or []
    srt2images_tracks = [
        t for t in tracks
        if t.get("type") == "video" and (t.get("name") or "").startswith("srt2images_")
    ]
    subtitle_tracks = [
        t for t in tracks
        if t.get("type") == "text" and (t.get("name") or "") == "subtitles_text"
    ]
    main_belt_track = next(
        (
            t for t in tracks
            if t.get("type") == "text"
            and (t.get("name") or "") != "subtitles_text"
            and len(t.get("segments") or []) == 1
        ),
        None,
    )
    return srt2images_tracks, subtitle_tracks, main_belt_track


def _replace_video_tracks(
    data: Dict[str, Any],
    srt2images_tracks: List[Dict[str, Any]],
    cues: List[Dict[str, Any]],
    images_dir: Path,
    draft_dir: Path,
) -> None:
    mats = data.setdefault("materials", {})
    videos: List[Dict[str, Any]] = mats.setdefault("videos", [])
    original_videos = deepcopy(videos)

    assets_img_dir = draft_dir / "assets" / "image"
    assets_img_dir.mkdir(parents=True, exist_ok=True)
    # remove old placeholder images only
    for f in assets_img_dir.glob("*.png"):
        try:
            f.unlink()
        except Exception:
            pass

    for track in srt2images_tracks:
        if not track.get("segments"):
            continue
        seg_tpl = deepcopy(track["segments"][0])
        tpl_mid = seg_tpl.get("material_id")
        mat_tpl = next((m for m in original_videos if m.get("id") == tpl_mid), None)
        if not mat_tpl:
            raise RuntimeError("Template video material not found for srt2images track")

        old_mids = {s.get("material_id") for s in track.get("segments") or []}
        videos[:] = [m for m in videos if m.get("id") not in old_mids]

        new_segments: List[Dict[str, Any]] = []
        for i, cue in enumerate(cues):
            src_img = images_dir / f"{i+1:04d}.png"
            if not src_img.exists():
                # allow missing tail images silently
                continue
            dest_img = assets_img_dir / src_img.name
            shutil.copy2(src_img, dest_img)

            mat = deepcopy(mat_tpl)
            mat_id = _uuid_upper()
            mat["id"] = mat_id
            mat["material_id"] = mat_id
            mat["local_material_id"] = _uuid_hex()
            mat["material_name"] = dest_img.name
            mat["path"] = str(dest_img)
            mat["media_path"] = str(dest_img)
            videos.append(mat)

            seg = deepcopy(seg_tpl)
            seg["id"] = _uuid_hex()
            seg["material_id"] = mat_id
            start_us = int(float(cue.get("start_sec", 0.0)) * SEC)
            end_us = int(float(cue.get("end_sec", 0.0)) * SEC)
            dur_us = max(SEC // 60, end_us - start_us)
            seg["target_timerange"] = {"start": start_us, "duration": dur_us}
            seg["source_timerange"] = {"start": 0, "duration": dur_us}
            seg["render_timerange"] = {"start": 0, "duration": dur_us}
            new_segments.append(seg)

        track["segments"] = new_segments


def _replace_subtitle_tracks(
    data: Dict[str, Any],
    subtitle_tracks: List[Dict[str, Any]],
    subs: List[Dict[str, Any]],
) -> None:
    mats = data.setdefault("materials", {})
    texts: List[Dict[str, Any]] = mats.setdefault("texts", [])

    for track in subtitle_tracks:
        if not track.get("segments"):
            continue
        seg_tpl = deepcopy(track["segments"][0])
        tpl_mid = seg_tpl.get("material_id")
        mat_tpl = next((m for m in texts if m.get("id") == tpl_mid), None)
        if not mat_tpl:
            raise RuntimeError("Template subtitle material not found")

        old_mids = {s.get("material_id") for s in track.get("segments") or []}
        texts[:] = [m for m in texts if m.get("id") not in old_mids]

        new_segments: List[Dict[str, Any]] = []
        for ent in subs:
            text_val = ent["text"]
            start_us = int(ent["start_us"])
            end_us = int(ent["end_us"])
            dur_us = max(SEC // 60, end_us - start_us)

            mat = deepcopy(mat_tpl)
            mat_id = _uuid_hex()
            mat["id"] = mat_id
            try:
                content_obj = json.loads(mat.get("content") or "{}")
                if isinstance(content_obj, dict):
                    content_obj["text"] = text_val
                    mat["content"] = json.dumps(content_obj, ensure_ascii=False)
            except Exception:
                mat["content"] = json.dumps({"text": text_val}, ensure_ascii=False)
            mat["base_content"] = text_val
            texts.append(mat)

            seg = deepcopy(seg_tpl)
            seg["id"] = _uuid_hex()
            seg["material_id"] = mat_id
            seg["target_timerange"] = {"start": start_us, "duration": dur_us}
            seg["source_timerange"] = {"start": 0, "duration": dur_us}
            seg["render_timerange"] = {"start": 0, "duration": dur_us}
            new_segments.append(seg)

        track["segments"] = new_segments


def _update_main_belt(data: Dict[str, Any], main_belt_track: Dict[str, Any] | None, title: str) -> None:
    if not main_belt_track or not main_belt_track.get("segments"):
        return
    mats = data.setdefault("materials", {})
    texts: List[Dict[str, Any]] = mats.setdefault("texts", [])
    seg = main_belt_track["segments"][0]
    mid = seg.get("material_id")
    if not mid:
        return
    mat = next((m for m in texts if m.get("id") == mid), None)
    if not mat:
        return
    try:
        content_obj = json.loads(mat.get("content") or "{}")
        if isinstance(content_obj, dict):
            content_obj["text"] = title
            mat["content"] = json.dumps(content_obj, ensure_ascii=False)
    except Exception:
        mat["content"] = json.dumps({"text": title}, ensure_ascii=False)
    mat["base_content"] = title


def patch_draft_json(
    data: Dict[str, Any],
    cues: List[Dict[str, Any]],
    subs: List[Dict[str, Any]],
    images_dir: Path,
    draft_dir: Path,
    title: str,
) -> None:
    srt2_tracks, sub_tracks, belt_track = _find_tracks(data)
    if len(srt2_tracks) < 1:
        raise RuntimeError("No srt2images tracks found in template")
    if len(sub_tracks) < 1:
        raise RuntimeError("No subtitles_text tracks found in template")

    _replace_video_tracks(data, srt2_tracks, cues, images_dir, draft_dir)
    _replace_subtitle_tracks(data, sub_tracks, subs)
    _update_main_belt(data, belt_track, title)


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
        srt_path = run_dir / f"CH06-{vid:03d}.srt"
        if not cues_path.exists() or not images_dir.exists() or not srt_path.exists():
            print(f"[SKIP] CH06-{vid:03d}: run assets missing")
            continue

        cues = load_json(cues_path).get("cues") or []
        subs = parse_srt(srt_path)
        title = title_map.get(vid, f"CH06-{vid:03d}")

        new_name = sanitize_filename(f"★CH06-{vid:03d}-{title}")
        draft_dir = draft_root / new_name
        if draft_dir.exists():
            shutil.rmtree(draft_dir)
        shutil.copytree(tpl_dir, draft_dir)

        # Patch draft_content.json (source of truth)
        content_path = draft_dir / "draft_content.json"
        content_data = load_json(content_path)
        patch_draft_json(content_data, cues, subs, images_dir, draft_dir, title)
        save_json(content_path, content_data)

        # Sync draft_info.json to match content (preserve template metadata keys)
        info_path = draft_dir / "draft_info.json"
        if info_path.exists():
            info_data = load_json(info_path)
            info_data["tracks"] = content_data.get("tracks", [])
            info_data["materials"] = content_data.get("materials", {})
            info_data["duration"] = content_data.get("duration", info_data.get("duration"))
            save_json(info_path, info_data)

        print(f"[OK] CH06-{vid:03d} -> {new_name}")


if __name__ == "__main__":
    main()
