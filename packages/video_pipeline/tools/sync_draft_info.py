#!/usr/bin/env python3
"""
Sync draft_info.json tracks with draft_content.json.

CapCut uses draft_info.json as the primary source for track information.
If draft_content.json has more tracks than draft_info.json, CapCut ignores them.

This script syncs track count and basic info from draft_content.json to draft_info.json.
"""
import json
import sys
from pathlib import Path


def sync_draft_info(draft_path: Path, dry_run: bool = False):
    """
    Sync track information from draft_content.json to draft_info.json.

    Args:
        draft_path: Path to draft directory
        dry_run: If True, only print what would be done
    """
    draft_content_path = draft_path / "draft_content.json"
    draft_info_path = draft_path / "draft_info.json"

    if not draft_content_path.exists():
        print(f"❌ draft_content.json not found in {draft_path}")
        return False

    if not draft_info_path.exists():
        print(f"❌ draft_info.json not found in {draft_path}")
        return False

    # Load both files
    content_data = json.loads(draft_content_path.read_text(encoding='utf-8'))
    info_data = json.loads(draft_info_path.read_text(encoding='utf-8'))

    content_tracks = content_data.get('tracks', [])
    info_tracks = info_data.get('tracks', [])

    print(f"=== Track Count Comparison ===")
    print(f"draft_content.json: {len(content_tracks)} tracks")
    print(f"draft_info.json:    {len(info_tracks)} tracks")

    if len(content_tracks) == len(info_tracks):
        print("\n✅ Track counts match - no sync needed")
        return True

    print(f"\n⚠️  Mismatch: {len(content_tracks) - len(info_tracks)} tracks need to be synced")

    # Show what tracks are missing
    print(f"\n=== Missing Tracks in draft_info.json ===")
    for idx in range(len(info_tracks), len(content_tracks)):
        track = content_tracks[idx]
        track_type = track.get('type', 'unknown')
        name = track.get('name', '').strip()
        segments = len(track.get('segments', []))
        print(f"Track {idx}: type={track_type:6s}, name='{name:25s}', segments={segments:3d}")

    if dry_run:
        print(f"\n🔍 DRY RUN - No changes made")
        return True

    # Backup draft_info.json
    backup_path = draft_info_path.with_suffix('.json.backup_before_sync')
    import shutil
    shutil.copy2(draft_info_path, backup_path)
    print(f"\n✅ Created backup: {backup_path.name}")

    # Add missing tracks to draft_info.json
    # We need to create minimal track entries that CapCut expects
    for idx in range(len(info_tracks), len(content_tracks)):
        content_track = content_tracks[idx]

        # Create a minimal track entry for draft_info.json
        # Based on template tracks, the structure is:
        info_track = {
            "attribute": content_track.get('attribute', 0),
            "flag": content_track.get('flag', 0),
            "id": content_track.get('id', f"track_{idx}"),
            "name": content_track.get('name', ''),
            "segments": [],  # draft_info.json might not need segment details
            "type": content_track.get('type', 'video')
        }

        info_tracks.append(info_track)
        print(f"Added Track {idx}: type={info_track['type']}, name='{info_track['name']}'")

    # Update track count
    info_data['tracks'] = info_tracks

    # Save updated draft_info.json
    draft_info_path.write_text(json.dumps(info_data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n✅ Updated draft_info.json with {len(info_tracks)} tracks")

    print(f"\n🎬 Next: Open draft in CapCut to verify tracks are now recognized")

    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Sync draft_info.json tracks with draft_content.json")
    ap.add_argument("draft", help="Path to draft directory")
    ap.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = ap.parse_args()

    draft_path = Path(args.draft)

    if not draft_path.is_dir():
        print(f"❌ Not a directory: {draft_path}")
        sys.exit(1)

    success = sync_draft_info(draft_path, dry_run=args.dry_run)
    sys.exit(0 if success else 1)
