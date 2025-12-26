import json
import sys
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')

def load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load {path}: {e}")
        sys.exit(1)

def extract_ideal_settings(draft_path):
    content_path = draft_path / "draft_content.json"
    data = load_json(content_path)
    
    print(f"Analyzing ideal settings from: {draft_path.name}")
    
    # 1. Video/Image Transform (Main Logic)
    video_configs = []
    for track in data.get('tracks', []):
        if track.get('type') != 'video':
            continue
            
        # Check segments
        for seg in track.get('segments', []):
            clip = seg.get('clip', {})
            transform = clip.get('transform', {})
            scale = clip.get('scale', {})
            
            tx = transform.get('x', 0.0)
            ty = transform.get('y', 0.0)
            sx = scale.get('x', 1.0)
            
            # Filter out likely default/empty values if they are exactly 0 or 1
            # BUT user might want exactly 0. Let's collect all valid video segments.
            video_configs.append({'tx': tx, 'ty': ty, 'scale': sx})
    
    # Calculate mode/average for video settings
    # We want the most common setting, assuming it represents the standard layout
    if video_configs:
        # Group by rounded values to find consensus
        from collections import Counter
        
        def round_tuple(c):
            return (round(c['tx'], 4), round(c['ty'], 4), round(c['scale'], 4))
            
        counts = Counter([round_tuple(c) for c in video_configs])
        most_common = counts.most_common(1)[0][0]
        
        ideal_tx, ideal_ty, ideal_scale = most_common
        
        print(f"\n[Ideal Video Settings]")
        print(f"  TX: {ideal_tx}")
        print(f"  TY: {ideal_ty}")
        print(f"  Scale: {ideal_scale}")
        print(f"  (Based on {counts.most_common(1)[0][1]} segments matching this profile)")
        
        return {
            'tx': ideal_tx,
            'ty': ideal_ty,
            'scale': ideal_scale
        }
    else:
        print("No video segments found.")
        return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
        
    draft_path = Path(sys.argv[1])
    extract_ideal_settings(draft_path)
