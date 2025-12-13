#!/usr/bin/env python3
import os
import sys
import shutil
import subprocess
import json
from pathlib import Path

# Configuration
EPISODE_ID = "191_3"
EPISODE_NUM = "191" # Display number
INPUT_SRT = Path("input/CH01_人生の道標/191_3.srt")
OUTPUT_DIR = Path(f"output/jinsei{EPISODE_ID}")
DRAFT_NAME = f"人生の道標_{EPISODE_ID}_完成版"
DRAFT_ROOT = Path(os.path.expanduser("~/Movies/CapCut/User Data/Projects/com.lveditor.draft"))
TEMPLATE_NAME = "CH01-UNK_道標_最新テンプレ"

def run_command(cmd, env=None):
    print(f"Running: {" ".join(cmd)}")
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
        
    # 1. Cleanup (only if output dir exists, to start fresh as requested)
    print("--- Step 1: Setup Output Directory ---")
    if OUTPUT_DIR.exists():
        print(f"Cleaning existing output dir: {OUTPUT_DIR}")
        shutil.rmtree(OUTPUT_DIR)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    
    draft_path = DRAFT_ROOT / DRAFT_NAME
    if draft_path.exists():
        print(f"Removing existing draft: {draft_path}")
        shutil.rmtree(draft_path)

    # Copy SRT to output
    dest_srt = OUTPUT_DIR / f"{EPISODE_ID}.srt"
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
        "--engine", "none", 
        "--prompt-template", "templates/jinsei_warm_gold_blue_strict.txt",
        "--style", "Warm Japanese illustration, calm storytelling, gentle lighting, soft colors, strict character consistency for 62-year-old Masako with realistic portrayals",
        "--nanobanana", "direct",
        "--concurrency", "3",
        "--placeholder-text", "生成中..."
    ]
    run_command(cmd, env)
    
    # 3. Generate Metadata
    print("--- Step 3: Metadata Generation ---")
    
    # 3a. Episode Info
    episode_info = {
        "episode": EPISODE_NUM,
        "title": f"人生の道標 {EPISODE_NUM}話", 
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
        "--title", f"人生の道標 {EPISODE_NUM}話",
        "--srt-file", str(dest_srt),
        "--belt-config", str(OUTPUT_DIR / "belt_config.json"),
        "--tx", "0.0",
        "--ty", "0.0",
        "--scale", "0.99",
        "--opening-offset", "0.0"
    ]
    run_command(cmd, env)
    
    print(f"\n🎉 Process Complete for {EPISODE_ID}!")
    print(f"Output: {OUTPUT_DIR}")
    print(f"Draft: {draft_path}")

if __name__ == "__main__":
    main()
