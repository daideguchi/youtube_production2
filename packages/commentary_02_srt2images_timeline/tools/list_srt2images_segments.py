#!/usr/bin/env python3
"""
Read-only listing of srt2images timeline.
Prints index, material_id, material_name from draft_content.json srt2images track.
"""
import argparse
import json
import sys
from pathlib import Path


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def find_track(data):
    tracks = data.get("tracks") or data.get("script", {}).get("tracks") or []
    for t in tracks:
        nm = t.get("name") or t.get("id") or ""
        if nm.startswith("srt2images_"):
            return t
    return None


def main():
    ap = argparse.ArgumentParser(description="List srt2images segments (read-only)")
    ap.add_argument("--draft", required=True, help="CapCut draft directory")
    args = ap.parse_args()

    draft = Path(args.draft)
    c_path = draft / "draft_content.json"
    if not c_path.exists():
        print("❌ draft_content.json missing")
        sys.exit(1)

    content = load_json(c_path)
    ct = find_track(content)
    if not ct:
        print("❌ srt2images track missing in content")
        sys.exit(1)

    c_vids = content.get("materials", {}).get("videos", [])
    by_id = {m.get("id"): m for m in c_vids}

    segs = ct.get("segments") or []
    if not segs:
        print("❌ srt2images segments missing")
        sys.exit(1)

    for idx, seg in enumerate(segs, start=1):
        mid = seg.get("material_id")
        mname = seg.get("material_name") or by_id.get(mid, {}).get("material_name")
        print(f"{idx:02d}: {mname} ({mid})")

    sys.exit(0)


if __name__ == "__main__":
    main()
