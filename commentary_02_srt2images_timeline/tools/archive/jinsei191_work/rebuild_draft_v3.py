#!/usr/bin/env python3
import sys
import os
import subprocess
import json
from pathlib import Path

# Configuration
OUTPUT_DIR = Path("output/jinsei191_3")
IMAGES_DIR = OUTPUT_DIR / "images"
CUES_JSON = OUTPUT_DIR / "image_cues.json"
DRAFT_ROOT = Path(os.path.expanduser("~/Movies/CapCut/User Data/Projects/com.lveditor.draft"))
TEMPLATE_NAME = "CH01-UNK_道標_最新テンプレ"
# New draft name (Version 3 to be absolutely safe)
NEW_DRAFT_NAME = "人生の道標_191_3_修正版_V3"

def main():
    print(f"🚀 Rebuilding draft using LATEST images in directory...")
    
    if not OUTPUT_DIR.exists():
        print(f"Error: {OUTPUT_DIR} not found")
        sys.exit(1)
        
    # 1. Update image_cues.json to point to the latest _vTimestamp.png files
    print("🔄 Updating image_cues.json with latest files...")
    with open(CUES_JSON, 'r', encoding='utf-8') as f:
        cues_data = json.load(f)
        
    updated_count = 0
    for cue in cues_data['cues']:
        idx = cue['index']
        # Look for latest file pattern: 0006_v*.png or 0006.png
        pattern = f"{idx:04d}*"
        files = list(IMAGES_DIR.glob(f"{idx:04d}*.png"))
        
        if not files:
            continue
            
        # Sort by modification time (newest first)
        latest_file = sorted(files, key=lambda x: x.stat().st_mtime, reverse=True)[0]
        
        # Update path in JSON (absolute path is safer for external tools)
        # cues_data uses relative or absolute? capcut_bulk_insert expects structure.
        # It usually ignores 'image_path' in json and looks in images dir, 
        # BUT let's be safe and update it if present, OR rely on bulk_insert's logic.
        
        # Actually, capcut_bulk_insert.py line 438:
        # img = images_dir / f"{i+1:04d}.png"
        # It HARDCODES the filename lookup to 000X.png!
        # This is why timestamped files are ignored by bulk_insert.
        
        # SOLUTION: We must RENAME the latest timestamped file back to 000X.png 
        # effectively "committing" the change.
        
        target_name = f"{idx:04d}.png"
        target_path = IMAGES_DIR / target_name
        
        if latest_file.name != target_name:
            print(f"   Commit: {latest_file.name} -> {target_name}")
            # Backup original just in case? No, we are committing to fix.
            # Copy content of latest to target
            import shutil
            shutil.copy2(latest_file, target_path)
            updated_count += 1
            
    print(f"✅ Committed {updated_count} updated images to standard filenames.")

    # 2. Run capcut_bulk_insert.py to create FRESH draft
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
    
    print(f"Running builder: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        print(f"\n🎉 Created FRESH draft: {NEW_DRAFT_NAME}")
        print(f"📂 Location: {DRAFT_ROOT / NEW_DRAFT_NAME}")
        print("👉 Open THIS draft. It was built from scratch using the fixed images.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to create draft: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
