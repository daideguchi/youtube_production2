#!/usr/bin/env python3
"""
Generic CapCut draft image path fixer

Fixes common issues with CapCut drafts created by external tools:
1. Copies images to draft/assets/image/
2. Updates material paths to point inside draft
3. Fixes render_timerange for video segments
4. Removes media_path (CapCut regenerates it)
5. Cleans cache files

Usage:
    python3 tools/fix_draft_images.py <draft_name> <image_dir> [--dry-run]

Example:
    python3 tools/fix_draft_images.py "001_人生の道標_186_Phase1完成版" "output/jinsei186/images"
"""

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Dict, Any, List

DRAFT_ROOT = Path.home() / "Movies/CapCut/User Data/Projects/com.lveditor.draft"

def load_json(path: Path) -> Dict[str, Any]:
    """Load JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))

def save_json(path: Path, data: Dict[str, Any]) -> None:
    """Save JSON file."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)

def copy_images_to_draft(image_dir: Path, assets_dir: Path, dry_run: bool = False) -> int:
    """
    Copy images from source directory to draft/assets/image/

    Returns:
        Number of images copied
    """
    if not image_dir.exists():
        print(f"❌ Image directory not found: {image_dir}")
        return 0

    source_images = sorted(image_dir.glob("*.png"))
    if not source_images:
        print(f"❌ No PNG images found in {image_dir}")
        return 0

    print(f"📋 Found {len(source_images)} images in source")

    if dry_run:
        print(f"   [DRY RUN] Would copy to: {assets_dir}")
        return len(source_images)

    # Create assets/image directory
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Copy images
    copied = 0
    for img in source_images:
        dest = assets_dir / img.name
        shutil.copy2(img, dest)
        copied += 1
        if copied <= 3:
            size_mb = dest.stat().st_size / (1024 * 1024)
            print(f"  ✅ {img.name} ({size_mb:.1f}MB)")

    if copied > 3:
        print(f"  ... and {copied - 3} more")

    print(f"✅ Copied {copied} images to {assets_dir}")
    return copied

def update_image_paths(data: Dict[str, Any], assets_dir: Path) -> int:
    """
    Update image material paths to point to draft/assets/image/

    Returns:
        Number of paths updated
    """
    materials = data.get("materials", {})
    videos = materials.get("videos", [])

    updated = 0
    for vid in videos:
        old_path = vid.get("path", "")
        if old_path.endswith(".png"):
            filename = Path(old_path).name
            new_path = str((assets_dir / filename).resolve())

            vid["path"] = new_path

            # Remove media_path (CapCut regenerates this)
            if "media_path" in vid:
                vid["media_path"] = ""

            updated += 1

    return updated

def fix_render_timerange(data: Dict[str, Any]) -> int:
    """
    Fix render_timerange for all video segments.

    Returns:
        Number of segments fixed
    """
    materials = data.get("materials", {})
    videos = materials.get("videos", [])
    material_dict = {vid["id"]: vid for vid in videos}

    tracks = data.get("tracks", [])
    fixed = 0

    for track in tracks:
        if track.get("type") != "video":
            continue

        for seg in track.get("segments", []):
            mat_id = seg.get("material_id", "")
            if mat_id in material_dict:
                material = material_dict[mat_id]
                mat_duration = material.get("duration", 0)

                seg["render_timerange"] = {
                    "start": 0,
                    "duration": mat_duration
                }
                fixed += 1

    return fixed

def clean_cache(draft_path: Path, dry_run: bool = False) -> List[str]:
    """
    Clean CapCut cache files.

    Returns:
        List of removed files
    """
    cache_files = [
        "template.tmp",
        "template-2.tmp",
        "performance_opt_info.json",
    ]

    removed = []
    for fn in cache_files:
        cache_file = draft_path / fn
        if cache_file.exists():
            if not dry_run:
                cache_file.unlink()
            removed.append(fn)

    return removed

def main():
    parser = argparse.ArgumentParser(
        description="Fix CapCut draft image paths and related issues",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Fix draft with images from output directory:
    python3 tools/fix_draft_images.py "001_人生の道標_186_Phase1完成版" "output/jinsei186/images"

  Dry run to see what would be done:
    python3 tools/fix_draft_images.py "MyDraft" "images/" --dry-run
        """
    )

    parser.add_argument("draft_name", help="Draft folder name")
    parser.add_argument("image_dir", help="Directory containing PNG images")
    parser.add_argument("--draft-root", default=str(DRAFT_ROOT), help=f"CapCut draft root (default: {DRAFT_ROOT})")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")

    args = parser.parse_args()

    # Validate paths
    draft_root = Path(args.draft_root).expanduser()
    draft_path = draft_root / args.draft_name
    image_dir = Path(args.image_dir).expanduser()

    if not draft_path.exists():
        print(f"❌ Draft not found: {draft_path}")
        return 1

    draft_info = draft_path / "draft_info.json"
    if not draft_info.exists():
        print(f"❌ draft_info.json not found in draft")
        return 1

    assets_dir = draft_path / "assets" / "image"

    print("🔧 CapCut Draft Image Fixer")
    print("="*70)
    print(f"📁 Draft: {args.draft_name}")
    print(f"🖼️  Images: {image_dir}")
    if args.dry_run:
        print("⚠️  DRY RUN MODE - No changes will be made")
    print()

    # Step 1: Copy images
    print("📋 Step 1: Copy images to draft/assets/image/")
    copied_count = copy_images_to_draft(image_dir, assets_dir, args.dry_run)
    if copied_count == 0:
        print("❌ No images to process")
        return 1
    print()

    if args.dry_run:
        print("✅ Dry run completed - no changes made")
        return 0

    # Step 2: Load and backup draft_info.json
    print("📋 Step 2: Load draft_info.json")
    data = load_json(draft_info)

    backup = draft_info.with_name("draft_info.json.backup_fix")
    shutil.copy2(draft_info, backup)
    print(f"✅ Backup created: {backup.name}")
    print()

    # Step 3: Update image paths
    print("📋 Step 3: Update image material paths")
    updated_paths = update_image_paths(data, assets_dir)
    print(f"✅ Updated {updated_paths} image material paths")
    print()

    # Step 4: Fix render_timerange
    print("📋 Step 4: Fix render_timerange for video segments")
    fixed_segments = fix_render_timerange(data)
    print(f"✅ Fixed {fixed_segments} video segments")
    print()

    # Step 5: Save
    print("📋 Step 5: Save draft_info.json")
    save_json(draft_info, data)
    print(f"✅ Saved: draft_info.json")
    print()

    # Step 6: Clean cache
    print("📋 Step 6: Clean cache files")
    removed = clean_cache(draft_path)
    if removed:
        for fn in removed:
            print(f"  Removed: {fn}")
        print(f"✅ Cleaned {len(removed)} cache files")
    else:
        print("  No cache files to clean")
    print()

    # Summary
    print("="*70)
    print("🎉 Draft fix completed successfully!")
    print("="*70)
    print(f"📁 Draft: {draft_path}")
    print(f"🖼️  Images: {copied_count} files in {assets_dir}")
    print(f"📝 Paths updated: {updated_paths}")
    print(f"🔧 Segments fixed: {fixed_segments}")
    print()
    print("🎬 Next steps:")
    print("  1. Close CapCut completely if it's open")
    print("  2. Wait 5 seconds")
    print("  3. Open CapCut and load the draft")
    print("  4. Verify images are properly linked")
    print("="*70)

    return 0

if __name__ == "__main__":
    sys.exit(main())
