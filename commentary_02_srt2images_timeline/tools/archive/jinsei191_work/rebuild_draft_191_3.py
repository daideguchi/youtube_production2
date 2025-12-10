#!/usr/bin/env python3
import sys
import os
import subprocess
from pathlib import Path

# Configuration
OUTPUT_DIR = Path("output/jinsei191_3")
DRAFT_ROOT = Path(os.path.expanduser("~/Movies/CapCut/User Data/Projects/com.lveditor.draft"))
TEMPLATE_NAME = "CH01-UNK_道標_最新テンプレ"
# Create a distinct new draft name to ensure no cache issues
NEW_DRAFT_NAME = "人生の道標_191_3_修正確認用_FINAL"

def main():
    print(f"🚀 Rebuilding draft to ensure updated images are used...")
    
    # Ensure output dir exists
    if not OUTPUT_DIR.exists():
        print(f"Error: {OUTPUT_DIR} does not exist")
        sys.exit(1)

    # Run capcut_bulk_insert.py
    cmd = [
        sys.executable,
        "tools/capcut_bulk_insert.py",
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
    
    print(f"Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        print(f"\n✅ Created fresh draft: {NEW_DRAFT_NAME}")
        print(f"📂 Location: {DRAFT_ROOT / NEW_DRAFT_NAME}")
        print("👉 Please open THIS specific draft in CapCut to see the changes.")
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to create draft: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
