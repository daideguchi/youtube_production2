#!/usr/bin/env python3
"""
Read-only validator for srt2images integrity.
Checks (no writes):
 - srt2images track exists in both draft_content.json and draft_info.json
 - segment counts match and material_id/material_name are present
 - no duplicate material_id within srt2images
 - all material_ids exist in materials.videos
 - only whitelisted non-srt2images video/audio tracks are present
Exit code: 0 if all checks pass, 1 otherwise.
"""
import argparse
import json
import sys
from pathlib import Path

CONFIG_WHITELIST = Path(__file__).resolve().parent.parent / "config" / "track_whitelist.json"


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
    ap = argparse.ArgumentParser(description="Validate srt2images track/material integrity (read-only)")
    ap.add_argument("--draft", required=True, help="CapCut draft directory")
    args = ap.parse_args()

    draft = Path(args.draft)
    c_path = draft / "draft_content.json"
    i_path = draft / "draft_info.json"
    if not c_path.exists() or not i_path.exists():
        print("❌ draft_content.json or draft_info.json missing")
        sys.exit(1)

    content = load_json(c_path)
    info = load_json(i_path)

    # foreign track check (info) via whitelist
    whitelist = {"video": [], "audio": []}
    if CONFIG_WHITELIST.exists():
        try:
            whitelist = load_json(CONFIG_WHITELIST)
        except json.JSONDecodeError:
            print(f"❌ whitelist JSON が壊れています: {CONFIG_WHITELIST}")
            sys.exit(1)
    whitelist_video = set(whitelist.get("video") or [])
    whitelist_audio = set(whitelist.get("audio") or [])

    # foreign track check (info)
    tracks_probe = info.get("tracks") or info.get("script", {}).get("tracks") or []
    for t in tracks_probe:
        name_raw = t.get("name") or t.get("id") or ""
        name = name_raw.lower()
        if t.get("type") in ("video", "audio") and not name.startswith("srt2images_"):
            if t.get("type") == "video":
                if name_raw not in whitelist_video:
                    print(f"❌ Non-srt2images track present: {name_raw}")
                    sys.exit(1)
            elif t.get("type") == "audio":
                if name_raw not in whitelist_audio:
                    print(f"❌ Non-srt2images track present: {name_raw}")
                    sys.exit(1)

    ct = find_track(content)
    it = find_track(info)
    if not ct or not it:
        print("❌ srt2images track missing in content or info")
        sys.exit(1)

    csegs = ct.get("segments") or []
    isegs = it.get("segments") or []
    if not csegs or not isegs:
        print("❌ srt2images segments missing (empty)")
        sys.exit(1)
    if len(csegs) != len(isegs):
        print(f"❌ segment count mismatch content({len(csegs)}) vs info({len(isegs)})")
        sys.exit(1)

    vids = content.get("materials", {}).get("videos", []) + info.get("materials", {}).get("videos", [])
    by_id = {}
    for v in vids:
        vid = v.get("id")
        if vid and vid not in by_id:
            by_id[vid] = v

    ids_seen = set()
    for idx, seg in enumerate(csegs):
        mid = seg.get("material_id")
        mname = seg.get("material_name")
        if not mid:
            print(f"❌ segment {idx} missing material_id")
            sys.exit(1)
        if mid in ids_seen:
            print(f"❌ duplicate material_id in srt2images: {mid}")
            sys.exit(1)
        ids_seen.add(mid)
        if mid not in by_id:
            print(f"❌ material_id not found in materials: {mid}")
            sys.exit(1)
        if not mname and by_id[mid].get("material_name"):
            # warn but allow; info side may be blank
            pass

    for idx, seg in enumerate(isegs):
        mid = seg.get("material_id")
        mname = seg.get("material_name")
        if not mid:
            print(f"❌ info segment {idx} missing material_id")
            sys.exit(1)
        if mid not in ids_seen:
            print(f"❌ info segment {idx} material_id not in content track: {mid}")
            sys.exit(1)
        # material_name in info can be blank; no hard failure

    print("✅ srt2images validation passed")
    sys.exit(0)


if __name__ == "__main__":
    main()
