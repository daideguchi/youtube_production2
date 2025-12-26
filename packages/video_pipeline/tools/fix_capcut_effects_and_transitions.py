#!/usr/bin/env python3
"""
CapCut Draft Effect & Transition Restoration Tool

Purpose:
    Fix two critical issues in generated CapCut drafts:
    1. Restore effect segments and materials from template (pyJianYingDraft loses them)
    2. Add fade transitions between consecutive image segments

Usage:
    python3 tools/fix_capcut_effects_and_transitions.py \
        --draft "/path/to/draft" \
        --template "/path/to/template" \
        --crossfade 0.5

Technical Details:
    - Copies effect track segments and video_effects materials from template
    - Adjusts effect duration to match draft duration
    - Adds crossfade transitions to materials.transitions
    - Updates both draft_content.json and draft_info.json
"""

import argparse
import json
import sys
import copy
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

SEC = 1_000_000  # 1 second in microseconds


def load_json(path: Path) -> dict:
    """Load JSON file with UTF-8 encoding."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path: Path, data: dict) -> None:
    """Save JSON file with UTF-8 encoding and proper formatting."""
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def backup_file(path: Path) -> Path:
    """Create a backup of the file before modifying."""
    backup_path = path.with_suffix(path.suffix + '.bak_fix')
    import shutil
    shutil.copy2(path, backup_path)
    logger.info(f"  📦 Backup created: {backup_path.name}")
    return backup_path


def restore_effect_segments(draft_data: dict, template_data: dict, draft_duration_us: int) -> int:
    """
    Restore effect track segments from template.

    Returns:
        Number of effect segments restored
    """
    draft_tracks = draft_data.get('tracks', [])
    template_tracks = template_data.get('tracks', [])

    # Find effect tracks
    draft_effect_track_idx = None
    template_effect_track = None

    for idx, track in enumerate(draft_tracks):
        if track.get('type') == 'effect':
            draft_effect_track_idx = idx
            break

    for track in template_tracks:
        if track.get('type') == 'effect':
            template_effect_track = track
            break

    if draft_effect_track_idx is None:
        logger.warning("  ⚠️  No effect track found in draft")
        return 0

    if template_effect_track is None:
        logger.warning("  ⚠️  No effect track found in template")
        return 0

    template_segments = template_effect_track.get('segments', [])
    if not template_segments:
        logger.warning("  ⚠️  Template effect track has no segments")
        return 0

    # Deep copy effect segments from template
    restored_segments = copy.deepcopy(template_segments)

    # Adjust duration to match draft duration
    for seg in restored_segments:
        target_timerange = seg.get('target_timerange', {})
        if target_timerange:
            # Preserve start time, but extend duration to draft end
            start = target_timerange.get('start', 0)
            target_timerange['duration'] = max(0, draft_duration_us - start)

    # Replace draft effect track segments
    draft_tracks[draft_effect_track_idx]['segments'] = restored_segments

    logger.info(f"  ✅ Restored {len(restored_segments)} effect segment(s)")
    return len(restored_segments)


def restore_effect_materials(draft_data: dict, template_data: dict) -> int:
    """
    Restore video_effects materials from template.

    Returns:
        Number of materials restored
    """
    draft_materials = draft_data.setdefault('materials', {})
    template_materials = template_data.get('materials', {})

    template_video_effects = template_materials.get('video_effects', [])
    if not template_video_effects:
        logger.warning("  ⚠️  No video_effects in template materials")
        return 0

    # Find material IDs referenced by effect segments
    draft_tracks = draft_data.get('tracks', [])
    effect_material_ids = set()

    for track in draft_tracks:
        if track.get('type') == 'effect':
            for seg in track.get('segments', []):
                mat_id = seg.get('material_id')
                if mat_id:
                    effect_material_ids.add(mat_id)

    if not effect_material_ids:
        logger.warning("  ⚠️  No effect material IDs found in segments")
        return 0

    # Copy video_effects materials that are referenced
    draft_video_effects = draft_materials.setdefault('video_effects', [])

    # Remove existing materials with same IDs (to avoid duplicates)
    existing_ids = {mat.get('id') for mat in draft_video_effects}

    restored_count = 0
    for template_mat in template_video_effects:
        mat_id = template_mat.get('id')
        if mat_id in effect_material_ids and mat_id not in existing_ids:
            draft_video_effects.append(copy.deepcopy(template_mat))
            restored_count += 1
            logger.info(f"  📦 Restored material: {template_mat.get('name', mat_id)}")

    logger.info(f"  ✅ Restored {restored_count} video_effects material(s)")
    return restored_count


def add_fade_transitions(draft_data: dict, crossfade_sec: float, image_track_name: str) -> int:
    """
    Add crossfade transitions between consecutive image segments.

    Note: CapCut stores transitions in materials.transitions and references them
    from segment.transitions. This implementation adds Dissolve transitions.

    Returns:
        Number of transitions added
    """
    draft_tracks = draft_data.get('tracks', [])
    draft_materials = draft_data.setdefault('materials', {})

    # Find image track
    image_track = None
    for track in draft_tracks:
        if track.get('name') == image_track_name:
            image_track = track
            break

    if image_track is None:
        logger.warning(f"  ⚠️  Image track '{image_track_name}' not found")
        return 0

    segments = image_track.get('segments', [])
    if len(segments) < 2:
        logger.warning("  ⚠️  Not enough segments for transitions")
        return 0

    crossfade_us = int(crossfade_sec * SEC)
    transitions = draft_materials.setdefault('transitions', [])

    # Generate transition material IDs
    import uuid

    added_count = 0
    for i in range(len(segments) - 1):
        seg = segments[i]
        next_seg = segments[i + 1]

        # Check if segments are consecutive (within 20ms)
        seg_end = seg.get('target_timerange', {}).get('start', 0) + \
                  seg.get('target_timerange', {}).get('duration', 0)
        next_start = next_seg.get('target_timerange', {}).get('start', 0)

        if abs(next_start - seg_end) > int(0.02 * SEC):
            # Segments not consecutive, skip transition
            continue

        # Create transition material (Dissolve)
        transition_id = str(uuid.uuid4()).upper()
        transition_material = {
            "id": transition_id,
            "type": "transition",
            "name": "溶解",  # Dissolve in Japanese
            "category_id": "",
            "category_name": "",
            "duration": crossfade_us,
            "path": "",  # CapCut built-in transitions don't need path
            "platform": "all",
            "resource_id": "6900818577798695432",  # Dissolve effect ID
            "sub_type": 0,
            "version": ""
        }
        transitions.append(transition_material)

        # Add transition reference to segment
        seg_transitions = seg.setdefault('transitions', [])
        seg_transitions.append({
            "id": transition_id,
            "type": "transition",
            "resource_id": "6900818577798695432",
            "duration": crossfade_us
        })

        added_count += 1

    logger.info(f"  ✅ Added {added_count} fade transition(s)")
    return added_count


def sync_draft_info(draft_dir: Path, content_data: dict) -> bool:
    """
    Sync draft_info.json with updated draft_content.json.

    Returns:
        True if successful, False otherwise
    """
    try:
        draft_info_path = draft_dir / "draft_info.json"
        if not draft_info_path.exists():
            logger.warning("  ⚠️  draft_info.json not found")
            return False

        info_data = load_json(draft_info_path)

        # Sync tracks
        info_data['tracks'] = copy.deepcopy(content_data.get('tracks', []))

        # Sync materials
        info_data['materials'] = copy.deepcopy(content_data.get('materials', {}))

        # Sync duration
        info_data['duration'] = content_data.get('duration', 0)

        # Backup and save
        backup_file(draft_info_path)
        save_json(draft_info_path, info_data)

        logger.info("  ✅ Synced draft_info.json")
        return True

    except Exception as e:
        logger.error(f"  ❌ Failed to sync draft_info.json: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Fix CapCut draft effects and transitions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--draft",
        required=True,
        help="Path to CapCut draft directory (e.g., jinsei187_人物一貫性版)"
    )
    parser.add_argument(
        "--template",
        required=True,
        help="Path to template draft directory (e.g., 人生の道標_最新テンプレ)"
    )
    parser.add_argument(
        "--crossfade",
        type=float,
        default=0.5,
        help="Crossfade duration in seconds (default: 0.5)"
    )
    parser.add_argument(
        "--image-track-name",
        default="srt2images_jinsei187_new",
        help="Name of the image track to add transitions (default: srt2images_jinsei187_new)"
    )

    args = parser.parse_args()

    draft_dir = Path(args.draft).resolve()
    template_dir = Path(args.template).resolve()

    # Validate paths
    if not draft_dir.exists():
        logger.error(f"❌ Draft directory not found: {draft_dir}")
        sys.exit(1)

    if not template_dir.exists():
        logger.error(f"❌ Template directory not found: {template_dir}")
        sys.exit(1)

    draft_content_path = draft_dir / "draft_content.json"
    template_content_path = template_dir / "draft_content.json"

    if not draft_content_path.exists():
        logger.error(f"❌ draft_content.json not found in draft: {draft_content_path}")
        sys.exit(1)

    if not template_content_path.exists():
        logger.error(f"❌ draft_content.json not found in template: {template_content_path}")
        sys.exit(1)

    logger.info("🔧 CapCut Draft Effect & Transition Restoration")
    logger.info(f"   Draft: {draft_dir.name}")
    logger.info(f"   Template: {template_dir.name}")
    logger.info(f"   Crossfade: {args.crossfade}s")
    logger.info("")

    # Load JSON files
    logger.info("📖 Loading draft and template...")
    draft_data = load_json(draft_content_path)
    template_data = load_json(template_content_path)

    # Get draft duration
    draft_duration_us = draft_data.get('duration', 0)
    logger.info(f"   Draft duration: {draft_duration_us / SEC:.1f}s")
    logger.info("")

    # Backup draft_content.json
    logger.info("📦 Creating backups...")
    backup_file(draft_content_path)
    logger.info("")

    # Step 1: Restore effect segments
    logger.info("🎨 Step 1: Restoring effect segments...")
    effect_count = restore_effect_segments(draft_data, template_data, draft_duration_us)
    logger.info("")

    # Step 2: Restore effect materials
    logger.info("📦 Step 2: Restoring effect materials...")
    material_count = restore_effect_materials(draft_data, template_data)
    logger.info("")

    # Step 3: Add fade transitions
    logger.info("✨ Step 3: Adding fade transitions...")
    transition_count = add_fade_transitions(draft_data, args.crossfade, args.image_track_name)
    logger.info("")

    # Save updated draft_content.json
    logger.info("💾 Saving updated draft_content.json...")
    save_json(draft_content_path, draft_data)
    logger.info("   ✅ Saved")
    logger.info("")

    # Step 4: Sync draft_info.json
    logger.info("🔄 Step 4: Syncing draft_info.json...")
    sync_draft_info(draft_dir, draft_data)
    logger.info("")

    # Summary
    logger.info("=" * 60)
    logger.info("✅ Restoration Complete")
    logger.info(f"   Effect segments restored: {effect_count}")
    logger.info(f"   Effect materials restored: {material_count}")
    logger.info(f"   Fade transitions added: {transition_count}")
    logger.info(f"   Draft: {draft_dir}")
    logger.info("=" * 60)

    if effect_count == 0 and material_count == 0 and transition_count == 0:
        logger.warning("\n⚠️  No changes were made. Check logs for warnings.")
        sys.exit(1)


if __name__ == "__main__":
    main()
