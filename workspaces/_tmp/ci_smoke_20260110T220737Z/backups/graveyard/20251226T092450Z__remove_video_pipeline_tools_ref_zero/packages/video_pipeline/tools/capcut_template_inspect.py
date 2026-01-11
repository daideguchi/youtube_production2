#!/usr/bin/env python3
"""
CapCut template inspector: summarize tracks/materials/effects in a draft directory.
Usage:
  python3 tools/capcut_template_inspect.py --draft /path/to/draft_dir [--json]
"""

from __future__ import annotations
import json
from pathlib import Path
import argparse


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft", required=True, help="Path to CapCut draft directory (contains draft_content.json)")
    ap.add_argument("--json", action="store_true", help="Output JSON instead of human readable")
    args = ap.parse_args()

    draft_dir = Path(args.draft).expanduser().resolve()
    content_path = draft_dir / "draft_content.json"
    info_path = draft_dir / "draft_info.json"
    if not content_path.exists():
        raise SystemExit(f"draft_content.json not found under {draft_dir}")

    data = json.loads(content_path.read_text(encoding="utf-8"))
    tracks = data.get("tracks", []) or []
    materials = data.get("materials", {}) or {}

    summary = {
        "draft_dir": str(draft_dir),
        "tracks": [],
        "materials_count": {k: len(v) for k, v in materials.items() if isinstance(v, list)},
        "has_info": info_path.exists(),
    }

    for tr in tracks:
        summary["tracks"].append(
            {
                "name": tr.get("name") or "",
                "type": tr.get("type") or "",
                "segments": len(tr.get("segments") or []),
            }
        )

    if not args.json:
        print(f"Draft: {draft_dir}")
        print(f"Has draft_info.json: {summary['has_info']}")
        print("Materials:")
        for k, v in sorted(summary["materials_count"].items()):
            print(f"  {k}: {v}")
        print("Tracks:")
        for tr in summary["tracks"]:
            print(f"  - {tr['name'] or '(no name)'} | type={tr['type']} | segs={tr['segments']}")
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
