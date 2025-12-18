import os
import re
from _bootstrap import ensure_monorepo_imports

ensure_monorepo_imports()

from factory_common.paths import script_data_root

base_dir = script_data_root() / "CH06"
target_char_count = 7000

print(f"{'ID':<5} {'Chars':<8} {'Chapters':<8} {'Status':<10} {'Title (First 20)'}")
print("-" * 70)

for i in range(1, 35):
    num = f"{i:03d}"
    path = base_dir / num / "content" / "assembled.md"
    
    if not path.exists():
        print(f"{num:<5} MISSING")
        continue
        
    with open(path, 'r') as f:
        content = f.read()
        
    char_count = len(content.replace('\n', '').replace(' ', '').replace('　', ''))
    chapters = len(re.findall(r'^## 第\d+章', content, re.MULTILINE))
    
    status = "OK"
    if char_count < 6500:
        status = "LOW"
    elif chapters < 7:
        status = "CHAPTERS"
        
    title = content.split('\n')[0].strip()[:20]
    print(f"{num:<5} {char_count:<8} {chapters:<8} {status:<10} {title}")
