#!/usr/bin/env python3
"""
CapCut Draft Transition Fix Tool V2

Purpose:
    Add proper fade transitions between image segments using correct CapCut JSON structure.

    CRITICAL FIXES:
    1. Use 'transition' (singular) field in segment, not 'transitions' array
    2. Add transition object ID to segment's extra_material_refs
    3. Create proper Transition material structure in materials.transitions

Usage:
    python3 tools/fix_capcut_transitions_v2.py \
        --draft "/path/to/draft" \
        --crossfade 0.5 \
        --image-track-name "srt2images_jinsei187_new"
"""

import argparse
import json
import sys
import uuid
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
    backup_path = path.with_suffix(path.suffix + '.bak_trans_v2')
    import shutil
    shutil.copy2(path, backup_path)
    logger.info(f"  📦 Backup created: {backup_path.name}")
    return backup_path


def create_dissolve_transition(duration_us: int) -> dict:
    """
    Create a Dissolve transition material object.

    Based on pyJianYingDraft CapCut_Transition_type.Dissolve:
    - resource_id: "6724846004274729480"
    - effect_id: "392FD26E-A514-4d0f-8950-EA4A20CB407C" (Dissolve variant 1)
    - is_overlap: False

    Args:
        duration_us: Transition duration in microseconds

    Returns:
        Transition material dict ready for materials.transitions
    """
    transition_id = str(uuid.uuid4()).upper()

    return {
        "id": transition_id,
        "type": "transition",
        "name": "Dissolve",
        "category_id": "",
        "category_name": "",
        "duration": duration_us,
        "effect_id": "392FD26E-A514-4d0f-8950-EA4A20CB407C",  # CapCut Dissolve
        "resource_id": "6724846004274729480",
        "is_overlap": False,
        "platform": "all"
    }


def add_transition_to_segment(segment: dict, transition_material: dict, force: bool = False) -> bool:
    """
    Add transition reference to a segment.

    CRITICAL: Use 'transition' (singular) field, not 'transitions' array.

    Args:
        segment: Segment dict to modify
        transition_material: Transition material dict with 'id' field
        force: If True, replace existing transition

    Returns:
        True if transition was added, False if segment already has one and force=False
    """
    # Remove old transition if force=True
    if force and segment.get('transition') is not None:
        old_trans_id = segment['transition'].get('id')
        # Remove from extra_material_refs
        extra_refs = segment.get('extra_material_refs', [])
        if old_trans_id in extra_refs:
            extra_refs.remove(old_trans_id)
        # Remove transition field
        del segment['transition']
    elif segment.get('transition') is not None:
        return False

    transition_id = transition_material['id']

    # Add transition field (singular, not array!)
    segment['transition'] = {
        "id": transition_id,
        "type": "transition",
        "resource_id": transition_material['resource_id'],
        "effect_id": transition_material['effect_id'],
        "duration": transition_material['duration'],
        "is_overlap": transition_material['is_overlap']
    }

    # Add to extra_material_refs
    extra_refs = segment.setdefault('extra_material_refs', [])
    if transition_id not in extra_refs:
        extra_refs.append(transition_id)

    return True


def add_fade_transitions(draft_data: dict, crossfade_sec: float, image_track_name: str) -> int:
    """
    Add crossfade transitions between consecutive image segments.

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
    transitions_materials = draft_materials.setdefault('transitions', [])

    # Clear old transitions (from previous implementations)
    # We'll rebuild them from scratch
    transitions_materials.clear()

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

        # Create transition material
        transition_material = create_dissolve_transition(crossfade_us)
        transitions_materials.append(transition_material)

        # Add transition to segment (force=True to replace existing)
        if add_transition_to_segment(seg, transition_material, force=True):
            added_count += 1
        else:
            # Remove the material we just added since segment already has transition
            transitions_materials.pop()

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
        info_data['tracks'] = content_data.get('tracks', [])

        # Sync materials
        info_data['materials'] = content_data.get('materials', {})

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
        description="Fix CapCut draft transitions (V2 - correct structure)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--draft",
        required=True,
        help="Path to CapCut draft directory (e.g., jinsei187_人物一貫性版)"
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

    # Validate paths
    if not draft_dir.exists():
        logger.error(f"❌ Draft directory not found: {draft_dir}")
        sys.exit(1)

    draft_content_path = draft_dir / "draft_content.json"

    if not draft_content_path.exists():
        logger.error(f"❌ draft_content.json not found in draft: {draft_content_path}")
        sys.exit(1)

    logger.info("🔧 CapCut Draft Transition Fix V2")
    logger.info(f"   Draft: {draft_dir.name}")
    logger.info(f"   Crossfade: {args.crossfade}s")
    logger.info("")

    # Load JSON files
    logger.info("📖 Loading draft...")
    draft_data = load_json(draft_content_path)

    # Get draft duration
    draft_duration_us = draft_data.get('duration', 0)
    logger.info(f"   Draft duration: {draft_duration_us / SEC:.1f}s")
    logger.info("")

    # Backup draft_content.json
    logger.info("📦 Creating backups...")
    backup_file(draft_content_path)
    logger.info("")

    # Add fade transitions
    logger.info("✨ Adding fade transitions (correct structure)...")
    transition_count = add_fade_transitions(draft_data, args.crossfade, args.image_track_name)
    logger.info("")

    # Save updated draft_content.json
    logger.info("💾 Saving updated draft_content.json...")
    save_json(draft_content_path, draft_data)
    logger.info("   ✅ Saved")
    logger.info("")

    # Sync draft_info.json
    logger.info("🔄 Syncing draft_info.json...")
    sync_draft_info(draft_dir, draft_data)
    logger.info("")

    # Summary
    logger.info("=" * 60)
    logger.info("✅ Transition Fix Complete")
    logger.info(f"   Fade transitions added: {transition_count}")
    logger.info(f"   Draft: {draft_dir}")
    logger.info("=" * 60)

    if transition_count == 0:
        logger.warning("\n⚠️  No changes were made. Check logs for warnings.")
        sys.exit(1)


if __name__ == "__main__":
    main()
