#!/usr/bin/env python3
"""
Commentary Service
Wraps the complex commentary_01 and commentary_02 pipelines into a simple CLI.
"""

import argparse
import sys
import os
from pathlib import Path

# Add project root to path to allow imports
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

# Add commentary_01 core to path
COMMENTARY_01_ROOT = PROJECT_ROOT / "factory_commentary" / "commentary_01_srtfile_v2"
sys.path.insert(0, str(COMMENTARY_01_ROOT))

def handle_script(video_id: str):
    print(f"[CommentaryService] Generating script for {video_id}...")
    
    # 1. Resolve paths
    # We need to find the project in master_planning.csv to get the folder path
    # For now, assuming standard path: production/Channel_Name/VideoID_Title
    # But since we don't have the title easily, we might need to look it up or glob.
    
    # Ideally, we should reuse the logic from `scaffold_project.py` or `project_gen.py` 
    # to find the project directory.
    
    from production._management.scripts.sheet_lookup import find_project_path
    
    project_path = find_project_path(video_id)
    if not project_path:
        print(f"Error: Could not find project directory for {video_id}")
        sys.exit(1)
        
    print(f"Target Project Path: {project_path}")
    
    # 2. Initialize ContentProcessor
    # We need to adapt ContentProcessor to work with our new structure
    # or temporarily mock it to verify the flow.
    
    # Importing here to avoid top-level import errors if paths aren't set
    try:
        from core.tools.content_processor import ContentProcessor
        
        # Initialize ContentProcessor
        # Note: ContentProcessor defaults to looking in `data/`, but we want `production/`.
        # We might need to subclass or patch it, but for now let's try to use it as is
        # and see where it writes.
        # Actually, looking at ContentProcessor code:
        # self.data_dir = self.project_root / "data"
        # We can pass project_root.
        
        # We want it to write to `production/[Channel]/[Video]/01_planning/`
        # But ContentProcessor writes to `data/[Channel]/[Video]/content/chapters/`
        
        # Strategy:
        # 1. Let it write to its default location in `factory_commentary/commentary_01_srtfile_v2/data`
        # 2. Move the result to `production/...`
        # OR
        # 3. Monkeypatch `_content_dir` in the instance.
        
        processor = ContentProcessor(project_root=COMMENTARY_01_ROOT)
        
        # Monkeypatch to redirect output to our production folder
        # production/Channel_Jinsei/CH01-TEST_人生の道標テスト/01_planning
        target_dir = project_path / "01_planning"
        target_dir.mkdir(parents=True, exist_ok=True)
        
        def patched_content_dir(channel_code, video_number):
            return target_dir
            
        processor._content_dir = patched_content_dir
        
        # We also need to ensure `outline.md` exists, as ContentProcessor reads it.
        # If it doesn't exist, we should generate a default one or ask LLM.
        # For this test, let's create a dummy outline if missing.
        outline_path = target_dir / "outline.md"
        if not outline_path.exists():
            print(f"Creating dummy outline at {outline_path}")
            with open(outline_path, "w", encoding="utf-8") as f:
                f.write(f"# {video_id} Outline\n\n## 第1章: 導入\n\n## 第2章: 本題\n\n## 第3章: 結論\n")
        
        # Run generation
        print(f"Running ContentProcessor for {video_id}...")
        # Channel code in master_planning is CH01, but ContentProcessor might expect CH01
        channel_code = video_id.split('-')[0]
        
        result = processor.generate_chapter_drafts(
            channel_code=channel_code,
            video_number=video_id,
            overwrite=True
        )
        
        print(f"Generation Result: {result}")
        
    except ImportError as e:
        print(f"Error importing ContentProcessor: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error during generation: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Factory Commentary Service")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # script
    script_parser = subparsers.add_parser("script", help="Generate Script")
    script_parser.add_argument("--video-id", required=True, help="Video ID")

    args = parser.parse_args()

    if args.command == "script":
        handle_script(args.video_id)

if __name__ == "__main__":
    main()
