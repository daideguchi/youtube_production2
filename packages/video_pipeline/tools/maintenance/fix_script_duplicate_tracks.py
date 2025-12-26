#!/usr/bin/env python3
"""
Fix duplicate tracks added by capcut_bulk_insert.py script.

Only removes duplicates of script-added tracks, preserving template tracks.
"""
import json
import sys
from pathlib import Path
from collections import Counter


def fix_script_duplicate_tracks(draft_path: Path, dry_run: bool = False):
    """
    Fix duplicate tracks added by script (srt2images_*, subtitles_text, title_text).

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
        backup_path = draft_content.with_suffix('.json.backup_before_dedup')
        import shutil
        shutil.copy2(draft_content, backup_path)
        print(f"✅ Created backup: {backup_path.name}")

    # Load draft
    data = json.loads(draft_content.read_text(encoding='utf-8'))
    tracks = data.get('tracks', [])

    print(f"\n=== Original Structure ===")
    print(f"Total tracks: {len(tracks)}")

    # Track prefixes added by script (not template)
    SCRIPT_TRACK_PATTERNS = ['srt2images_', 'subtitles_text', 'title_text']

    # Group tracks by name
    track_groups = {}
    for idx, track in enumerate(tracks):
        name = track.get('name', '').strip()
        track_type = track.get('type', 'unknown')
        segments = len(track.get('segments', []))

        # Check if this is a script-added track
        is_script_track = any(name.startswith(pattern) or name == pattern
                             for pattern in SCRIPT_TRACK_PATTERNS)

        if is_script_track:
            if name not in track_groups:
                track_groups[name] = []
            track_groups[name].append({
                'index': idx,
                'type': track_type,
                'segments': segments,
                'track': track
            })
            marker = "🔧"
        else:
            marker = "📦"

        print(f"{marker} Track {idx:2d}: type={track_type:6s}, name='{name:25s}', segments={segments:3d}")

    # Find duplicates in script tracks
    print(f"\n=== Script-Added Tracks Analysis ===")
    tracks_to_remove = []
    materials_from_removed = set()

    for name, track_list in track_groups.items():
        if len(track_list) == 1:
            print(f"✅ '{name}': 1 track (no duplicates)")
            continue

        print(f"\n⚠️  '{name}': {len(track_list)} duplicate tracks")

        # Keep the first occurrence with segments, remove others
        kept = None
        for info in track_list:
            if kept is None:
                if info['segments'] > 0:
                    kept = info['index']
                    print(f"   ✅ KEEP Track {info['index']} ({info['segments']} segments)")
                else:
                    print(f"   ⚠️  SKIP Track {info['index']} (0 segments, looking for better candidate)")
            else:
                print(f"   ❌ REMOVE Track {info['index']} ({info['segments']} segments)")
                tracks_to_remove.append(info['index'])

                # Collect material IDs from this track
                for seg in info['track'].get('segments', []):
                    mat_id = seg.get('material_id')
                    if mat_id:
                        materials_from_removed.add(mat_id)

        # If no track with segments was found, keep the first one anyway
        if kept is None and track_list:
            kept = track_list[0]['index']
            print(f"   ✅ KEEP Track {kept} (fallback: first track)")
            for info in track_list[1:]:
                print(f"   ❌ REMOVE Track {info['index']}")
                tracks_to_remove.append(info['index'])

    if not tracks_to_remove:
        print("\n✅ No duplicate script tracks found")
        return True

    # Check which materials are still used by remaining tracks
    materials_in_use = set()
    for idx, track in enumerate(tracks):
        if idx not in tracks_to_remove:
            for seg in track.get('segments', []):
                mat_id = seg.get('material_id')
                if mat_id:
                    materials_in_use.add(mat_id)

    # Only remove materials that are NOT used by any remaining track
    safe_to_remove = materials_from_removed - materials_in_use

    print(f"\n=== Materials Analysis ===")
    print(f"Material IDs from removed tracks: {len(materials_from_removed)}")
    print(f"Material IDs still in use (will KEEP): {len(materials_from_removed - safe_to_remove)}")
    print(f"Material IDs safe to remove: {len(safe_to_remove)}")

    if dry_run:
        print(f"\n🔍 DRY RUN - No changes made")
        print(f"Would remove {len(tracks_to_remove)} tracks: {sorted(tracks_to_remove)}")
        print(f"Would remove {len(safe_to_remove)} materials")
        return True

    # Remove duplicate tracks (in reverse order to preserve indices)
    print(f"\n=== Removing Tracks ===")
    for track_idx in sorted(tracks_to_remove, reverse=True):
        track_name = tracks[track_idx].get('name', 'unnamed')
        segments = len(tracks[track_idx].get('segments', []))
        print(f"Removing Track {track_idx}: '{track_name}' ({segments} segments)")
        del tracks[track_idx]

    # Remove unused materials
    materials = data.get('materials', {})
    videos = materials.get('videos', [])
    original_count = len(videos)

    filtered_videos = [m for m in videos if m.get('id') not in safe_to_remove]
    materials['videos'] = filtered_videos

    print(f"\n=== Materials Cleanup ===")
    print(f"Before: {original_count} materials")
    print(f"After:  {len(filtered_videos)} materials")
    print(f"Removed: {original_count - len(filtered_videos)} materials")

    # Verify no duplicate material IDs remain
    remaining_ids = [m.get('id') for m in filtered_videos if m.get('id')]
    id_counts = Counter(remaining_ids)
    remaining_dups = {mid: count for mid, count in id_counts.items() if count > 1}

    if remaining_dups:
        print(f"\n⚠️  WARNING: {len(remaining_dups)} material IDs still duplicated")
        print(f"   (This might be intentional if materials are reused)")
    else:
        print(f"\n✅ No duplicate material IDs")

    # Save fixed draft
    draft_content.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f"\n✅ Fixed draft saved to {draft_content}")

    print(f"\n=== Final Structure ===")
    print(f"Total tracks: {len(data['tracks'])}")
    script_track_count = 0
    for idx, track in enumerate(data['tracks']):
        track_type = track.get('type', 'unknown')
        name = track.get('name', '').strip()
        segments = len(track.get('segments', []))

        is_script_track = any(name.startswith(pattern) or name == pattern
                             for pattern in SCRIPT_TRACK_PATTERNS)
        if is_script_track:
            marker = "🔧"
            script_track_count += 1
        else:
            marker = "📦"

        print(f"{marker} Track {idx:2d}: type={track_type:6s}, name='{name:25s}', segments={segments:3d}")

    print(f"\n📦 Template tracks: {len(data['tracks']) - script_track_count}")
    print(f"🔧 Script tracks: {script_track_count}")

    return True


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fix duplicate script-added tracks in CapCut draft")
    ap.add_argument("draft", help="Path to draft directory or draft_content.json")
    ap.add_argument("--dry-run", action="store_true", help="Preview changes without applying")
    args = ap.parse_args()

    draft_path = Path(args.draft)
    if draft_path.is_file() and draft_path.name == "draft_content.json":
        draft_path = draft_path.parent

    if not draft_path.is_dir():
        print(f"❌ Not a directory: {draft_path}")
        sys.exit(1)

    success = fix_script_duplicate_tracks(draft_path, dry_run=args.dry_run)
    sys.exit(0 if success else 1)
