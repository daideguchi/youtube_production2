import json
import sys
from pathlib import Path

def inspect_tracks(draft_path):
    content_path = Path(draft_path) / "draft_content.json"
    if not content_path.exists():
        print(f"File not found: {content_path}")
        sys.exit(1)

    with open(content_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # 1. Create a map of material_id -> filename
    material_map = {}
    for mat in data.get('materials', {}).get('videos', []):
        material_map[mat['id']] = Path(mat.get('path', 'unknown')).name

    print(f"=== TRACK INSPECTION: {Path(draft_path).name} ===\n")

    # 2. Inspect each video track
    for i, track in enumerate(data.get('tracks', [])):
        if track.get('type') != 'video':
            continue

        print(f"[Track {i}] (Type: {track.get('type')})")
        
        segments = track.get('segments', [])
        if not segments:
            print("  (Empty Track)")
            continue

        # Check first few segments
        for j, seg in enumerate(segments[:5]):
            mat_id = seg.get('material_id')
            filename = material_map.get(mat_id, "Unknown Material")
            
            clip = seg.get('clip', {})
            scale = clip.get('scale', {}).get('x', 'N/A')
            tx = clip.get('transform', {}).get('x', 'N/A')
            ty = clip.get('transform', {}).get('y', 'N/A')

            print(f"  Seg {j}: {filename} | Scale: {scale} | Pos: ({tx}, {ty})")
        
        if len(segments) > 5:
            print(f"  ... and {len(segments) - 5} more segments.")
        print("")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(1)
    inspect_tracks(sys.argv[1])
