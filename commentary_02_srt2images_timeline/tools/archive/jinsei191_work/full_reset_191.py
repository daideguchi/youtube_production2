#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess
import json
from pathlib import Path

# Configuration
EPISODE = "191"
INPUT_SRT = Path("input/191_temp.srt")
OUTPUT_DIR = Path(f"output/jinsei{EPISODE}_v3")
DRAFT_NAME = f"人生の道標_{EPISODE}_完成版_最新画像_完全新規"
DRAFT_ROOT = Path(os.path.expanduser("~/Movies/CapCut/User Data/Projects/com.lveditor.draft"))
TEMPLATE_NAME = "CH01-UNK_道標_最新テンプレ"

def run_command(cmd, env=None):
    print(f"Running: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True, env=env)
    except subprocess.CalledProcessError as e:
        print(f"Error running command: {e}")
        sys.exit(1)

def main():
    # 0. Validation
    if not INPUT_SRT.exists():
        print(f"Error: Input SRT {INPUT_SRT} not found!")
        sys.exit(1)
        
    # 1. Cleanup
    print("--- Step 1: Cleanup ---")
    if OUTPUT_DIR.exists():
        print(f"Removing output dir: {OUTPUT_DIR}")
        shutil.rmtree(OUTPUT_DIR)
    
    draft_path = DRAFT_ROOT / DRAFT_NAME
    if draft_path.exists():
        print(f"Removing draft: {draft_path}")
        shutil.rmtree(draft_path)
        
    # Recreate output dir
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    # Copy SRT to output for consistency
    dest_srt = OUTPUT_DIR / f"{EPISODE}.srt"
    shutil.copy(INPUT_SRT, dest_srt)
    
    # 2. Run Pipeline (Image Generation)
    print("--- Step 2: Image Generation ---")
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{os.getcwd()}/src"
    
    cmd = [
        sys.executable, "src/srt2images/cli.py",
        "--srt", str(dest_srt),
        "--out", str(OUTPUT_DIR),
        "--channel", "CH01",
        "--engine", "none", # We will do capcut separately
        "--prompt-template", "templates/jinsei191_masako_strict.txt",
        "--style", "Warm Japanese illustration, calm storytelling, gentle lighting, soft colors, strict character consistency for 62-year-old Masako with realistic portrayals",
        "--nanobanana", "direct",
        "--force",
        "--concurrency", "3",
        "--placeholder-text", "生成中..."
    ]
    run_command(cmd, env)
    
    # 3. Generate Metadata
    print("--- Step 3: Metadata Generation ---")
    
    # 3a. Episode Info
    episode_info = {
        "episode": EPISODE,
        "title": f"人生の道標 {EPISODE}話", # Placeholder title
        "channel": "CH01"
    }
    with open(OUTPUT_DIR / "episode_info.json", "w", encoding="utf-8") as f:
        json.dump(episode_info, f, ensure_ascii=False, indent=2)
        
    # 3b. Extract Chapters
    cmd = [
        sys.executable, "tools/extract_chapter_titles.py",
        str(dest_srt),
        "--output", str(OUTPUT_DIR / "chapters.json")
    ]
    run_command(cmd, env)
    
    # 3c. Belt Config
    cmd = [
        sys.executable, "tools/generate_belt_layers.py",
        "--episode-info", str(OUTPUT_DIR / "episode_info.json"),
        "--chapters", str(OUTPUT_DIR / "chapters.json"),
        "--output", str(OUTPUT_DIR / "belt_config.json")
    ]
    run_command(cmd, env)
    
    # 4. Create CapCut Draft
    print("--- Step 4: CapCut Draft Creation ---")
    cmd = [
        sys.executable, "tools/capcut_bulk_insert.py",
        "--run", str(OUTPUT_DIR),
        "--draft-root", str(DRAFT_ROOT),
        "--template", TEMPLATE_NAME,
        "--new", DRAFT_NAME,
        "--title", f"人生の道標 {EPISODE}話",
        "--srt-file", str(dest_srt),
        "--belt-config", str(OUTPUT_DIR / "belt_config.json"),
        "--tx", "0.0",
        "--ty", "0.0",
        "--scale", "0.99",
        "--opening-offset", "0.0"
    ]
    run_command(cmd, env)
    
    print("\n🎉 Full Reset and Regeneration Complete!")
    print(f"Output: {OUTPUT_DIR}")
    print(f"Draft: {draft_path}")

if __name__ == "__main__":
    main()
