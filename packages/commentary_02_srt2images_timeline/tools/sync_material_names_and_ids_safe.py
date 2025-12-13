#!/usr/bin/env python3
"""
Update material_name and id in draft_info.json to match draft_content.json
for specific targets. Track structure and timerange are not touched.

Usage:
  python3 tools/sync_material_names_and_ids_safe.py \
    --draft "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/195_draft-【手動調整後4】" \
    --targets \
      0004_v1764583832.png=04cfba85-a7fa-417a-931e-1f1a9af943fe \
      0005_v1764583876.png=07a78a60-73dc-4627-83cc-66a1ab4bc3f7 \
      ...
"""

import argparse
import json
from pathlib import Path
import shutil


def main():
    ap = argparse.ArgumentParser(description="Sync material_name+id into draft_info (names/ids from draft_content). Track structure untouched.")
    ap.add_argument("--draft", required=True, help="CapCut draft dir")
    ap.add_argument("--targets", nargs="+", required=True, help="name=id pairs, e.g., 0004_vxxx.png=uuid ...")
    args = ap.parse_args()

    draft_dir = Path(args.draft)
    info_path = draft_dir / "draft_info.json"
    if not info_path.exists():
        print("❌ draft_info.json not found")
        return

    # parse targets
    target_map = {}
    for t in args.targets:
        if "=" not in t:
            print(f"⚠️ invalid target '{t}', expected name=id")
            continue
        name, mid = t.split("=", 1)
        target_map[name] = mid

    info = json.loads(info_path.read_text(encoding="utf-8"))
    vids = info.get("materials", {}).get("videos", [])

    updated = 0
    for m in vids:
        name = m.get("material_name", "")
        if name in target_map:
            new_id = target_map[name]
            if m.get("id") != new_id:
                m["id"] = new_id
                updated += 1

    if updated == 0:
        print("ℹ️ No ids updated (names not found in draft_info videos).")
        return

    # backup info
    shutil.copy2(info_path, str(info_path) + ".bak_safe_sync")
    info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ Updated {updated} materials in draft_info.json (names/ids). Track/timerange untouched.")


if __name__ == "__main__":
    main()
