import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from factory_common.paths import script_data_root

base_dir = script_data_root() / "CH06"
target_char_count = 7000 # Minimum target for high quality

print(f"{'ID':<5} {'Chars':<8} {'Chapters':<8} {'Status':<10} {'Title (First 20)'}")
print("-" * 70)

enhance_targets = []

for i in range(1, 35):
    num = f"{i:03d}"
    path = base_dir / num / "content" / "assembled.md"
    
    if not path.exists():
        continue
        
    with open(path, 'r') as f:
        content = f.read()
        
    char_count = len(content.replace('\n', '').replace(' ', '').replace('　', ''))
    chapters = len(re.findall(r'^## 第\d+章', content, re.MULTILINE))
    
    status = "OK"
    if char_count < 5000:
        status = "CRITICAL" # Too short, needs rewrite
        enhance_targets.append(num)
    elif char_count < 6500:
        status = "WARNING" # Could be better
        # enhance_targets.append(num) # Optional: enable if we want to perfect everything
    elif chapters < 7:
        status = "CHAPTERS" # Format issue
        enhance_targets.append(num)
        
    title = content.split('\n')[0].strip()[:20]
    print(f"{num:<5} {char_count:<8} {chapters:<8} {status:<10} {title}")

# Save targets for bash script to handle
with open("enhance_list.txt", "w") as f:
    for t in enhance_targets:
        f.write(f"{t}\n")

print("-" * 70)
if enhance_targets:
    print(f"Enhance Targets: {', '.join(enhance_targets)}")
else:
    print("All scripts meet basic quality standards.")
