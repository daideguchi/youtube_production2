#!/usr/bin/env python3
"""
Inject title track/segment directly into CapCut draft JSON (draft_content.json + draft_info.json).
Use when pyJianYingDraft title constructors are flaky.

Usage:
    python3 tools/inject_title_json.py --draft "/Users/dd/Movies/CapCut/User Data/Projects/com.lveditor.draft/192_draft" \
        --title "人生の道標 192話 ～タイトル～" --duration 30.0
"""
import argparse
import json
from pathlib import Path


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def save_json(p: Path, data):
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def inject_title(draft_dir: Path, title: str, duration_sec: float, start_sec: float = 0.0):
    content_path = draft_dir / "draft_content.json"
    info_path = draft_dir / "draft_info.json"
    if not content_path.exists() or not info_path.exists():
        raise FileNotFoundError("draft_content.json or draft_info.json missing")

    content = load_json(content_path)
    info = load_json(info_path)

    duration_us = int(duration_sec * 1_000_000)
    start_us = int(start_sec * 1_000_000)

    def _inject(data):
        tracks = data.get("tracks", [])
        materials = data.setdefault("materials", {})
        texts = materials.setdefault("texts", [])

        # 1) try to reuse existing title track (name == title_text or single text track)
        candidate_idx = None
        for idx, t in enumerate(tracks):
            if t.get("type") != "text":
                continue
            name = (t.get("name") or "").lower()
            if "subtitle" in name:
                continue  # 字幕トラックは対象外
            if name == "title_text" or "title" in name:
                candidate_idx = idx
                break

        # Ensure text material
        def _update_material(mat_id: str):
            nonlocal texts
            found = False
            for i, mat in enumerate(texts):
                if mat.get("id") == mat_id:
                    mat["content"] = json.dumps({"text": title}, ensure_ascii=False)
                    found = True
                    break
            if not found:
                texts = [t for t in texts if t.get("id") != mat_id] + [{
                    "id": mat_id,
                    "type": "text",
                    "content": json.dumps({"text": title}, ensure_ascii=False),
                }]
            materials["texts"] = texts

        if candidate_idx is not None:
            track = tracks[candidate_idx]
            segs = track.setdefault("segments", [{}])
            if not segs:
                segs.append({})
            seg = segs[0]
            mat_id = seg.get("material_id") or "title_text_material"
            seg["material_id"] = mat_id
            seg["target_timerange"] = {"start": start_us, "duration": duration_us}
            seg["source_timerange"] = {"start": 0, "duration": duration_us}
            seg["render_timerange"] = {"start": 0, "duration": duration_us}
            if "name" not in track or not track.get("name"):
                track["name"] = "title_text"
            track.setdefault("absolute_index", 1_000_000)
            _update_material(mat_id)
            tracks[candidate_idx] = track
        else:
            mat_id = "title_text_material"
            text_mat = {
                "id": mat_id,
                "type": "text",
                "content": json.dumps({"text": title}, ensure_ascii=False),
            }
            text_seg = {
                "id": "title_seg",
                "material_id": mat_id,
                "target_timerange": {"start": start_us, "duration": duration_us},
                "source_timerange": {"start": 0, "duration": duration_us},
                "render_timerange": {"start": 0, "duration": duration_us},
            }
            text_track = {
                "id": "title_track",
                "type": "text",
                "name": "title_text",
                "absolute_index": 1_000_000,
                "segments": [text_seg],
            }
            tracks.append(text_track)
            materials["texts"] = [t for t in texts if t.get("id") != mat_id] + [text_mat]
            data["tracks"] = tracks
        return data

    content = _inject(content)
    info = _inject(info)

    save_json(content_path, content)
    save_json(info_path, info)


def main():
    ap = argparse.ArgumentParser(description="Inject title into CapCut draft JSON")
    ap.add_argument("--draft", required=True, help="Path to draft folder")
    ap.add_argument("--title", required=True, help="Title text")
    ap.add_argument("--duration", type=float, default=30.0, help="Duration seconds")
    ap.add_argument("--start", type=float, default=0.0, help="Start seconds (e.g., 3.0 for CH01)")
    args = ap.parse_args()
    inject_title(Path(args.draft), args.title, args.duration, args.start)
    print(f"✅ Injected title into {args.draft}")


if __name__ == "__main__":
    main()
