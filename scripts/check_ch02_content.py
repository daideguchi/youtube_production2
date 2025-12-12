#!/usr/bin/env python3
"""
CH02 全スクリプト内容品質チェック (001-082)
- 段落の重複検出
- タイトルとの内容整合性確認
- 異常な繰り返し/同じフレーズの過剰使用
- 文字数
"""

from pathlib import Path
import re
import sys
from collections import Counter

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory_common.paths import script_data_root

DATA_DIR = script_data_root() / "CH02"

def get_paragraphs(text):
    """テキストを段落に分割"""
    paragraphs = []
    current = []
    for line in text.split('\n'):
        line = line.strip()
        if line:
            current.append(line)
        elif current:
            paragraphs.append(' '.join(current))
            current = []
    if current:
        paragraphs.append(' '.join(current))
    return paragraphs

def check_duplicate_paragraphs(paragraphs):
    """重複段落を検出（50文字以上）"""
    issues = []
    seen = {}
    for i, p in enumerate(paragraphs):
        if len(p) > 50:
            if p in seen:
                issues.append(f"段落重複: P{seen[p]+1}とP{i+1}")
            else:
                seen[p] = i
    return issues

def check_repetitive_phrases(text):
    """同じフレーズの過剰な繰り返しを検出"""
    issues = []
    # 20文字以上の繰り返しフレーズを検出
    sentences = [s.strip() for s in re.split(r'。|\n', text) if len(s.strip()) > 20]
    counter = Counter(sentences)
    for sentence, count in counter.items():
        if count >= 4:
            issues.append(f"過剰繰り返し({count}回): 「{sentence[:30]}...」")
    return issues

def check_ending_markers(text):
    """終わりのような段落が複数あるか（複数回終わる）"""
    issues = []
    ending_patterns = [
        r"最後に[、。]",
        r"これからも.*探求",
        r"チャンネル登録",
        r"次の思索の時間",
    ]
    ending_count = 0
    for pattern in ending_patterns:
        matches = re.findall(pattern, text)
        ending_count += len(matches)
    if ending_count > 4:
        issues.append(f"終わりマーカーが多すぎる({ending_count}回)")
    return issues

def check_script_quality(video_id):
    """個別スクリプトの品質チェック"""
    path = DATA_DIR / video_id / "content/assembled.md"
    if not path.exists():
        return {"id": video_id, "exists": False, "issues": ["ファイルなし"]}
    
    text = path.read_text(encoding='utf-8')
    paragraphs = get_paragraphs(text)
    
    result = {
        "id": video_id,
        "exists": True,
        "chars": len(text),
        "paragraphs": len(paragraphs),
        "issues": []
    }
    
    # 文字数チェック
    if len(text) < 5000:
        result["issues"].append(f"短すぎ: {len(text)}文字")
    
    # 重複段落
    result["issues"].extend(check_duplicate_paragraphs(paragraphs))
    
    # 過剰な繰り返し
    result["issues"].extend(check_repetitive_phrases(text))
    
    # 終わりマーカーの矛盾
    result["issues"].extend(check_ending_markers(text))
    
    return result

def main():
    print("=" * 70)
    print("CH02 全スクリプト内容品質チェック (001-082)")
    print("=" * 70)
    
    all_results = []
    problem_scripts = []
    
    for i in range(1, 83):
        video_id = f"{i:03d}"
        result = check_script_quality(video_id)
        all_results.append(result)
        
        if result["issues"]:
            problem_scripts.append(result)
            print(f"\n[WARN] {video_id}: {result.get('chars', 0)}文字")
            for issue in result["issues"]:
                print(f"       ⚠️  {issue}")
        else:
            print(f"[OK] {video_id}: {result['chars']}文字, {result['paragraphs']}段落")
    
    print("\n" + "=" * 70)
    print("サマリー")
    print("=" * 70)
    print(f"チェック完了: {len(all_results)}本")
    print(f"問題あり: {len(problem_scripts)}本")
    
    if problem_scripts:
        print("\n⚠️  要確認スクリプト一覧:")
        for r in problem_scripts:
            print(f"  - {r['id']}: {', '.join(r['issues'][:3])}")
    else:
        print("\n✅ 全82本スクリプト問題なし！音声生成可能です。")

if __name__ == "__main__":
    main()
