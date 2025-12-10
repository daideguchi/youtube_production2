import os
import glob
import re

base_dir = "script_pipeline/data/CH06"
target_char_count = 8000

print(f"Checking CH06 scripts against target: {target_char_count} chars")
print(f"{'ID':<5} {'Status':<10} {'Chars':<8} {'Title (First 20 chars)':<30}")
print("-" * 60)

for i in range(1, 35):
    num = f"{i:03d}"
    path = f"{base_dir}/{num}/content/assembled.md"
    
    if not os.path.exists(path):
        print(f"{num:<5} {'MISSING':<10} {'-':<8} -")
        continue
        
    with open(path, 'r') as f:
        content = f.read()
        
    char_count = len(content.replace('\n', '').replace(' ', '').replace('　', ''))
    
    # Simple quality checks
    has_chapters = len(re.findall(r'^## 第\d+章', content, re.MULTILINE))
    has_cta = "チャンネル登録" in content[-500:] # Should NOT be there per prompt
    has_cheap_hook = "ご存知でしょうか" in content[:500] # Should NOT be there
    
    status = "OK"
    if char_count < target_char_count * 0.8: # Allow 20% margin
        status = "LOW"
    elif char_count > target_char_count * 1.5:
        status = "HIGH"
        
    title = content.split('\n')[0].strip()[:20]
    
    print(f"{num:<5} {status:<10} {char_count:<8} {title:<30}")
    
    if has_cta:
        print(f"  WARNING: Possible CTA found near end.")
    if has_cheap_hook:
        print(f"  WARNING: Cheap hook found near start.")
    if has_chapters < 7:
        print(f"  WARNING: Chapter count {has_chapters} < 7")

