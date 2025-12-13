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

def get_track_map(data):
    """Map tracks by type and index/name for comparison."""
    tracks = {}
    for i, track in enumerate(data.get('tracks', [])):
        t_type = track.get('type')
        # Use a key that groups tracks reasonably
        key = f"{t_type}_{i}"
        tracks[key] = track
    return tracks

def analyze_transform_changes(baseline_tracks, edited_tracks):
    changes = []
    
    # Check image/video tracks for transform changes
    for key, e_track in edited_tracks.items():
        if e_track.get('type') != 'video': 
            continue
            
        b_track = baseline_tracks.get(key)
        if not b_track: continue
        
        e_segments = e_track.get('segments', [])
        b_segments = b_track.get('segments', [])
        
        # Compare first segment as a sample (assuming consistent application per track usually)
        # But manual edits might differ per clip. Let's check averages.
        
        tx_diffs = []
        ty_diffs = []
        scale_diffs = []
        
        for i, e_seg in enumerate(e_segments):
            if i >= len(b_segments): break
            b_seg = b_segments[i]
            
            e_clip = e_seg.get('clip', {})
            b_clip = b_seg.get('clip', {})
            
            # Check transform
            e_trans = e_clip.get('transform', {})
            b_trans = b_clip.get('transform', {})
            
            e_tx = e_trans.get('x', 0.0)
            e_ty = e_trans.get('y', 0.0)
            b_tx = b_trans.get('x', 0.0)
            b_ty = b_trans.get('y', 0.0)
            
            # Check scale
            e_scale = e_clip.get('scale', {}).get('x', 1.0)
            b_scale = b_clip.get('scale', {}).get('x', 1.0)
            
            if abs(e_tx - b_tx) > 0.001: tx_diffs.append(e_tx)
            if abs(e_ty - b_ty) > 0.001: ty_diffs.append(e_ty)
            if abs(e_scale - b_scale) > 0.001: scale_diffs.append(e_scale)

        if tx_diffs or ty_diffs or scale_diffs:
            avg_tx = sum(tx_diffs)/len(tx_diffs) if tx_diffs else None
            avg_ty = sum(ty_diffs)/len(ty_diffs) if ty_diffs else None
            avg_scale = sum(scale_diffs)/len(scale_diffs) if scale_diffs else None
            
            changes.append({
                'track': key,
                'new_tx': avg_tx,
                'new_ty': avg_ty,
                'new_scale': avg_scale
            })
            
    return changes

def analyze_text_changes(baseline_data, edited_data):
    """Check for changes in main title or belt text styling/position."""
    changes = []
    
    # Helper to extract text materials
    def get_texts(data):
        texts = {}
        for mat in data.get('materials', {}).get('texts', []):
            try:
                content = json.loads(mat.get('content', '{}'))
                texts[mat['id']] = content
            except:
                pass
        return texts

    b_texts = get_texts(baseline_data)
    e_texts = get_texts(edited_data)
    
    # We can't easily map materials 1:1 if IDs changed, but IDs usually persist in simple edits.
    # Let's look at tracks again to find text segments.
    
    for i, e_track in enumerate(edited_data.get('tracks', [])):
        if e_track.get('type') != 'text': continue
        
        b_track = baseline_data.get('tracks', [])[i] if i < len(baseline_data.get('tracks', [])) else None
        if not b_track: continue
        
        for j, e_seg in enumerate(e_track.get('segments', [])):
            if j >= len(b_track.get('segments', [])): break
            b_seg = b_track['segments'][j]
            
            # Position check
            e_clip = e_seg.get('clip', {})
            b_clip = b_seg.get('clip', {})
            
            e_tx = e_clip.get('transform', {}).get('x', 0)
            b_tx = b_clip.get('transform', {}).get('x', 0)
            e_ty = e_clip.get('transform', {}).get('y', 0)
            b_ty = b_clip.get('transform', {}).get('y', 0)
            e_sx = e_clip.get('scale', {}).get('x', 1)
            b_sx = b_clip.get('scale', {}).get('x', 1)

            if abs(e_tx - b_tx) > 0.01 or abs(e_ty - b_ty) > 0.01 or abs(e_sx - b_sx) > 0.01:
                # Get text content for context
                mat_id = e_seg.get('material_id')
                text_content = e_texts.get(mat_id, {}).get('text', 'Unknown')
                
                changes.append({
                    'type': 'text_transform',
                    'text': text_content[:20],
                    'tx': e_tx,
                    'ty': e_ty,
                    'scale': e_sx,
                    'diff_tx': e_tx - b_tx,
                    'diff_ty': e_ty - b_ty
                })
                
    return changes

def main():
    if len(sys.argv) < 3:
        print("Usage: compare.py <baseline_json> <edited_json>")
        sys.exit(1)

    baseline = load_json(sys.argv[1])
    edited = load_json(sys.argv[2])
    
    b_tracks_map = get_track_map(baseline)
    e_tracks_map = get_track_map(edited)
    
    print("=== Analysis of Manual Edits ===")
    
    # 1. Video/Image Transform Changes
    transform_changes = analyze_transform_changes(b_tracks_map, e_tracks_map)
    if transform_changes:
        print("\n[Video/Image Position Changes]")
        for c in transform_changes:
            print(f"  Track {c['track']}:")
            if c['new_tx'] is not None: print(f"    X: {c['new_tx']:.4f}")
            if c['new_ty'] is not None: print(f"    Y: {c['new_ty']:.4f}")
            if c['new_scale'] is not None: print(f"    Scale: {c['new_scale']:.4f}")
            
            # Heuristic: Is this the main image track?
            if c['new_scale'] is not None and 0.5 < c['new_scale'] < 1.5:
                 print(f"    -> Suggest updating preset 'scale' to {c['new_scale']:.3f}")
            if c['new_tx'] is not None or c['new_ty'] is not None:
                 tx = c['new_tx'] if c['new_tx'] is not None else 0
                 ty = c['new_ty'] if c['new_ty'] is not None else 0
                 print(f"    -> Suggest updating preset 'tx': {tx:.3f}, 'ty': {ty:.3f}")

    # 2. Text Position Changes
    text_changes = analyze_text_changes(baseline, edited)
    if text_changes:
        print("\n[Text/Title Position Changes]")
        # Group similar changes
        avg_ty_diff = 0
        count = 0
        for c in text_changes:
            print(f"  '{c['text']}...': X={c['tx']:.3f}, Y={c['ty']:.3f}, Scale={c['scale']:.3f} (Diff Y: {c['diff_ty']:.3f})")
            avg_ty_diff += c['diff_ty']
            count += 1
        
        if count > 0:
            avg = avg_ty_diff / count
            if abs(avg) > 0.01:
                print(f"    -> Text moved by avg Y: {avg:.3f}. Update belt config or template?")

    # 3. Track Count Check
    if len(baseline.get('tracks', [])) != len(edited.get('tracks', [])):
        print(f"\n[Structure Change] Track count changed: {len(baseline['tracks'])} -> {len(edited['tracks'])}")

if __name__ == "__main__":
    main()
