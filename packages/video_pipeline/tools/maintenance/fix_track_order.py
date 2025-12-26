#!/usr/bin/env python3
"""
Fix track rendering order by moving image track to the front.

CapCut appears to render tracks in REVERSE order (index 0 = topmost layer).
This script moves the image track (srt2images_*) to index 0 to ensure visibility.
"""
import json
import sys
from pathlib import Path


def fix_track_order(draft_path: Path, dry_run: bool = False):
    """
    Move image track to the front of tracks array.

    Args:
        draft_path: Path to draft directory
        dry_run: If True, only print what would be done
    """
    draft_content = draft_path / "draft_content.json"

    if not draft_content.exists():
        print(f"❌ draft_content.json not found in {draft_path}")
        return False

    # Backup
    if not dry_run:
        backup_path = draft_content.with_suffix('.json.backup_before_reorder')
        import shutil
        shutil.copy2(draft_content, backup_path)
        print(f"✅ Created backup: {backup_path.name}")

    # Load draft
    data = json.loads(draft_content.read_text(encoding='utf-8'))
    tracks = data.get('tracks', [])

    print(f"\n=== Original Track Order ===")
    for idx, track in enumerate(tracks):
        track_type = track.get('type', 'unknown')
        name = track.get('name', '').strip()
        segments = len(track.get('segments', []))
        print(f"Track {idx}: type={track_type:6s}, name='{name:25s}', segments={segments:3d}")

    # Find image track (srt2images_*)
    image_track_idx = None
    for idx, track in enumerate(tracks):
        name = track.get('name', '').strip()
        if name.startswith('srt2images_'):
            image_track_idx = idx
            print(f"\n✅ Found image track at index {idx}: {name}")
            break

    if image_track_idx is None:
        print("\n⚠️  No image track (srt2images_*) found")
        return False

    if image_track_idx == 0:
        print("\n✅ Image track is already at index 0 (topmost)")
        return True

    print(f"\n💡 Moving image track from index {image_track_idx} to index 0")

    if dry_run:
        print(f"\n🔍 DRY RUN - No changes made")
        return True

    # Move image track to the front
    image_track = tracks.pop(image_track_idx)
    tracks.insert(0, image_track)

    # Update data
    data['tracks'] = tracks

    print(f"\n=== New Track Order ===")
    for idx, track in enumerate(tracks):
        track_type = track.get('type', 'unknown')
        name = track.get('name', '').strip()
        segments = len(track.get('segments', []))
        marker = "🔧" if name.startswith('srt2images_') else "📦"
        print(f"{marker} Track {idx}: type={track_type:6s}, name='{name:25s}', segments={segments:3d}")

    # Save
    draft_content.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n✅ Track order fixed and saved to {draft_content}")

    print(f"\n🎬 Next: Open draft in CapCut to verify images are now visible")

    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fix track rendering order in CapCut draft")
    ap.add_argument("draft", help="Path to draft directory or draft_content.json")
    ap.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = ap.parse_args()

    draft_path = Path(args.draft)
    if draft_path.is_file() and draft_path.name == "draft_content.json":
        draft_path = draft_path.parent

    if not draft_path.is_dir():
        print(f"❌ Not a directory: {draft_path}")
        sys.exit(1)

    success = fix_track_order(draft_path, dry_run=args.dry_run)
    sys.exit(0 if success else 1)
