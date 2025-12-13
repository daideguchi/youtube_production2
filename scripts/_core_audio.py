#!/usr/bin/env python3
"""
[INTERNAL] Core Audio Logic
===========================
WARNING: DO NOT CALL THIS SCRIPT DIRECTLY.
Use the fixed Entry Points:
  1. Route 1 (Batch/API):  python scripts/run_route1_batch.py
  2. Route 2 (Agent/User): python scripts/run_route2_agent.py

This script unifies the logic but should be controlled via the above wrappers.
"""
import argparse
import sys
import subprocess
from pathlib import Path
import os
import time

from factory_common.paths import repo_root, script_data_root

def main():
    # [AMBIGUITY GUARD] Check if called via wrapper
    if not os.getenv("IS_WRAPPER_CALL"):
        print("\n[WARNING] You are running _core_audio.py directly!")
        print("          Please use strict entry points:")
        print("          - scripts/run_route2_agent.py (Agent/Manual)")
        print("          - scripts/run_route1_batch.py (Batch/API)\n")
        # We don't exit to allow force debugging, but we warn heavily.
        time.sleep(2)

    parser = argparse.ArgumentParser(description="Generate Audio (Unified Script)")
    parser.add_argument("--channel", required=True, help="Channel ID (e.g. CH05)")
    
    # ID Selection (Single or Range)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video", "--id", help="Single Video ID (e.g. 001)")
    group.add_argument("--start", type=int, help="Start Video ID for range")
    
    parser.add_argument("--end", type=int, help="End Video ID for range (inclusive)")

    # Mode Selection (Explicit User Request)
    parser.add_argument("--mode", choices=["interactive", "batch"], default="batch",
                       help="Operation Mode: 'interactive' (Manual Confirm) or 'batch' (Non-Interactive/Auto). Default: batch")
    parser.add_argument("--interactive", action="store_true", help="Alias for --mode interactive")
    parser.add_argument("--force", action="store_true", help="Force regeneration even if output exists")
    parser.add_argument("--skip-annotation", action="store_true", help="Skip LLM Audit (Pass-through to run_tts)")
    
    args = parser.parse_args()
    
    # Resolve Mode
    # If explicit --interactive flag is used, it overrides --mode default
    mode = "interactive" if args.interactive else args.mode
    
    # Determine target list
    targets = []
    if args.video:
        targets.append(args.video)
    else:
        if args.end is None:
            targets.append(f"{args.start:03d}")
        else:
            for i in range(args.start, args.end + 1):
                targets.append(f"{i:03d}")

    # Base Paths
    project_root = repo_root()
    script_pipeline_root = script_data_root()
    
    print(f"=== Audio Generation: {args.channel} ({len(targets)} videos) ===")
    if mode == "interactive":
        print(">> MODE: INTERACTIVE (AI Agent / エージェント推論モード)")
        print("   - Agentic Twin-Engine Audit (High Quality).")
        print("   - 'AIエージェントが考える'")
    else:
        print(">> MODE: NON-INTERACTIVE (API / API処理モード)")
        print("   - Standard Processing.")
        print("   - 'APIのLLMが考える'")

    success_count = 0
    fail_count = 0

    for video_id in targets:
        # Normalize ID
        if video_id.isdigit():
            video_id = f"{int(video_id):03d}"
            
        print(f"\n--- Processing {args.channel}-{video_id} ---")
        
        # 1. Locate Input File (assembled.md)
        input_file = script_pipeline_root / args.channel / video_id / "content" / "assembled.md"
        if not input_file.exists():
            # Fallback
            input_file_alt = script_pipeline_root / args.channel / video_id / "audio_prep" / "script_corrected.txt"
            if input_file_alt.exists():
                input_file = input_file_alt
            else:
                print(f"[SKIP] Input not found: {input_file}")
                fail_count += 1
                continue
        
        print(f"Input: {input_file}")
        


        # 2. Construct Command
        cmd = [
            sys.executable, "-m", "audio_tts_v2.scripts.run_tts", 
            "--channel", args.channel,
            "--video", video_id,
            "--input", str(input_file),
            "--mode", "interactive" if mode == "interactive" else "auto"
        ]
        
        if args.skip_annotation:
            cmd.append("--skip-annotation")
        
        # Mode: Interactive (Agent) now implies Automatic Agent execution, NOT manual pause.
        # However, for Route 2 (Human Operator), successful "Manual Progress" might imply
        # we handle the loop here?
        # User said: "I (User) operate 2. Agent runs manually while processing."
        # Basically, we just run the script.
        
        if mode == "interactive":
            # print("Ready to run pipeline...")
            pass

        # env setup
        env = os.environ.copy()
        packages_dir = project_root / "packages"
        env["PYTHONPATH"] = f"{project_root}:{packages_dir}"
        
        try:
            # Run
            res = subprocess.run(cmd, cwd=project_root, env=env, check=True)
            success_count += 1
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Pipeline failed for {video_id}")
            fail_count += 1
            if args.interactive:
                # If we are in interactive mode, we might want to ask user whether to continue?
                # But since I am the operator, I can see the log and decide.
                pass
        except KeyboardInterrupt:
            print("\n[ABORT] User skipped.")
            break

    print(f"\n=== Summary: Success {success_count}, Failed {fail_count} ===")

if __name__ == "__main__":
    main()
