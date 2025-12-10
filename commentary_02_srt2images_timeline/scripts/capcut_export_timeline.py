#!/usr/bin/env python3
"""
Extract a simplified timeline JSON from a CapCut draft (draft_content.json)
for downstream Remotion利用や検証用。

使い方:
  python scripts/capcut_export_timeline.py --draft "/path/to/com.lveditor.draft/★CH06-テンプレ" --out /tmp/remotion_timeline.json

出力:
{
  "fps": 30,
  "duration_us": 123456789,
  "transitions": [{"id": "...", "name": "...", "duration": ...}, ...],
  "tracks": [
    {
      "name": "",
      "type": "video",
      "segments": [
        {
          "start_us": 0,
          "duration_us": 3033333,
          "source_start_us": 0,
          "source_duration_us": 3033333,
          "material_id": "10EE...",
          "material_type": "video|image|text|audio|placeholder|unknown",
          "src": "/abs/path/to/file" (if分かれば),
          "transition": {"transition_id": "...", "duration": ...} (あれば)
        }
      ]
    },
    ...
  ]
}
"""

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List


def load_capcut(draft_dir: Path) -> Dict[str, Any]:
    draft_file = draft_dir / "draft_content.json"
    if not draft_file.exists():
        raise FileNotFoundError(f"draft_content.json not found in {draft_dir}")
    with draft_file.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_material_index(materials: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    materialsはカテゴリごとの配列を持つdict。
    id->recordで引けるようにflattenする。
    """
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
            if not mid:
                continue
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True, help="CapCut project directory (contains draft_content.json)")
    ap.add_argument("--out", help="Output JSON path (default: draft_dir/remotion_timeline.json)")
    args = ap.parse_args()

    draft_dir = Path(args.draft).expanduser().resolve()
    data = load_capcut(draft_dir)

    mat_index = build_material_index(data.get("materials", {}))
    transitions = data.get("transitions", [])
    transitions_out = []
    for tr in transitions:
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
        "source": str(draft_dir),
    }

    out_path = Path(args.out).expanduser() if args.out else draft_dir / "remotion_timeline.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote timeline: {out_path}")
    print(f"Tracks: {len(tracks_out)}; video track segments total: {sum(len(t['segments']) for t in tracks_out if t.get('type')=='video')}")


if __name__ == "__main__":
    main()
