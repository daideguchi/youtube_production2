#!/usr/bin/env python3
"""
Sync srt2images track materials from draft_content.json into draft_info.json
by segment order, preserving timerange and track structure.

What it does:
- Finds track named srt2images_* in draft_content.json and draft_info.json.
- For each segment index, copies material_id from content -> info (timerange untouched).
- Ensures materials.videos in draft_info contain the corresponding material objects from draft_content.
- Does NOT touch other tracks or timeranges.

Usage:
  python3 tools/sync_srt2images_materials.py \
    --draft "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/195_draft-【手動調整後4】"
"""

import argparse
import json
import shutil
import copy
import sys
from pathlib import Path


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(p: Path, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Sync srt2images track materials from draft_content to draft_info (order-based)")
    ap.add_argument("--draft", required=True, help="CapCut draft dir")
    args = ap.parse_args()

    draft_dir = Path(args.draft)
    content_path = draft_dir / "draft_content.json"
    info_path = draft_dir / "draft_info.json"
    if not content_path.exists() or not info_path.exists():
        print("❌ draft_content.json or draft_info.json not found")
        sys.exit(1)

    content = load_json(content_path)
    info = load_json(info_path)

    # Locate target track in content/info
    def find_track(data):
        tracks = data.get("tracks") or data.get("script", {}).get("tracks") or []
        # 1) named srt2images
        for t in tracks:
            name = (t.get("name") or t.get("id") or "").lower()
            if name.startswith("srt2images_"):
                return t
        # 2) longest video track
        videos = [t for t in tracks if t.get("type") == "video"]
        if videos:
            videos = sorted(videos, key=lambda x: len(x.get("segments") or []), reverse=True)
            return videos[0]
        return None

    ct = find_track(content)
    it = find_track(info)
    if not ct or not it:
        print("❌ 対象トラックを特定できませんでした（content/info のどちらかに動画トラックがありません）。")
        sys.exit(1)

    csegs = ct.get("segments", [])
    isegs = it.get("segments", [])
    if not csegs or not isegs:
        print("❌ 対象トラックにセグメントがありません。")
        sys.exit(1)
    if len(csegs) != len(isegs):
        print(f"⚠️ セグメント数が不一致: content({len(csegs)}) vs info({len(isegs)}). 短い方に合わせて同期します。")

    # Build materials lookup from content videos
    c_vids = content.get("materials", {}).get("videos", [])
    by_id = {m.get("id"): m for m in c_vids}

    # Sync material_id by index (shorter of two) and also set material_name if known
    limit = min(len(csegs), len(isegs))
    for i in range(limit):
        mid = csegs[i].get("material_id")
        if mid:
            isegs[i]["material_id"] = mid
            if mid in by_id and by_id[mid].get("material_name"):
                isegs[i]["material_name"] = by_id[mid]["material_name"]

    # Ensure materials.videos in info contain these materials (copy from content)
    target_ids = {csegs[i].get("material_id") for i in range(limit) if csegs[i].get("material_id")}
    i_vids = info.setdefault("materials", {}).setdefault("videos", [])
    i_by_id = {m.get("id"): idx for idx, m in enumerate(i_vids)}
    added = 0
    for mid in target_ids:
        if not mid or mid not in by_id:
            continue
        if mid in i_by_id:
            # replace to keep path/name updated
            i_vids[i_by_id[mid]] = by_id[mid]
        else:
            i_vids.append(by_id[mid])
            added += 1

    # Backup and save
    shutil.copy2(info_path, str(info_path) + ".bak_srt2sync")
    save_json(info_path, info)
    print(f"✅ Synced {limit} segments; added/replaced {len(target_ids)} materials (new add: {added})")


if __name__ == "__main__":
    main()
