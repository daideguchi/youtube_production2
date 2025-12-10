import csv
import json
import os
import sys
from pathlib import Path

# Configuration
PRODUCTION_ROOT = Path("/Users/dd/10_YouTube_Automation/production")
MANAGEMENT_DIR = PRODUCTION_ROOT / "_management"
MASTER_CSV_PATH = MANAGEMENT_DIR / "master_planning.csv"
CHANNELS_CSV_PATH = MANAGEMENT_DIR / "channels.csv"

def load_channels(csv_path):
    """Reads channels.csv and returns a dict mapping channel_id to folder_name."""
    if not csv_path.exists():
        print(f"Error: Channels CSV not found at {csv_path}")
        sys.exit(1)
        
    channels = {}
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('channel_id') and row.get('folder_name'):
                channels[row['channel_id']] = row['folder_name']
    return channels

def load_planning_data(csv_path):
    """Reads the planning CSV and returns a list of dicts."""
    if not csv_path.exists():
        print(f"Error: Master CSV not found at {csv_path}")
        sys.exit(1)
    
    projects = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('video_id'): # Ensure valid row
                projects.append(row)
    return projects

def sanitize_filename(name):
    """Sanitizes a string to be safe for filenames."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '')
    return name.strip()

def create_project_structure(project, channel_folders):
    """Creates the folder structure for a single project."""
    video_id = project['video_id']
    channel_id = project.get('channel_id')
    
    if not channel_id or channel_id not in channel_folders:
        print(f"Skipping project {video_id}: Unknown channel_id '{channel_id}'")
        return False

    channel_folder_name = channel_folders[channel_id]
    title = project.get('theme_title', 'Untitled')
    safe_title = sanitize_filename(title)
    
    # Folder naming convention: [VideoID]_[Title]
    folder_name = f"{video_id}_{safe_title}"
    project_path = PRODUCTION_ROOT / channel_folder_name / folder_name
    
    if project_path.exists():
        # print(f"Skipping existing project: {folder_name}")
        return False

    print(f"Creating project: {channel_folder_name}/{folder_name}")
    
    # Create subdirectories
    subdirs = [
        "01_planning",
        "02_audio",
        "03_materials",
        "04_edit",
        "05_output"
    ]
    
    for subdir in subdirs:
        (project_path / subdir).mkdir(parents=True, exist_ok=True)
        
    # Create metadata.json
    metadata = {
        "channel_id": channel_id,
        "video_id": video_id,
        "title": title,
        "series": project.get('series_name', ''),
        "status": project.get('status', 'Idea'),
        "created_at": "2025-11-20", # In a real app, use datetime.now()
        "original_data": project
    }
    
    with open(project_path / "metadata.json", 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
        
    return True

def main():
    print(f"Starting scaffolding from {MASTER_CSV_PATH}...")
    
    channels = load_channels(CHANNELS_CSV_PATH)
    print(f"Loaded {len(channels)} channels.")

    projects = load_planning_data(MASTER_CSV_PATH)
    print(f"Found {len(projects)} projects in Master CSV.")
    
    created_count = 0
    for project in projects:
        if create_project_structure(project, channels):
            created_count += 1
            
    print(f"Scaffolding complete. Created {created_count} new project folders.")

if __name__ == "__main__":
    main()
