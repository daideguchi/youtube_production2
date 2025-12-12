#!/usr/bin/env python3
"""
Restore CapCut belt design in draft_info.json from a template draft.

Why:
- Some generation steps sync draft_info.json from draft_content.json and can wipe
  template-only styling that CapCut stores ONLY in draft_info.json segments:
  - segment.clip (transform/scale)
  - segment.extra_material_refs (e.g., text_shape background)
  - referenced materials.effects entries

This tool copies the belt track segment styling from a template draft_info.json
into target drafts, WITHOUT changing timing or the belt text string.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_DRAFT_ROOT = Path(
    os.getenv("CAPCUT_DRAFT_ROOT")
    or (Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft")
)


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _find_track(tracks: List[Dict[str, Any]], track_id: str) -> Optional[Dict[str, Any]]:
    for t in tracks:
        if t.get("id") == track_id:
            return t
    return None


def _extract_template_belt(template_info: Dict[str, Any], belt_track_id: str) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
    tracks = template_info.get("tracks", [])
    if not isinstance(tracks, list):
        raise ValueError("template draft_info.json has no tracks[]")
    track = _find_track(tracks, belt_track_id)
    if not track:
        raise ValueError(f"template missing track id={belt_track_id}")
    segs = track.get("segments") or []
    if not segs or not isinstance(segs, list) or not isinstance(segs[0], dict):
        raise ValueError(f"template track {belt_track_id} has no segments")
    seg0 = segs[0]
    clip = deepcopy(seg0.get("clip") or {})
    refs = list(seg0.get("extra_material_refs") or [])
    mats = template_info.get("materials", {}) or {}
    effects = {e.get("id"): deepcopy(e) for e in mats.get("effects", []) if isinstance(e, dict) and e.get("id")}
    needed_effects = {rid: effects[rid] for rid in refs if rid in effects}
    return clip, refs, needed_effects


def _ensure_effects(target_info: Dict[str, Any], needed_effects: Dict[str, Any]) -> bool:
    if not needed_effects:
        return False
    mats = target_info.setdefault("materials", {})
    if not isinstance(mats, dict):
        return False
    effects = mats.setdefault("effects", [])
    if not isinstance(effects, list):
        mats["effects"] = []
        effects = mats["effects"]
    existing = {e.get("id") for e in effects if isinstance(e, dict) and e.get("id")}
    changed = False
    for eid, eff in needed_effects.items():
        if eid in existing:
            continue
        effects.append(deepcopy(eff))
        existing.add(eid)
        changed = True
    return changed


def _ensure_belt_text_content(target_info: Dict[str, Any]) -> bool:
    """
    CapCut template uses draft_info.materials.texts[].content as plain string.
    If a belt text ends up with content=="" but base_content is filled, copy it back.
    """
    mats = target_info.get("materials", {})
    if not isinstance(mats, dict):
        return False
    texts = mats.get("texts")
    if not isinstance(texts, list):
        return False
    changed = False
    for t in texts:
        if not isinstance(t, dict):
            continue
        if t.get("id") != "belt_main_text":
            continue
        if isinstance(t.get("content"), str) and not t["content"]:
            bc = t.get("base_content")
            if isinstance(bc, str) and bc:
                t["content"] = bc
                changed = True
        break
    return changed


def restore_one_draft(
    draft_dir: Path,
    *,
    belt_track_id: str,
    template_clip: Dict[str, Any],
    template_refs: List[str],
    needed_effects: Dict[str, Any],
) -> bool:
    info_path = draft_dir / "draft_info.json"
    if not info_path.exists():
        return False
    info = _load_json(info_path)
    tracks = info.get("tracks", [])
    if not isinstance(tracks, list):
        return False
    belt = _find_track(tracks, belt_track_id)
    if not belt:
        return False
    segs = belt.get("segments") or []
    if not segs or not isinstance(segs, list) or not isinstance(segs[0], dict):
        return False

    changed = False
    seg0 = segs[0]
    # Preserve timing fields from the generated draft, but restore visual styling from template.
    if seg0.get("clip") != template_clip:
        seg0["clip"] = deepcopy(template_clip)
        changed = True
    if (seg0.get("extra_material_refs") or []) != template_refs:
        seg0["extra_material_refs"] = list(template_refs)
        changed = True

    changed |= _ensure_effects(info, needed_effects)
    changed |= _ensure_belt_text_content(info)

    if changed:
        _save_json(info_path, info)
    return changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft-root", type=Path, default=DEFAULT_DRAFT_ROOT)
    ap.add_argument("--template", required=True, help="CapCut template draft directory name (e.g., CH02-テンプレ)")
    ap.add_argument("--belt-track-id", default="belt_main_track")
    ap.add_argument(
        "--draft-regex",
        required=True,
        help="Regex to select target draft directories under draft-root (e.g., '^CH02-014_.*_draft$')",
    )
    args = ap.parse_args()

    template_dir = args.draft_root / args.template
    template_info_path = template_dir / "draft_info.json"
    if not template_info_path.exists():
        raise SystemExit(f"template draft_info.json not found: {template_info_path}")

    template_info = _load_json(template_info_path)
    clip, refs, needed_effects = _extract_template_belt(template_info, args.belt_track_id)

    pattern = re.compile(args.draft_regex)
    targets = [d for d in args.draft_root.iterdir() if d.is_dir() and pattern.match(d.name)]
    targets.sort(key=lambda p: p.name)
    if not targets:
        raise SystemExit("no target drafts matched")

    changed = 0
    for d in targets:
        did = restore_one_draft(
            d,
            belt_track_id=args.belt_track_id,
            template_clip=clip,
            template_refs=refs,
            needed_effects=needed_effects,
        )
        print(("OK  " if did else "NOOP"), d.name)
        if did:
            changed += 1

    print(f"\nDone. updated={changed}, total={len(targets)}")


if __name__ == "__main__":
    main()
