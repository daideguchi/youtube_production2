import argparse
import subprocess
import os
from pathlib import Path

def run_b_text_batch(channel: str, start: int = 1, reverse: bool = False, count: int = -1):
    input_dir = Path("audio_tts_v2/inputs") / channel
    files = sorted(input_dir.glob("*.txt"))
    
    print(f"[B-TEXT BATCH] Found {len(files)} inputs for {channel}")
    
    # Filter by numeric
    video_files = []
    for f in files:
        if f.stem.isdigit():
            video_files.append(f)
            
    # Sort
    video_files.sort(key=lambda x: int(x.stem))
    
    # Apply filtering
    if reverse:
        video_files.reverse()
    
    # Filter by range if needed
    # (Simplified logic: just iterate and skip until start, or just run whatever)
    # But reverse means start from end.
    
    processed_count = 0
    
    for f in video_files:
        vid = int(f.stem)
        
        # If normal mode, skip < start
        if not reverse and vid < start:
            continue
        # If reverse mode, skip > start (if start is meant to be max?) 
        # Actually user probably just wants "start from end". 
        # Let's interpret start as "Start Index" in list or "Video ID"?
        # Let's just say "Reverse" starts from MAX ID and goes down. 
        # "Start" argument usually means "Min ID".
        
        if reverse:
            # If reverse, we process 82, 81, 80... 
            # We stop if we hit "start" (optional).
            pass
            
        print(f"\n[B-TEXT BATCH] Generatings Readings for {channel}-{vid}")
        
        # Check if already exists?
        # artifacts/final/CHxx/xxx/srt_blocks.json
        blocks_path = Path(f"audio_tts_v2/artifacts/final/{channel}/{f.stem}/srt_blocks.json")
        if blocks_path.exists():
            print(f"[SKIP] srt_blocks.json already exists for {vid}")
            continue
            
        cmd = [
            "python3", "audio_tts_v2/scripts/run_tts.py",
            "--mode", "auto",
            "--channel", channel,
            "--video", f.stem,
            "--input", str(f),
            "--phase", "srt_only" 
        ]
        
        env = os.environ.copy()
        env["PYTHONPATH"] = "audio_tts_v2"
        
        try:
            subprocess.check_call(cmd, env=env)
            processed_count += 1
        except subprocess.CalledProcessError as e:
            print(f"[FAIL] {channel}-{vid}")
            
        if count > 0 and processed_count >= count:
            print(f"[LIMIT] Reached count limit {count}")
            break

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel", required=True)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--reverse", action="store_true")
    parser.add_argument("--count", type=int, default=-1)
    args = parser.parse_args()
    
    run_b_text_batch(args.channel, args.start, args.reverse, args.count)
