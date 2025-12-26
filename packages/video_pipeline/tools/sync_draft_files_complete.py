#!/usr/bin/env python3
"""
Complete sync of draft_info.json with draft_content.json.

CapCut requires BOTH files to be in sync:
- draft_content.json: Full track and segment details
- draft_info.json: Track list, materials, and metadata

pyJianYingDraft 0.2.3 only updates draft_content.json, causing desync.
This script syncs everything from draft_content to draft_info.
"""
import json
import sys
from pathlib import Path


def sync_complete(draft_path: Path, dry_run: bool = False):
    """
    Complete sync from draft_content.json to draft_info.json.

    Args:
        draft_path: Path to draft directory
        dry_run: If True, only print what would be done
    """
    draft_content_path = draft_path / "draft_content.json"
    draft_info_path = draft_path / "draft_info.json"

    if not draft_content_path.exists():
        print(f"❌ draft_content.json not found")
        return False

    if not draft_info_path.exists():
        print(f"❌ draft_info.json not found")
        return False

    # Load both files
    content_data = json.loads(draft_content_path.read_text(encoding='utf-8'))
    info_data = json.loads(draft_info_path.read_text(encoding='utf-8'))

    print("=== Analyzing Differences ===\n")

    # 1. Compare tracks
    content_tracks = content_data.get('tracks', [])
    info_tracks = info_data.get('tracks', [])

    print(f"Tracks:")
    print(f"  draft_content.json: {len(content_tracks)}")
    print(f"  draft_info.json:    {len(info_tracks)}")
    if len(content_tracks) != len(info_tracks):
        print(f"  ⚠️  Mismatch: {abs(len(content_tracks) - len(info_tracks))} tracks")
    else:
        print(f"  ✅ Match")

    # 2. Compare materials
    content_materials = content_data.get('materials', {})
    info_materials = info_data.get('materials', {})

    print(f"\nMaterials:")
    for mat_type in ['videos', 'audios', 'texts', 'effects']:
        content_count = len(content_materials.get(mat_type, []))
        info_count = len(info_materials.get(mat_type, []))

        match_str = "✅" if content_count == info_count else "⚠️"
        print(f"  {mat_type:10s}: content={content_count:3d}, info={info_count:3d} {match_str}")

    # 3. Check duration
    content_duration = content_data.get('duration', 0)
    info_duration = info_data.get('duration', 0)

    print(f"\nDuration:")
    print(f"  draft_content.json: {content_duration / 1_000_000:.2f}s")
    print(f"  draft_info.json:    {info_duration / 1_000_000:.2f}s")
    if content_duration != info_duration:
        print(f"  ⚠️  Mismatch")
    else:
        print(f"  ✅ Match")

    if dry_run:
        print(f"\n🔍 DRY RUN - No changes made")
        print(f"\nWould sync:")
        print(f"  - Tracks: {len(content_tracks)} tracks")
        print(f"  - Materials.videos: {len(content_materials.get('videos', []))} items")
        print(f"  - Duration: {content_duration}")
        return True

    # Backup
    backup_path = draft_info_path.with_suffix('.json.backup_complete_sync')
    import shutil
    shutil.copy2(draft_info_path, backup_path)
    print(f"\n✅ Created backup: {backup_path.name}")

    # Sync tracks (preserve IDs from info if they exist)
    synced_tracks = []
    for idx, content_track in enumerate(content_tracks):
        if idx < len(info_tracks):
            # Update existing track
            info_track = info_tracks[idx].copy()
            info_track.update({
                'type': content_track.get('type'),
                'name': content_track.get('name', ''),
                'attribute': content_track.get('attribute', 0),
                'flag': content_track.get('flag', 0),
                'segments': content_track.get('segments', [])  # Full sync
            })
            synced_tracks.append(info_track)
        else:
            # Add new track
            new_track = {
                'id': content_track.get('id', f'track_{idx}'),
                'type': content_track.get('type'),
                'name': content_track.get('name', ''),
                'attribute': content_track.get('attribute', 0),
                'flag': content_track.get('flag', 0),
                'segments': content_track.get('segments', [])
            }
            synced_tracks.append(new_track)
            print(f"Added Track {idx}: {new_track['type']} '{new_track['name']}'")

    info_data['tracks'] = synced_tracks

    # Sync materials
    print(f"\nSyncing materials...")
    info_data['materials'] = content_materials.copy()
    print(f"  ✅ Synced {len(content_materials.get('videos', []))} video materials")

    # Sync duration
    info_data['duration'] = content_duration
    print(f"  ✅ Synced duration: {content_duration / 1_000_000:.2f}s")

    # Save
    draft_info_path.write_text(json.dumps(info_data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n✅ Complete sync saved to {draft_info_path.name}")

    # Verify
    print(f"\n=== Verification ===")
    verify_data = json.loads(draft_info_path.read_text(encoding='utf-8'))
    verify_tracks = verify_data.get('tracks', [])
    verify_videos = verify_data.get('materials', {}).get('videos', [])

    print(f"draft_info.json now has:")
    print(f"  - {len(verify_tracks)} tracks")
    print(f"  - {len(verify_videos)} video materials")
    print(f"  - Duration: {verify_data.get('duration', 0) / 1_000_000:.2f}s")

    # Check for our images
    our_images = [m for m in verify_videos if draft_path.name in m.get('path', '')]
    print(f"  - Our images: {len(our_images)}")

    if len(our_images) > 0:
        print(f"\n✅ SUCCESS: Our images are now in draft_info.json")
    else:
        print(f"\n⚠️  WARNING: Our images still not found in draft_info.json")

    print(f"\n🎬 Next: Open draft in CapCut - images should now be visible")

    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Complete sync of draft_info.json with draft_content.json")
    ap.add_argument("draft", help="Path to draft directory")
    ap.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = ap.parse_args()

    draft_path = Path(args.draft)

    if not draft_path.is_dir():
        print(f"❌ Not a directory: {draft_path}")
        sys.exit(1)

    success = sync_complete(draft_path, dry_run=args.dry_run)
    sys.exit(0 if success else 1)
