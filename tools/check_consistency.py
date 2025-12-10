import sys
import csv
import json
import os

def check_consistency(channel_id, video_number):
    # Normalize video number (e.g., "31" -> "031")
    video_number_str = str(video_number).zfill(3)
    video_id = f"{channel_id}-{video_number_str}"
    
    print(f"🔍 Checking consistency for {video_id}...")

    # 1. Get SoT Title from CSV
    csv_path = f"progress/channels/{channel_id}.csv"
    if not os.path.exists(csv_path):
        print(f"❌ CSV not found: {csv_path}")
        return False

    sot_title = None
    sot_row = None
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Check various columns for ID match
                if row.get('動画番号') == str(int(video_number)) or row.get('管理ID') == video_id:
                    sot_title = row.get('タイトル')
                    sot_row = row
                    break
    except Exception as e:
        print(f"❌ Error reading CSV: {e}")
        return False
    
    if not sot_title:
        print(f"❌ Video ID {video_id} (No. {video_number}) not found in CSV.")
        return False

    print(f"✅ SoT Title (CSV): {sot_title}")

    # 2. Check status.json
    base_dir = f"script_pipeline/data/{channel_id}/{video_number_str}"
    status_path = f"{base_dir}/status.json"
    
    status_ok = True
    if os.path.exists(status_path):
        try:
            with open(status_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                meta_title = data.get('metadata', {}).get('title', '')
                
                # Normalize for comparison (ignore whitespace differences)
                if meta_title.strip() != sot_title.strip():
                    print(f"❌ [CRITICAL] status.json title mismatch!")
                    print(f"   Expected: {sot_title}")
                    print(f"   Found:    {meta_title}")
                    status_ok = False
                else:
                    print(f"✅ status.json title matches.")
        except Exception as e:
            print(f"❌ Error reading status.json: {e}")
            status_ok = False
    else:
        print(f"⚠️ status.json not found (Safe to start new)")

    # 3. Check Content Files for Theme Consistency
    # We check if the file exists and if it contains keywords from the title
    content_files = [
        f"{base_dir}/content/outline.md",
        f"{base_dir}/content/analysis/research/research_brief.md",
        f"{base_dir}/content/final/assembled.md"
    ]
    
    # Extract keywords from title (simple heuristic: split by space/brackets, take long words)
    # For Japanese, this is harder without tokenizer, but we can check exact title presence or manual verify
    
    content_issues = False
    for fpath in content_files:
        if os.path.exists(fpath):
            try:
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # Check if title is present in the first 500 chars (usually header)
                    if sot_title not in content[:1000]:
                        print(f"⚠️ [WARNING] Exact title not found in {os.path.basename(fpath)}")
                        print(f"   File header: {content[:100].replace(chr(10), ' ')}...")
                        # Don't fail automatically, but warn heavily
                    else:
                        print(f"✅ Title found in {os.path.basename(fpath)}")
            except Exception as e:
                print(f"❌ Error reading {fpath}: {e}")

    if not status_ok:
        print("\n🛑 CONSISTENCY CHECK FAILED. DO NOT PROCEED.")
        return False
    
    print("\n✨ Consistency Check Passed (or no conflicting data found).")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 tools/check_consistency.py <channel_id> <video_number>")
        print("Example: python3 tools/check_consistency.py CH02 31")
        sys.exit(1)
    
    channel = sys.argv[1]
    num = sys.argv[2]
    
    if not check_consistency(channel, num):
        sys.exit(1)
