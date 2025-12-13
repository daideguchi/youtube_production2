#!/usr/bin/env python3
import sys
import shutil
import os
from pathlib import Path
import subprocess

# Configuration
OUTPUT_DIR = Path("output/jinsei191_3")
IMAGES_DIR = OUTPUT_DIR / "images"
DRAFT_ROOT = Path(os.path.expanduser("~/Movies/CapCut/User Data/Projects/com.lveditor.draft"))
TEMPLATE_NAME = "CH01-UNK_道標_最新テンプレ"
NEW_DRAFT_NAME = "人生の道標_191_3_緊急修正_完全版"

# Ensure src is in path
root_dir = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_dir))

try:
    from src.srt2images.image_regenerator import ImageRegenerator
except ImportError:
    sys.path.insert(0, str(Path.cwd()))
    from src.srt2images.image_regenerator import ImageRegenerator

def main():
    print("🚨 STARTING EMERGENCY RESCUE FOR 191_3 🚨")
    
    if not IMAGES_DIR.exists():
        print(f"Error: {IMAGES_DIR} not found")
        sys.exit(1)

    regenerator = ImageRegenerator(OUTPUT_DIR)

    # 1. DEFINE TARGETS AND STYLES
    # Indices to fix: 6, 7, 14, 16, 19, 27 (and 1 for good measure)
    
    masako_style = """
    STYLE: Japanese digital illustration, soft watercolor style, hand-drawn lines.
    CHARACTER: Masako (62yo woman), gentle face, short hair, simple clothes.
    NEGATIVE: Photorealistic, 3D render, photograph, scary, horror, dark, messy, distorted face.
    NOTE: Must look like a friendly, warm illustration.
    """

    tasks = {
        # 1: Impact opening
        1: f"""
        SCENE: Opening title shot.
        VISUAL: A beautiful, artistic silhouette of an elderly woman looking at a warm sunset.
        STYLE: High-quality anime background art style. Atmospheric.
        NEGATIVE: Photorealistic, blurry.
        """,
        
        # 6: Supermarket mistake
        6: f"""
        SCENE: Masako bowing at supermarket.
        VISUAL: Masako (illustrated character) apologizing with a bowed head. A young colleague looks on kindly.
        {masako_style}
        """,
        
        # 7: Bus sadness
        7: f"""
        SCENE: Masako on bus, sad.
        VISUAL: Masako sitting on a bus seat. City lights outside are painted as soft colorful blobs (bokeh). Her expression is melancholic but soft.
        {masako_style}
        """,
        
        # 14: Metaphor (Fog lifting) - FIXING "CREEPY"
        14: f"""
        SCENE: Metaphor of relief.
        VISUAL: A soft, pastel illustration of Masako taking a deep breath. Soft white mist is clearing away from her, revealing a blue sky.
        ATMOSPHERE: Peaceful, airy, light.
        NEGATIVE: Spiderwebs, nets, dark threads, horror elements, scary faces.
        {masako_style}
        """,
        
        # 16: Writing list
        16: f"""
        SCENE: Writing on paper.
        VISUAL: Close up illustration of hands holding a pen. Simple, clean lines. Masako's face is calm.
        {masako_style}
        """,
        
        # 19: Bus happy
        19: f"""
        SCENE: Masako on bus, happy.
        VISUAL: Masako on the bus, smiling gently at her reflection. Warm colors.
        {masako_style}
        """,
        
        # 27: Word Karma - FIXING "CREEPY/REALISTIC"
        27: f"""
        SCENE: Metaphor for 'Word Karma'.
        VISUAL: A fantasy illustration. A silhouette of a person blowing gently, and their breath turns into golden glowing sparkles/flowers that float to the ground.
        STYLE: Storybook art, ethereal, magical.
        NEGATIVE: Body horror, seeds coming out of mouth, realistic mouth, grotesque.
        """
    }

    # 2. DELETE OLD FILES AND REGENERATE
    print(f"🗑️  Deleting old files and regenerating {len(tasks)} images...")
    
    for idx, prompt in tasks.items():
        # Delete all variants
        for f in IMAGES_DIR.glob(f"{idx:04d}*.png"):
            try:
                f.unlink()
            except:
                pass
        
        print(f"   🔄 Regenerating {idx}...")
        success = regenerator.regenerate_image(idx, custom_prompt=prompt)
        
        # Verify file exists
        target_file = IMAGES_DIR / f"{idx:04d}.png"
        if success and target_file.exists():
            print(f"      ✅ Created: {target_file.name}")
        else:
            print(f"      ❌ FAILED: {idx}")

    # 3. BUILD NEW DRAFT
    print("\n🏗️  Building NEW CapCut Draft...")
    cmd = [
        sys.executable, "tools/capcut_bulk_insert.py",
        "--run", str(OUTPUT_DIR),
        "--draft-root", str(DRAFT_ROOT),
        "--template", TEMPLATE_NAME,
        "--new", NEW_DRAFT_NAME,
        "--title", "人生の道標 191話",
        "--srt-file", str(OUTPUT_DIR / "191_3.srt"),
        "--belt-config", str(OUTPUT_DIR / "belt_config.json"),
        "--tx", "0.0",
        "--ty", "0.0",
        "--scale", "0.99",
        "--opening-offset", "0.0"
    ]
    
    subprocess.run(cmd, check=True)
    
    print(f"\n🎉 EMERGENCY RESCUE COMPLETE")
    print(f"📂 Draft: {DRAFT_ROOT / NEW_DRAFT_NAME}")
    print("👉 Please open '人生の道標_191_3_緊急修正_完全版'")

if __name__ == "__main__":
    main()
