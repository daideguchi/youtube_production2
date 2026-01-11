#!/usr/bin/env python3
# audit_capcut_photo_refs.py
import argparse
import json
from pathlib import Path

def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)

def main():
    ap = argparse.ArgumentParser(description="Audit photo material references in CapCut draft_content.json")
    ap.add_argument("draft", help="Draft dir OR path to draft_content.json")
    args = ap.parse_args()

    p = Path(args.draft).expanduser()
    if p.is_dir():
        p = p / "draft_content.json"

    if not p.exists():
        raise SystemExit(f"draft_content.json not found: {p}")

    d = json.loads(p.read_text(encoding="utf-8"))

    # 画像素材のID集合
    photo_ids = set()
    for o in walk(d):
        if isinstance(o, dict) and o.get("type") == "photo" and "id" in o:
            photo_ids.add(o["id"])

    # 画像セグメントが参照しているID集合
    seg_photo_refs = []
    for o in walk(d):
        if isinstance(o, dict) and "material_id" in o and "target_timerange" in o and "clip" in o:
            mid = o["material_id"]
            if mid in photo_ids:
                seg_photo_refs.append(mid)

    print("photo materials:", len(photo_ids))
    print("segments that reference photo materials:", len(seg_photo_refs))
    print("unreferenced photo materials:", len(photo_ids - set(seg_photo_refs)))
    print("segments referencing missing photo materials:", len([1 for o in seg_photo_refs if o not in photo_ids]))


if __name__ == "__main__":
    main()
