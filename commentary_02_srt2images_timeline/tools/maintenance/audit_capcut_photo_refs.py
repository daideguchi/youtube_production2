#!/usr/bin/env python3
# audit_capcut_photo_refs.py
import json, pathlib
p = pathlib.Path("/Users/dd/Movies/CapCut/User Data/Projects/com.lveditor.draft/【改修】人生の道標_186_多様版_最終/draft_content.json")
d = json.loads(p.read_text(encoding="utf-8"))

def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values(): yield from walk(v)
    elif isinstance(o, list):
        for v in o: yield from walk(v)

# 画像素材のID集合
photo_ids = set()
for o in walk(d):
    if isinstance(o, dict) and o.get("type")=="photo" and "id" in o:
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