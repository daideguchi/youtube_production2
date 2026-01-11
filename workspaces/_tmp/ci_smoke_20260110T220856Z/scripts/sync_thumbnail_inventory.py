#!/usr/bin/env python3
"""Validate and optionally repair thumbnail project entries against on-disk assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from _bootstrap import bootstrap

bootstrap()

from factory_common.paths import thumbnails_root

THUMB_ROOT = thumbnails_root()
PROJECTS_PATH = THUMB_ROOT / "projects.json"
ASSETS_ROOT = THUMB_ROOT / "assets"


def _load_projects() -> Dict[str, Any]:
    if not PROJECTS_PATH.exists():
        raise FileNotFoundError(f"projects file not found: {PROJECTS_PATH}")
    with PROJECTS_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_channel(value: Any) -> str:
    if not value:
        return ""
    return str(value).strip().upper()


def _normalize_video(value: Any) -> str:
    if not value:
        return ""
    text = str(value).strip()
    return text.zfill(3) if text.isdigit() else text


def _asset_exists(image_path: str) -> bool:
    if not image_path:
        return False
    candidate = ASSETS_ROOT / image_path
    if candidate.exists():
        return True
    # allow already-normalized "CHxx/NNN/file" without leading assets
    candidate = ASSETS_ROOT / image_path.lstrip("/")
    return candidate.exists()


def scan_projects(projects: Dict[str, Any], channel_filter: str | None, repair: bool) -> Dict[str, int]:
    stats = {
        "projects": 0,
        "variants_total": 0,
        "variants_missing": 0,
        "projects_rewritten": 0,
        "selected_pruned": 0,
    }
    project_entries: List[Dict[str, Any]] = projects.get("projects") or []

    for entry in project_entries:
        channel = _normalize_channel(entry.get("channel"))
        video = _normalize_video(entry.get("video"))
        if channel_filter and channel != channel_filter:
            continue
        stats["projects"] += 1
        variants = entry.get("variants") or []
        if not isinstance(variants, list):
            continue
        rewritten = False
        keep_variants = []
        for variant in variants:
            stats["variants_total"] += 1
            image_path = variant.get("image_path") or variant.get("image_url")
            if image_path and _asset_exists(image_path.strip("/")):
                keep_variants.append(variant)
                continue
            stats["variants_missing"] += 1
            rewritten = True
        if rewritten and repair:
            entry["variants"] = keep_variants
            if entry.get("selected_variant_id") and not any(
                v.get("id") == entry.get("selected_variant_id") for v in keep_variants
            ):
                entry.pop("selected_variant_id", None)
                stats["selected_pruned"] += 1
            stats["projects_rewritten"] += 1
    if repair:
        projects["projects"] = [entry for entry in project_entries]
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Thumbnail project inventory checker")
    parser.add_argument("--channel", help="Limit to specific channel code (e.g. CH06)")
    parser.add_argument("--repair", action="store_true", help="Persist fixes (remove variants without files)")
    parser.add_argument("--output", help="Override projects file path", default=str(PROJECTS_PATH))
    args = parser.parse_args()

    target_path = Path(args.output)
    if args.repair and not target_path.parent.exists():
        raise SystemExit(f"Output directory does not exist: {target_path.parent}")

    payload = _load_projects()
    stats = scan_projects(payload, args.channel.upper() if args.channel else None, args.repair)

    if args.repair:
        tmp_path = target_path.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(target_path)
        print(f"✅ Rewrote {target_path} (projects_rewritten={stats['projects_rewritten']})")
    print(
        "projects={projects} variants={variants_total} missing={variants_missing} rewritten={projects_rewritten} pruned_selected={selected_pruned}".format(
            **stats
        )
    )


if __name__ == "__main__":
    main()
