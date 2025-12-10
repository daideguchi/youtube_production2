import argparse
import subprocess
import glob
import os
import sys
from pathlib import Path

def run_batch(channel: str, start_index: int = 1):
    input_dir = Path("audio_tts_v2/inputs") / channel
    # Find all .txt files
    files = sorted(input_dir.glob("*.txt"))
    
    print(f"[BATCH] Found {len(files)} inputs for {channel}")
    
    for f in files:
        # Extract video ID (filename without extension)
        video_id = f.stem
        # Ensure it is numeric
        if not video_id.isdigit():
            continue
            
        if int(video_id) < start_index:
            continue
            
        print(f"\n==========================================")
        print(f"[BATCH] Starting {channel}-{video_id}")
        print(f"==========================================\n")
        
        cmd = [
            "python3", "audio_tts_v2/scripts/run_tts.py",
            "--mode", "auto",
            "--channel", channel,
            "--video", video_id,
            "--input", str(f),
            "--phase", "full"
        ]
        
        env = os.environ.copy()
        env["PYTHONPATH"] = "audio_tts_v2"
        
        try:
            subprocess.check_call(cmd, env=env)
            print(f"[BATCH] Success: {channel}-{video_id}")
        except subprocess.CalledProcessError as e:
            print(f"[BATCH] FAILED: {channel}-{video_id} (Exit Code {e.returncode})")
            # Determine if we should stop or continue?
            # User wants "dondon" (fast forward). We should probably continue but log error.
            # But run_tts.py issues are usually fatal (LLM error). 
            # However, transient errors might pass on retry?
            # For now, we continue to next video.
            continue
            
    print(f"[BATCH] Completed {channel}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    parser.add_argument("--start", type=int, default=1)
    args = parser.parse_args()
    
    run_batch(args.channel, args.start)
