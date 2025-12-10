from __future__ import annotations
import json
from pathlib import Path
from typing import List, Dict


def build_capcut_draft(out_dir: Path, cues: List[Dict], fps: int, crossfade: float, size: Dict) -> Path:
    """Attempt to prepare a CapCut draft. If CapCutAPI is unavailable, write a stub draft
    and instructions for the user to finalize.
    """
    draft_dir = out_dir / "capcut_draft"
    draft_dir.mkdir(parents=True, exist_ok=True)

    # Try to import a hypothetical CapCutAPI; fallback if not present
    api_ok = False
    try:
        # Example placeholder import name; actual library name may differ.
        # from capcutapi import Draft
        # api_ok = True
        api_ok = False  # disabled by default; requires user to install a real API
    except Exception:
        api_ok = False

    if api_ok:
        # Pseudocode placeholder for real implementation
        pass
    else:
        # Stub JSONs for documentation/reference; these are not guaranteed to be importable by CapCut.
        # They exist to show intended structure and allow the user to map into a real API later.
        timeline = []
        cur = 0.0
        images_dir = out_dir / "images"
        for i, c in enumerate(cues):
            start = cur if i == 0 else max(0.0, cur - crossfade)
            end = start + c["duration_sec"]
            # Prefer generated image path in out_dir/images
            default_name = f"{i+1:04d}.png"
            if c.get("image_path"):
                img_path = Path(c["image_path"]).resolve()
            else:
                img_path = (images_dir / default_name).resolve()
            timeline.append(
                {
                    "index": i + 1,
                    "image": str(img_path),
                    "start_sec": round(start, 3),
                    "end_sec": round(end, 3),
                    "summary": c.get("summary", ""),
                }
            )
            cur = end

        (draft_dir / "README.txt").write_text(
            (
                "This is a stub CapCut draft folder.\n\n"
                "To generate a real CapCut draft:\n"
                "  1) Install a CapCut draft API implementation (e.g., an open-source CapCutAPI).\n"
                "  2) Replace this stub with code that calls create_draft() and add_image() at the timestamps below.\n"
                "  3) Optionally add subtitles from the original SRT.\n\n"
                "The files draft_meta.json and draft_content.json here are placeholders for reference only.\n"
            ),
            encoding="utf-8",
        )

        (draft_dir / "draft_meta.json").write_text(
            json.dumps({"fps": fps, "size": size}, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (draft_dir / "draft_content.json").write_text(
            json.dumps({"clips": timeline}, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return draft_dir
