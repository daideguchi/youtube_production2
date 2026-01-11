import json
import sys
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(message)s')

def load_draft_content(draft_path):
    content_path = Path(draft_path) / "draft_content.json"
    try:
        with open(content_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Failed to load {content_path}: {e}")
        sys.exit(1)

def extract_track_info(data):
    info = {}
    for i, track in enumerate(data.get('tracks', [])):
        track_type = track.get('type')
        segments = track.get('segments', [])
        
        track_key = f"Track_{i}_{track_type}"
        segment_details = []
        
        for j, seg in enumerate(segments):
            clip = seg.get('clip')
            # Handle cases where clip is None or missing
            if clip is None:
                clip = {}
                
            transform = clip.get('transform', {})
            scale = clip.get('scale', {})
            
            # Extract raw values
            tx = transform.get('x')
            ty = transform.get('y')
            sx = scale.get('x')
            sy = scale.get('y')
            
            # Text content lookup (if text track)
            text_content = None
            if track_type == 'text':
                mat_id = seg.get('material_id')
                # Basic material lookup (inefficient but okay for analysis)
                for mat in data.get('materials', {}).get('texts', []):
                    if mat['id'] == mat_id:
                        try:
                            text_content = json.loads(mat['content']).get('text')
                        except:
                            pass
                        break
            
            seg_info = {
                'index': j,
                'tx': tx,
                'ty': ty,
                'sx': sx,
                'sy': sy,
            }
            if text_content:
                seg_info['text'] = text_content[:20]  # Truncate for display
                
            segment_details.append(seg_info)
            
        info[track_key] = segment_details
    return info

def compare_drafts(path1, path2):
    name1 = Path(path1).name
    name2 = Path(path2).name
    
    data1 = load_draft_content(path1)
    data2 = load_draft_content(path2)
    
    info1 = extract_track_info(data1)
    info2 = extract_track_info(data2)
    
    print(f"=== DEEP COMPARISON REPORT ===")
    print(f"A: {name1}")
    print(f"B: {name2}")
    print("="*60)
    
    all_keys = sorted(list(set(info1.keys()) | set(info2.keys())))
    
    for key in all_keys:
        segs1 = info1.get(key, [])
        segs2 = info2.get(key, [])
        
        if not segs1:
            print(f"\n[{key}] Only in B (Length: {len(segs2)})")
            continue
        if not segs2:
            print(f"\n[{key}] Only in A (Length: {len(segs1)})")
            continue
            
        print(f"\n[{key}] Comparing {len(segs1)} vs {len(segs2)} segments")
        
        # Compare segments
        max_len = max(len(segs1), len(segs2))
        mismatch_count = 0
        
        for i in range(max_len):
            s1 = segs1[i] if i < len(segs1) else None
            s2 = segs2[i] if i < len(segs2) else None
            
            if not s1 or not s2:
                print(f"  Seg {i}: Structure mismatch (Missing in one)")
                continue
                
            # Compare key attributes
            diffs = []
            
            # Helper to compare floats safely
            def is_diff(v1, v2):
                if v1 is None and v2 is None: return False
                if v1 is None or v2 is None: return True
                return abs(v1 - v2) > 0.0001

            if is_diff(s1['tx'], s2['tx']): diffs.append(f"TX: {s1['tx']} -> {s2['tx']}")
            if is_diff(s1['ty'], s2['ty']): diffs.append(f"TY: {s1['ty']} -> {s2['ty']}")
            if is_diff(s1['sx'], s2['sx']): diffs.append(f"Scale X: {s1['sx']} -> {s2['sx']}")
            if is_diff(s1['sy'], s2['sy']): diffs.append(f"Scale Y: {s1['sy']} -> {s2['sy']}")
            
            if diffs:
                mismatch_count += 1
                prefix = f"  Seg {i}"
                if s1.get('text'): prefix += f" ({s1['text']})"
                print(f"{prefix}: {', '.join(diffs)}")
                
        if mismatch_count == 0:
            # If all segments match, print a summary of the values
            sample = segs1[0]
            # Format output clearly
            sx = f"{sample.get('sx'):.4f}" if sample.get('sx') is not None else "None"
            tx = f"{sample.get('tx'):.4f}" if sample.get('tx') is not None else "None"
            ty = f"{sample.get('ty'):.4f}" if sample.get('ty') is not None else "None"
            print(f"  ✅ ALL MATCH. Scale: {sx}, TX: {tx}, TY: {ty}")
        else:
             print(f"  ⚠️  Found {mismatch_count} segment mismatches")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: deep_compare.py <draft1> <draft2>")
        sys.exit(1)
    compare_drafts(sys.argv[1], sys.argv[2])