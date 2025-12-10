#!/usr/bin/env python3

# final_script.txtを読み込み、///で区切られたセクションを番号付きで表示
with open('work/ch05/008/final_script.txt', 'r', encoding='utf-8') as f:
    content = f.read()

# 区切り文字'///'で分割
sections = content.split('///')

# 各セクションを番号付きで表示（ただし空文字列は無視）
for i, section in enumerate(sections, 1):
    if section.strip():  # 空白のみのセクションをスキップ
        line_count = section.count('\n') + 1  # 改行の数に1を足して行数を数える
        print(f"セクション{i}: 行数{line_count}")
        if i in [36, 38, 39, 41, 49]:  # 問題のセクションのみ出力
            print(f"内容: {repr(section)}")
        print()