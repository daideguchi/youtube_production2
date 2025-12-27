#!/usr/bin/env python3
"""
CapCut Fade Transition Fix - CORRECT Implementation

Purpose:
    Add crossfade transitions between image segments using the CORRECT CapCut structure.

    CRITICAL DISCOVERY:
    - Fades are NOT material_animations
    - Fades are NOT intro/outro animations
    - Fades ARE transitions with resource_id "6724845717472416269" (叠化 = crossfade)
    - Transition is added to THE NEXT SEGMENT's extra_material_refs

    Structure discovered from working draft (人生の道標_185_完成版):
    1. materials.transitions array contains transition objects
    2. Next segment's extra_material_refs contains [speed_id, transition_id]
    3. is_overlap: true is critical

Usage:
    PYTHONPATH=".:packages" python3 -m video_pipeline.tools.fix_fade_transitions_correct \
        --draft "/path/to/draft" \
        --fade-duration 0.5 \
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
    backup_path = path.with_suffix(path.suffix + '.bak_fade_correct')
    import shutil
    shutil.copy2(path, backup_path)
    logger.info(f"  📦 Backup created: {backup_path.name}")
    return backup_path


def create_crossfade_transition(duration_us: int) -> dict:
    """
    Create CapCut crossfade transition object.

    Based on working draft analysis:
    - name: "叠化" (Chinese for "crossfade/dissolve")
    - effect_id: "322577"
    - resource_id: "6724845717472416269"
    - is_overlap: true (CRITICAL)
    """
    transition_id = str(uuid.uuid4()).replace('-', '')

    return {
        "category_id": "",
        "category_name": "",
        "duration": duration_us,
        "effect_id": "322577",
        "id": transition_id,
        "is_overlap": True,  # CRITICAL for crossfade
        "name": "叠化",
        "platform": "all",
        "resource_id": "6724845717472416269",
        "type": "transition"
    }


def add_crossfade_transitions(draft_data: dict, fade_duration_sec: float,
                              image_track_name: str) -> int:
    """
    Add crossfade transitions between consecutive image segments.

    CORRECT Implementation:
    1. Create transition in materials.transitions
    2. Add transition ID to NEXT segment's extra_material_refs
    3. First segment has no transition (no incoming fade)

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

    fade_duration_us = int(fade_duration_sec * SEC)
    transitions = draft_materials.setdefault('transitions', [])

    # Clear old transitions (will rebuild)
    transitions.clear()

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
            logger.debug(f"  Skipping transition {i}->{i+1} (gap: {(next_start - seg_end) / SEC:.3f}s)")
            continue

        # Create transition
        transition = create_crossfade_transition(fade_duration_us)
        transitions.append(transition)

        # Add to NEXT segment's extra_material_refs
        next_refs = next_seg.setdefault('extra_material_refs', [])

        # Remove old transition refs (keep only speed refs)
        # Speed IDs are 32-char hex without dashes
        # Transition IDs are 32-char hex without dashes too, but we'll rebuild all
        old_refs = next_refs.copy()
        next_refs.clear()

        # Keep speed ref (should be first in array)
        for ref in old_refs:
            # Check if it's a speed by looking in materials.speeds
            speeds = draft_materials.get('speeds', [])
            if any(s.get('id') == ref for s in speeds):
                next_refs.append(ref)
                break

        # Add transition ID
        next_refs.append(transition['id'])

        added_count += 1
        logger.debug(f"  Added crossfade {i}->{i+1} (transition ID: {transition['id']})")

    logger.info(f"  ✅ Added {added_count} crossfade transition(s)")
    return added_count


def sync_draft_info(draft_dir: Path, content_data: dict) -> bool:
    """Sync draft_info.json with updated draft_content.json."""
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
        description="Fix CapCut draft fade transitions (CORRECT implementation)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument(
        "--draft",
        required=True,
        help="Path to CapCut draft directory (e.g., jinsei187_人物一貫性版)"
    )
    parser.add_argument(
        "--fade-duration",
        type=float,
        default=0.5,
        help="Crossfade duration in seconds (default: 0.5)"
    )
    parser.add_argument(
        "--image-track-name",
        default="srt2images_jinsei187_new",
        help="Name of the image track (default: srt2images_jinsei187_new)"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    draft_dir = Path(args.draft).resolve()

    # Validate paths
    if not draft_dir.exists():
        logger.error(f"❌ Draft directory not found: {draft_dir}")
        sys.exit(1)

    draft_content_path = draft_dir / "draft_content.json"

    if not draft_content_path.exists():
        logger.error(f"❌ draft_content.json not found: {draft_content_path}")
        sys.exit(1)

    logger.info("🔧 CapCut Fade Transition Fix (CORRECT Implementation)")
    logger.info(f"   Draft: {draft_dir.name}")
    logger.info(f"   Fade duration: {args.fade_duration}s")
    logger.info("")

    # Load JSON files
    logger.info("📖 Loading draft...")
    draft_data = load_json(draft_content_path)
    logger.info("")

    # Backup draft_content.json
    logger.info("📦 Creating backups...")
    backup_file(draft_content_path)
    logger.info("")

    # Add crossfade transitions
    logger.info("✨ Adding crossfade transitions (correct structure)...")
    added_count = add_crossfade_transitions(
        draft_data,
        args.fade_duration,
        args.image_track_name
    )
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
    logger.info("✅ Crossfade Transition Fix Complete")
    logger.info(f"   Transitions added: {added_count}")
    logger.info(f"   Draft: {draft_dir}")
    logger.info("=" * 60)

    if added_count == 0:
        logger.warning("\n⚠️  No changes were made. Check logs for warnings.")
        sys.exit(1)


if __name__ == "__main__":
    main()
