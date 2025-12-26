#!/usr/bin/env python3
"""
Enforce a fixed image scale for CapCut drafts without touching template layers.

This patches BOTH:
  - draft_content.json
  - draft_info.json

Only video segments whose material path looks like a numbered image asset
(e.g., 0001.png) are updated. This avoids breaking template layers such as
logo/belt backgrounds.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any


DEFAULT_DRAFT_ROOT = Path(
    os.getenv("CAPCUT_DRAFT_ROOT")
    or (Path.home() / "Movies" / "CapCut" / "User Data" / "Projects" / "com.lveditor.draft")
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_numbered_asset(path_str: str) -> bool:
    try:
        name = Path(str(path_str)).name
    except Exception:
        return False
    return bool(re.match(r"^\d{4}\.(png|jpg|jpeg|webp)$", name, flags=re.IGNORECASE))


def _build_material_id_to_path(materials: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in ("videos", "images"):
        items = materials.get(key) or []
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            mid = it.get("id")
            mpath = it.get("path") or it.get("file_path")
            if isinstance(mid, str) and mid and isinstance(mpath, str) and mpath:
                out[mid] = mpath
    return out


def _patch_scale_in_data(data: dict[str, Any], *, scale: float) -> int:
    mats = data.get("materials") or {}
    if not isinstance(mats, dict):
        return 0
    id_to_path = _build_material_id_to_path(mats)

    changed = 0
    tracks = data.get("tracks") or []
    if not isinstance(tracks, list):
        return 0

    for tr in tracks:
        if not isinstance(tr, dict) or tr.get("type") != "video":
            continue
        for seg in tr.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            mid = seg.get("material_id")
            if not isinstance(mid, str) or not mid:
                continue
            mpath = id_to_path.get(mid)
            if not (isinstance(mpath, str) and mpath and _is_numbered_asset(mpath)):
                continue

            clip = seg.setdefault("clip", {})
            if not isinstance(clip, dict):
                seg["clip"] = {}
                clip = seg["clip"]
            s = clip.setdefault("scale", {})
            if not isinstance(s, dict):
                clip["scale"] = {}
                s = clip["scale"]
            if s.get("x") != scale or s.get("y") != scale:
                s["x"] = scale
                s["y"] = scale
                changed += 1

            us = seg.get("uniform_scale")
            if isinstance(us, dict) and us.get("value") != scale:
                us["value"] = scale
                seg["uniform_scale"] = us
                changed += 1

            for kf in seg.get("common_keyframes") or []:
                if not isinstance(kf, dict):
                    continue
                ptype = kf.get("property_type") or ""
                if not (isinstance(ptype, str) and "Scale" in ptype):
                    continue
                for item in kf.get("keyframe_list") or []:
                    if not isinstance(item, dict):
                        continue
                    vals = item.get("values")
                    if not isinstance(vals, list) or not vals:
                        continue
                    new_vals = [scale for _ in vals]
                    if vals != new_vals:
                        item["values"] = new_vals
                        changed += 1

    return changed


def _backup_if_needed(path: Path, *, stamp: str) -> None:
    backup_path = path.with_name(f"{path.name}.bak_scale103_{stamp}")
    if backup_path.exists():
        return
    backup_path.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")


def patch_one_draft(draft_dir: Path, *, scale: float, stamp: str, dry_run: bool) -> int:
    total_changed = 0
    for fname in ("draft_content.json", "draft_info.json"):
        p = draft_dir / fname
        if not p.exists():
            continue
        data = _load_json(p)
        changed = _patch_scale_in_data(data, scale=scale)
        if changed > 0 and not dry_run:
            _backup_if_needed(p, stamp=stamp)
            _save_json(p, data)
        total_changed += changed
    return total_changed


def main() -> None:
    ap = argparse.ArgumentParser(description="Apply fixed image scale to CapCut drafts (numbered assets only)")
    ap.add_argument("--draft-root", type=Path, default=DEFAULT_DRAFT_ROOT)
    ap.add_argument("--draft-regex", required=True, help="Regex to select target draft dirs under draft-root")
    ap.add_argument("--exclude-regex", default="", help="Optional regex to exclude draft dir names")
    ap.add_argument("--scale", type=float, default=1.03)
    ap.add_argument("--dry-run", action="store_true", help="Report changes without writing files")
    args = ap.parse_args()

    if not args.draft_root.exists():
        raise SystemExit(f"draft-root not found: {args.draft_root}")

    include_pat = re.compile(args.draft_regex)
    exclude_pat = re.compile(args.exclude_regex) if args.exclude_regex else None

    targets = [d for d in args.draft_root.iterdir() if d.is_dir() and include_pat.search(d.name)]
    if exclude_pat:
        targets = [d for d in targets if not exclude_pat.search(d.name)]
    targets.sort(key=lambda p: p.name)
    if not targets:
        raise SystemExit("no target drafts matched")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    updated = 0
    for d in targets:
        changed = patch_one_draft(d, scale=args.scale, stamp=stamp, dry_run=args.dry_run)
        print(("OK  " if changed else "NOOP"), d.name, f"changed={changed}")
        if changed:
            updated += 1

    print(f"\nDone. updated={updated}, total={len(targets)}, scale={args.scale}, dry_run={args.dry_run}")


if __name__ == "__main__":
    main()
