#!/usr/bin/env python3
"""
Regenerate and Swap Images in CapCut Draft (V2 - ID Replacement Logic)
確実に画像を差し替えるための動的ロジック決定版

Why V1 failed: CapCut caches materials by their UUID, even if the file path changes.
Solution V2:
1. Regenerate image with timestamped filename.
2. Generate a NEW UUID for the material.
3. Update the material definition with the new UUID and path.
4. Scan ALL tracks and replace references from the Old UUID to the New UUID.
5. This forces CapCut to treat it as a completely new asset, bypassing all caches.

NOTE:
  - このツールは `video_pipeline.tools.safe_image_swap` からのみ呼び出す（バックアップ/同期/ガード込み）。
  - 直接実行する場合は `SAFE_IMAGE_SWAP_ALLOW=1` が必須（事故防止のため）。

Debug usage (非推奨):
    SAFE_IMAGE_SWAP_ALLOW=1 GEMINI_API_KEY=... PYTHONPATH=".:packages" python3 -m video_pipeline.tools.regenerate_and_swap_v2 \
        --run-dir workspaces/video/runs/<run_id> \
        --draft-path "$HOME/Movies/CapCut/User Data/Projects/com.lveditor.draft/<draft_name>" \
        --indices 6 7 16 19 \
        --style-mode illustration
"""

import argparse
import json
import os
import shutil
import subprocess
import time
import sys
import logging
import uuid
from pathlib import Path

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from video_pipeline.src.srt2images.image_regenerator import ImageRegenerator  # noqa: E402

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

def generate_uuid():
    """Generate a fresh UUID string."""
    return str(uuid.uuid4())

def update_draft_content_with_id_swap(draft_dir: Path, index: int, new_image_path: Path):
    """
    Update CapCut draft content by replacing the material ID.
    This ensures cache invalidation.
    """
    content_json = draft_dir / "draft_content.json"
    if not content_json.exists():
        logger.error(f"Draft content not found: {content_json}")
        return False

    try:
        with open(content_json, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"Failed to load draft content: {e}")
        return False

    materials = data.get('materials', {}).get('videos', [])
    tracks = data.get('tracks', [])
    
    target_index_str = f"{index:04d}"
    
    old_id = None
    new_id = generate_uuid()
    found_material = None

    def _material_suffix(mat: dict) -> str:
        for k in ("path", "material_name"):
            raw = str(mat.get(k) or "").strip()
            if not raw:
                continue
            suf = Path(raw).suffix.lower()
            if suf:
                return suf
        return ""

    def _png_to_mp4(png_path: Path, mp4_path: Path, *, duration_sec: float, width: int, height: int) -> bool:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            logger.error("ffmpeg not found; cannot convert PNG->MP4 for %s", mp4_path.name)
            return False
        duration_sec = float(duration_sec) if duration_sec and duration_sec > 0 else 3.0
        width = int(width) if width and int(width) > 0 else 1920
        height = int(height) if height and int(height) > 0 else 1080

        cmd = [
            ffmpeg,
            "-y",
            "-loop",
            "1",
            "-i",
            str(png_path),
            "-t",
            f"{duration_sec:.3f}",
            "-vf",
            f"scale={width}:{height},format=yuv420p",
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(mp4_path),
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return res.returncode == 0 and mp4_path.exists()
        except Exception as e:
            logger.error("ffmpeg failed: %s", e)
            return False

    # 1. Find the material and update it (photo or video)
    for mat in materials:
        if mat.get('type') not in ('photo', 'video'):
            continue
            
        path = mat.get('path', '')
        name = mat.get('material_name', '')
        
        # Match by filename pattern (e.g. "0006.png" or "0006_v123.png")
        # We check if the current material looks like the one we want to replace
        if target_index_str in Path(path).name or target_index_str in name:
            old_id = mat.get('id')
            found_material = mat
            break
    
    if not found_material or not old_id:
        logger.warning(f"   ⚠️ Material for index {index} not found in draft.")
        return False

    # 2. Copy/convert new image to draft assets
    asset_dir = draft_dir / "assets" / "image"
    asset_dir.mkdir(parents=True, exist_ok=True)
    
    src_suffix = new_image_path.suffix.lower()
    target_suffix = _material_suffix(found_material) or src_suffix

    # CapCut sometimes stores srt2images assets as mp4 (type=video). In that case,
    # convert the PNG into an mp4 still clip that matches the original material's
    # dimensions/duration so timeline behavior stays stable.
    if target_suffix == ".mp4":
        out_name = new_image_path.with_suffix(".mp4").name
        draft_image_path = asset_dir / out_name
        if src_suffix == ".mp4":
            shutil.copy2(new_image_path, draft_image_path)
        else:
            duration_us = found_material.get("duration")
            try:
                duration_sec = float(duration_us) / 1_000_000.0 if duration_us else 0.0
            except Exception:
                duration_sec = 0.0
            ok = _png_to_mp4(
                new_image_path,
                draft_image_path,
                duration_sec=duration_sec,
                width=int(found_material.get("width") or 1920),
                height=int(found_material.get("height") or 1080),
            )
            if not ok:
                logger.error(f"   ❌ Failed to convert PNG->MP4 for index {index}")
                return False
    else:
        # Default: keep as image (PNG/JPG/etc) and copy bytes.
        draft_image_path = asset_dir / new_image_path.name
        shutil.copy2(new_image_path, draft_image_path)
    
    # 3. Update Material Definition
    # We change the ID, Path, Name, and Dimensions
    found_material['id'] = new_id
    found_material['path'] = str(draft_image_path)
    found_material['material_name'] = draft_image_path.name
    
    if target_suffix != ".mp4":
        try:
            from PIL import Image
            with Image.open(new_image_path) as img:
                found_material['width'] = img.width
                found_material['height'] = img.height
        except ImportError:
            pass

    logger.info(f"   🔄 Swapping Material ID: {old_id} -> {new_id}")
    logger.info(f"   📂 New Path: {draft_image_path.name}")

    # 4. Update References in Tracks
    # We must scan all tracks and replace usages of old_id with new_id
    replacement_count = 0
    
    for track in tracks:
        for segment in track.get('segments', []):
            # Check material_id
            if segment.get('material_id') == old_id:
                segment['material_id'] = new_id
                replacement_count += 1
            
            # Check extra_material_refs (sometimes used)
            if 'extra_material_refs' in segment:
                refs = segment['extra_material_refs']
                for i, ref in enumerate(refs):
                    if ref == old_id:
                        refs[i] = new_id
                        replacement_count += 1

    logger.info(f"   🔗 Updated {replacement_count} references in timeline.")

    # 5. Save
    if replacement_count > 0 or found_material:
        # Backup
        shutil.copy2(content_json, str(content_json) + ".bak_swap_v2")
        
        with open(content_json, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            
        # Force reload trigger
        (draft_dir / "draft_info.json").touch()
        return True
    else:
        return False

def main():
    parser = argparse.ArgumentParser(description="Regenerate and Swap Images V2 (ID Swap)")
    parser.add_argument("--run-dir", required=True, help="Output directory containing image_cues.json")
    parser.add_argument("--draft-path", required=True, help="Path to CapCut draft")
    parser.add_argument("--indices", type=int, nargs='+', required=True, help="Image indices to process")
    parser.add_argument("--style-mode", choices=['illustration', 'realistic', 'keep'], default='illustration')
    parser.add_argument(
        "--swap-only",
        action="store_true",
        help="Skip regeneration and only perform ID swap using existing run_dir/images/<index>.png",
    )
    parser.add_argument("--custom-prompt", help="Optional custom prompt instruction")

    args = parser.parse_args()

    if os.environ.get("SAFE_IMAGE_SWAP_ALLOW") != "1":
        logger.error("This tool is locked. Run via tools/safe_image_swap.py (--apply) only.")
        sys.exit(1)

    run_dir = Path(args.run_dir)
    draft_dir = Path(args.draft_path)
    
    if not run_dir.exists() or not draft_dir.exists():
        logger.error("Invalid directories.")
        sys.exit(1)

    # Style definitions
    STYLE_PROMPTS = {
        'illustration': """
STYLE: Warm Japanese digital illustration. Hand-drawn aesthetic, soft textures, gentle lighting.
NEGATIVE: Photorealistic, live action, real photo, 4k photograph, 3D render, uncanny valley.
REQUIREMENT: Character must be an illustration/drawing, NOT a real person.
""",
        'realistic': "STYLE: Cinematic realism.",
        'keep': ""
    }
    
    style_instruction = STYLE_PROMPTS[args.style_mode]
    if args.custom_prompt:
        style_instruction += f"\n{args.custom_prompt}"

    regenerator = None if args.swap_only else ImageRegenerator(run_dir)
    logger.info(f"🚀 V2 Processing {len(args.indices)} images (ID Swap Mode)...")

    success_count = 0
    for idx in args.indices:
        if not args.swap_only:
            logger.info(f"🔄 [Image {idx}] Regenerating...")
            success = regenerator.regenerate_image(
                idx,
                custom_prompt=args.custom_prompt,
                custom_style=style_instruction,
            )
            if not success:
                continue
        else:
            original_path = run_dir / "images" / f"{idx:04d}.png"
            try:
                if not original_path.exists():
                    logger.error(f"❌ [Image {idx}] Missing existing image: {original_path}")
                    continue
                if original_path.stat().st_size < 50 * 1024:
                    logger.error(
                        f"❌ [Image {idx}] Existing image looks like a fallback/placeholder (size < 50KB): {original_path}"
                    )
                    continue
            except Exception as exc:
                logger.error(f"❌ [Image {idx}] Failed to stat existing image: {exc}")
                continue

        # Versioning (always create a fresh filename to avoid CapCut caching by path)
        original_path = run_dir / "images" / f"{idx:04d}.png"
        timestamp = int(time.time())
        new_filename = f"{idx:04d}_v{timestamp}.png"
        new_path = run_dir / "images" / new_filename
        shutil.copy2(original_path, new_path)
        
        # ID Swap Update
        logger.info(f"📝 [Image {idx}] Deep swapping in draft...")
        if update_draft_content_with_id_swap(draft_dir, idx, new_path):
            success_count += 1
            logger.info(f"✅ [Image {idx}] Success")
        else:
            logger.error(f"❌ [Image {idx}] Failed update")

    logger.info("-" * 40)
    logger.info(f"🎉 Finished V2. Updated {success_count}/{len(args.indices)} images.")
    logger.info("👉 REOPEN CapCut to see changes (Cache bypassed via ID swap).")
    if success_count != len(args.indices):
        logger.error("Not all images were updated. Exiting with error.")
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
