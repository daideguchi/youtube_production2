#!/usr/bin/env python3
"""
CapCut Draft Fade Animation Tool

Purpose:
    Add Fade In and Fade Out animations to all image segments.

    CapCut implements fades as ANIMATIONS (intro/outro), NOT transitions.
    - Fade In: Intro animation (start of clip)
    - Fade Out: Outro animation (end of clip)

Usage:
    python3 tools/add_fade_animations.py \
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


# CapCut Fade_In animation metadata
FADE_IN_META = {
    "category_id": "",
    "category_name": "",
    "duration": 500000,  # 0.5 seconds
    "is_ai_generate_content": False,
    "material_type": "",
    "name": "Fade In",
    "platform": "all",
    "request_id": "",
    "resource_id": "6846887950582568456",
    "resource_name": "",
    "source_platform": 0,
    "type": "video_animation"
}

# CapCut Fade_Out animation metadata
FADE_OUT_META = {
    "category_id": "",
    "category_name": "",
    "duration": 500000,  # 0.5 seconds
    "is_ai_generate_content": False,
    "material_type": "",
    "name": "Fade Out",
    "platform": "all",
    "request_id": "",
    "resource_id": "6846889021381182989",
    "resource_name": "",
    "source_platform": 0,
    "type": "video_animation"
}


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
    backup_path = path.with_suffix(path.suffix + '.bak_fade')
    import shutil
    shutil.copy2(path, backup_path)
    logger.info(f"  📦 Backup created: {backup_path.name}")
    return backup_path


def create_segment_animations(fade_in_duration_us: int, fade_out_duration_us: int,
                              segment_duration_us: int) -> dict:
    """
    Create Segment_animations object with Fade In and Fade Out.

    Structure:
    {
      "id": "ANIMATION_ID",
      "type": "sticker_animation",
      "animations": [
        {
          "anim_adjust_params": [],
          "duration": 500000,
          "id": "FADE_IN_ID",
          "start": 0,  # Start at beginning
          "...": fade_in_meta
        },
        {
          "anim_adjust_params": [],
          "duration": 500000,
          "id": "FADE_OUT_ID",
          "start": <segment_duration - fade_out_duration>,  # Start at end
          "...": fade_out_meta
        }
      ]
    }
    """
    animation_id = str(uuid.uuid4()).upper()
    fade_in_id = str(uuid.uuid4()).upper()
    fade_out_id = str(uuid.uuid4()).upper()

    fade_in_anim = {
        "anim_adjust_params": [],
        "category_id": FADE_IN_META["category_id"],
        "category_name": FADE_IN_META["category_name"],
        "duration": fade_in_duration_us,
        "id": fade_in_id,
        "is_ai_generate_content": False,
        "material_type": "",
        "name": FADE_IN_META["name"],
        "panel": "video",
        "platform": "all",
        "request_id": "",
        "resource_id": FADE_IN_META["resource_id"],
        "resource_name": "",
        "source_platform": 0,
        "start": 0,  # Fade in starts at beginning
        "type": "video_animation"
    }

    fade_out_anim = {
        "anim_adjust_params": [],
        "category_id": FADE_OUT_META["category_id"],
        "category_name": FADE_OUT_META["category_name"],
        "duration": fade_out_duration_us,
        "id": fade_out_id,
        "is_ai_generate_content": False,
        "material_type": "",
        "name": FADE_OUT_META["name"],
        "panel": "video",
        "platform": "all",
        "request_id": "",
        "resource_id": FADE_OUT_META["resource_id"],
        "resource_name": "",
        "source_platform": 0,
        "start": max(0, segment_duration_us - fade_out_duration_us),  # Fade out at end
        "type": "video_animation"
    }

    return {
        "id": animation_id,
        "type": "sticker_animation",
        "animations": [fade_in_anim, fade_out_anim],
        "multi_language_current": "none"
    }


def add_fade_animations_to_segments(draft_data: dict, fade_duration_sec: float,
                                    image_track_name: str) -> int:
    """
    Add Fade In and Fade Out animations to all image segments.

    Returns:
        Number of segments with animations added
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
    if len(segments) == 0:
        logger.warning("  ⚠️  No segments found")
        return 0

    fade_duration_us = int(fade_duration_sec * SEC)
    material_animations = draft_materials.setdefault('material_animations', [])

    added_count = 0
    for seg in segments:
        segment_duration_us = seg.get('target_timerange', {}).get('duration', 0)

        if segment_duration_us == 0:
            continue

        # Create Segment_animations object
        anim_obj = create_segment_animations(
            fade_duration_us,
            fade_duration_us,
            segment_duration_us
        )

        # Add to materials
        material_animations.append(anim_obj)

        # Add animation ID to segment's extra_material_refs
        extra_refs = seg.setdefault('extra_material_refs', [])
        animation_id = anim_obj['id']

        # Remove old animation refs (if any)
        # Keep only non-animation refs
        old_refs = extra_refs.copy()
        extra_refs.clear()
        for ref in old_refs:
            # Check if ref is an animation
            is_animation = any(a.get('id') == ref for a in material_animations)
            if not is_animation:
                extra_refs.append(ref)

        # Add new animation ref
        extra_refs.append(animation_id)

        added_count += 1

    logger.info(f"  ✅ Added fade animations to {added_count} segment(s)")
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
        description="Add Fade In/Fade Out animations to CapCut draft images",
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
        help="Fade duration in seconds (default: 0.5)"
    )
    parser.add_argument(
        "--image-track-name",
        default="srt2images_jinsei187_new",
        help="Name of the image track (default: srt2images_jinsei187_new)"
    )

    args = parser.parse_args()

    draft_dir = Path(args.draft).resolve()

    # Validate paths
    if not draft_dir.exists():
        logger.error(f"❌ Draft directory not found: {draft_dir}")
        sys.exit(1)

    draft_content_path = draft_dir / "draft_content.json"

    if not draft_content_path.exists():
        logger.error(f"❌ draft_content.json not found: {draft_content_path}")
        sys.exit(1)

    logger.info("🎨 CapCut Fade Animation Tool")
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

    # Add fade animations
    logger.info("✨ Adding Fade In / Fade Out animations...")
    added_count = add_fade_animations_to_segments(
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
    logger.info("✅ Fade Animation Complete")
    logger.info(f"   Segments with fade: {added_count}")
    logger.info(f"   Draft: {draft_dir}")
    logger.info("=" * 60)

    if added_count == 0:
        logger.warning("\n⚠️  No changes were made. Check logs for warnings.")
        sys.exit(1)


if __name__ == "__main__":
    main()
