#!/usr/bin/env python3
"""
Fix duplicate tracks in CapCut draft.

Problem: Track 6 and Track 7 are identical duplicates with same material IDs.
Solution: Remove Track 7 and remove duplicate materials.
"""
import json
import sys
from pathlib import Path
from collections import Counter


def fix_duplicate_tracks(draft_path: Path, dry_run: bool = False):
    """
    Fix duplicate tracks in CapCut draft.

    Args:
        draft_path: Path to draft directory
        dry_run: If True, only print what would be done without making changes
    """
    draft_content = draft_path / "draft_content.json"

    if not draft_content.exists():
        print(f"❌ draft_content.json not found in {draft_path}")
        return False

    # Backup original
    if not dry_run:
        backup_path = draft_content.with_suffix('.json.backup')
        import shutil
        shutil.copy2(draft_content, backup_path)
        print(f"✅ Created backup: {backup_path}")

    # Load draft
    data = json.loads(draft_content.read_text(encoding='utf-8'))
    tracks = data.get('tracks', [])

    print(f"\n=== Original Structure ===")
    print(f"Total tracks: {len(tracks)}")

    # Find duplicate tracks by name
    track_names = {}
    for idx, track in enumerate(tracks):
        name = track.get('name', 'unnamed')
        track_type = track.get('type', 'unknown')
        segments = len(track.get('segments', []))

        if name not in track_names:
            track_names[name] = []
        track_names[name].append({
            'index': idx,
            'type': track_type,
            'segments': segments
        })

        print(f"Track {idx}: type={track_type:6s}, name='{name:20s}', segments={segments:3d}")

    # Find duplicates
    duplicates = {name: indices for name, indices in track_names.items() if len(indices) > 1}

    if not duplicates:
        print("\n✅ No duplicate track names found")
        return True

    print(f"\n⚠️  Found {len(duplicates)} duplicate track names:")
    tracks_to_remove = []
    for name, info_list in duplicates.items():
        print(f"  '{name}': {len(info_list)} tracks")
        # Keep the first occurrence, mark others for removal
        for info in info_list[1:]:
            tracks_to_remove.append(info['index'])
            print(f"    → Will remove Track {info['index']}")

    if not tracks_to_remove:
        print("\n✅ No tracks to remove")
        return True

    # Get material IDs from tracks to be removed
    materials_to_remove_ids = set()
    for track_idx in tracks_to_remove:
        segments = tracks[track_idx].get('segments', [])
        for seg in segments:
            mat_id = seg.get('material_id')
            if mat_id:
                materials_to_remove_ids.add(mat_id)

    print(f"\n=== Materials to Remove ===")
    print(f"Material IDs from duplicate tracks: {len(materials_to_remove_ids)}")

    # Find which materials are used by OTHER tracks (must keep)
    materials_in_use = set()
    for idx, track in enumerate(tracks):
        if idx not in tracks_to_remove:
            segments = track.get('segments', [])
            for seg in segments:
                mat_id = seg.get('material_id')
                if mat_id:
                    materials_in_use.add(mat_id)

    # Only remove materials that are NOT used by other tracks
    safe_to_remove = materials_to_remove_ids - materials_in_use
    print(f"Material IDs safe to remove (not used by other tracks): {len(safe_to_remove)}")
    print(f"Material IDs still in use (will NOT remove): {len(materials_to_remove_ids - safe_to_remove)}")

    if dry_run:
        print(f"\n🔍 DRY RUN - No changes made")
        print(f"Would remove {len(tracks_to_remove)} tracks")
        print(f"Would remove {len(safe_to_remove)} materials")
        return True

    # Remove duplicate tracks (in reverse order to preserve indices)
    for track_idx in sorted(tracks_to_remove, reverse=True):
        print(f"Removing Track {track_idx}: {tracks[track_idx].get('name')}")
        del tracks[track_idx]

    # Remove unused materials
    materials = data.get('materials', {})
    videos = materials.get('videos', [])
    original_count = len(videos)

    filtered_videos = [m for m in videos if m.get('id') not in safe_to_remove]
    materials['videos'] = filtered_videos

    print(f"\n=== Cleanup Results ===")
    print(f"Materials before: {original_count}")
    print(f"Materials after: {len(filtered_videos)}")
    print(f"Materials removed: {original_count - len(filtered_videos)}")

    # Verify no duplicate material IDs remain
    remaining_ids = [m.get('id') for m in filtered_videos if m.get('id')]
    id_counts = Counter(remaining_ids)
    remaining_dups = {mid: count for mid, count in id_counts.items() if count > 1}

    if remaining_dups:
        print(f"\n⚠️  WARNING: {len(remaining_dups)} material IDs still duplicated:")
        for mid, count in list(remaining_dups.items())[:5]:
            print(f"  {mid}: {count} times")
    else:
        print(f"\n✅ No duplicate material IDs remain")

    # Save fixed draft
    draft_content.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n✅ Fixed draft saved to {draft_content}")

    print(f"\n=== Final Structure ===")
    print(f"Total tracks: {len(data['tracks'])}")
    for idx, track in enumerate(data['tracks']):
        track_type = track.get('type', 'unknown')
        name = track.get('name', 'unnamed')
        segments = len(track.get('segments', []))
        print(f"Track {idx}: type={track_type:6s}, name='{name:20s}', segments={segments:3d}")

    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fix duplicate tracks in CapCut draft")
    ap.add_argument("draft", help="Path to draft directory or draft_content.json")
    ap.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = ap.parse_args()

    draft_path = Path(args.draft)
    if draft_path.is_file() and draft_path.name == "draft_content.json":
        draft_path = draft_path.parent

    if not draft_path.is_dir():
        print(f"❌ Not a directory: {draft_path}")
        sys.exit(1)

    success = fix_duplicate_tracks(draft_path, dry_run=args.dry_run)
    sys.exit(0 if success else 1)
