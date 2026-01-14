import os

def get_char_count(file_path):
    if not os.path.exists(file_path):
        return 0
    with open(file_path, 'r', encoding='utf-8') as f:
        return len(f.read())

print("ScriptID | CharCount | Status")
print("---------|-----------|-------")
for i in range(42, 92):
    script_id = f"CH02-{i:03d}"
    path = f"scripts/CH02/{i:03d}/content/assembled.md"
    count = get_char_count(path)
    status = "DONE" if count >= 8000 else "TODO"
    if count == 0: status = "MISSING"
    print(f"{script_id} | {count:9d} | {status}")
