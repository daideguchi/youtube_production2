#!/usr/bin/env python3

# 検証ツールと同じ方法でセクションを数える
def count_sections_like_validator(file_path, delimiter):
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read()
    
    lines = text.splitlines()
    
    sec = []
    sec_idx = 0
    
    for idx, raw in enumerate(lines, start=1):
        ln = raw.rstrip("\n")
        if not ln.strip():
            continue
        sec.append(ln)
        if ln.endswith(delimiter):
            sec_idx += 1
            if sec_idx in [36, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49]:  # 問題のセクション
                print(f"セクション{sec_idx}: 行数{len(sec)}")
                print(f"内容: {repr(sec)}")
                print()
            sec = []
    
    if sec:
        print("最終セクションが区切りで閉じていません")

count_sections_like_validator('work/ch05/008/final_script.txt', '///')