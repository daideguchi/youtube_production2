#!/usr/bin/env python3
"""
CapCut draft に画像フォルダの連番を自動割り当てし、material/src を埋めた
patched draft と Remotion 用タイムラインを出力する。

使い方:
  python scripts/capcut_link_images.py \
    --draft "~/Movies/CapCut/User Data/Projects/com.lveditor.draft/★CH06-テンプレ" \
    --images "/path/to/images"

生成物:
  draft_content.json.patched        : material/images が埋まった CapCut draft
  remotion_timeline_with_src.json   : Remotion 再生用の簡易タイムライン

前提:
  - 画像フォルダに連番 (png/jpg/webp など) が並んでいること。
  - CapCut draft の video セグメント順に画像を割り当てる。
  - 既存の materials.images は置き換えないが、新規で images を追記し
    segments.material_id を差し替える。
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List
import itertools
import shutil


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, data: Dict[str, Any]):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def find_images(images_dir: Path) -> List[Path]:
    exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    files = [p for p in sorted(images_dir.iterdir()) if p.suffix.lower() in exts]
    return files


def build_material_index(materials: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    idx: Dict[str, Dict[str, Any]] = {}
    if not isinstance(materials, dict):
        return idx
    for cat, items in materials.items():
        if not isinstance(items, list):
            continue
        for rec in items:
            if not isinstance(rec, dict):
                continue
            mid = rec.get("id")
            if mid:
                rec_copy = dict(rec)
                rec_copy["_category"] = cat
                idx[mid] = rec_copy
    return idx


def material_type(rec: Dict[str, Any] | None) -> str:
    if not rec:
        return "unknown"
    t = rec.get("type") or rec.get("_category")
    if t == "extract_music":
        return "audio"
    if t:
        return str(t)
    return "unknown"


def assign_images_to_segments(data: Dict[str, Any], images: List[Path]) -> Dict[str, Any]:
    materials = data.get("materials")
    if not isinstance(materials, dict):
        materials = {}
        data["materials"] = materials

    existing_images = materials.get("images")
    if not isinstance(existing_images, list):
        existing_images = []
    new_images: List[Dict[str, Any]] = []

    # collect video segments
    video_segments = []
    for track in data.get("tracks", []):
        if track.get("type") != "video":
            continue
        for seg in track.get("segments", []) or []:
            video_segments.append(seg)

    if not video_segments:
        raise RuntimeError("No video segments found; nothing to assign")

    # cycle images if fewer than segments
    img_iter = itertools.cycle(images)

    # assign
    for i, seg in enumerate(video_segments):
        img_path = next(img_iter)
        mid = f"img-{i:05d}"
        img_entry = {
            "app_id": 0,
            "category_id": "local",
            "category_name": "local",
            "check_flag": 1,
            "duration": None,
            "effect_id": "",
            "formula_id": "",
            "id": mid,
            "local_material_id": mid,
            "name": img_path.name,
            "path": str(img_path),
            "resource_id": "",
            "type": "image",
        }
        new_images.append(img_entry)
        seg["material_id"] = mid
        seg["materialId"] = mid
        seg["material_type"] = "image"

    materials["images"] = new_images
    return data


def export_timeline(data: Dict[str, Any], out_path: Path):
    materials = data.get("materials", {})
    mat_index = build_material_index(materials)

    transitions_out = []
    for tr in data.get("transitions", []) or []:
        if not isinstance(tr, dict):
            continue
        transitions_out.append(
            {
                "id": tr.get("id"),
                "name": tr.get("name"),
                "duration": tr.get("duration"),
                "effect_id": tr.get("effect_id"),
                "resource_id": tr.get("resource_id"),
            }
        )

    tracks_out: List[Dict[str, Any]] = []
    for track in data.get("tracks", []):
        if not isinstance(track, dict):
            continue
        ttype = track.get("type")
        name = track.get("name") or ""
        segs_out = []
        for seg in track.get("segments", []) or []:
            if not isinstance(seg, dict):
                continue
            trange = seg.get("target_timerange") or {}
            source_trange = seg.get("source_timerange") or {}
            mid = seg.get("material_id") or seg.get("materialId")
            mrec = mat_index.get(mid) if mid else None
            mtype = material_type(mrec)
            src = None
            if mrec:
                src = mrec.get("path") or mrec.get("file_path") or mrec.get("resource_path")
            segs_out.append(
                {
                    "start_us": trange.get("start"),
                    "duration_us": trange.get("duration"),
                    "source_start_us": source_trange.get("start"),
                    "source_duration_us": source_trange.get("duration"),
                    "material_id": mid,
                    "material_type": mtype,
                    "src": src,
                    "transition": seg.get("transition"),
                    "id": seg.get("id"),
                }
            )
        tracks_out.append({"name": name, "type": ttype, "segments": segs_out})

    out = {
        "fps": data.get("fps"),
        "duration_us": data.get("duration"),
        "transitions": transitions_out,
        "tracks": tracks_out,
    }
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote timeline with src: {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True, help="CapCut project directory (contains draft_content.json)")
    ap.add_argument("--images", required=True, help="Directory of image files (png/jpg/webp...) in order")
    args = ap.parse_args()

    draft_dir = Path(args.draft).expanduser().resolve()
    images_dir = Path(args.images).expanduser().resolve()

    draft_file = draft_dir / "draft_content.json"
    if not draft_file.exists():
        raise FileNotFoundError(f"draft_content.json not found in {draft_dir}")

    images = find_images(images_dir)
    if not images:
        raise RuntimeError(f"No images found in {images_dir}")

    data = load_json(draft_file)
    backup = draft_file.with_suffix(".json.bak_link_images")
    shutil.copy2(draft_file, backup)

    data = assign_images_to_segments(data, images)
    patched = draft_file.with_suffix(".json.patched")
    save_json(patched, data)
    print(f"Patched draft written: {patched}")

    timeline_out = draft_dir / "remotion_timeline_with_src.json"
    export_timeline(data, timeline_out)


if __name__ == "__main__":
    main()
