import os
import glob

base_dir = "script_pipeline/data/CH06"
cta_pattern = ["チャンネル登録", "高評価", "コメント", "登録をお願い", "評価をお願い"]
hook_pattern = ["ご存知でしょうか", "いかがでしたか", "驚きですよね"]

for i in range(1, 35):
    num = f"{i:03d}"
    path = f"{base_dir}/{num}/content/assembled.md"
    
    if not os.path.exists(path):
        continue
        
    with open(path, 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    modified = False
    
    # Simple logic: Remove lines containing prohibited phrases from intro/outro areas
    # Intro: first 20 lines, Outro: last 20 lines
    total_lines = len(lines)
    
    for idx, line in enumerate(lines):
        is_intro = idx < 20
        is_outro = idx > total_lines - 20
        
        should_remove = False
        
        if is_intro:
            for p in hook_pattern:
                if p in line:
                    should_remove = True
                    break
        
        if is_outro:
            for p in cta_pattern:
                if p in line:
                    should_remove = True
                    break
        
        if should_remove:
            modified = True
            continue # Skip this line
            
        new_lines.append(line)
    
    if modified:
        print(f"Cleaning {num}...")
        with open(path, 'w') as f:
            f.writelines(new_lines)

print("Batch cleaning complete.")
