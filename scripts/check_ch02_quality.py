#!/usr/bin/env python3
"""
CH02全スクリプト徹底品質チェック (001-082)
- 段落レベルの重複検出（50文字以上の完全一致）
- 文レベルの重複検出（30文字以上の完全一致が3件以上）
- 文字数チェック
- 空行の異常
- 最終段落チェック

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
        if line.strip():
            current.append(line.strip())
        elif current:
            paragraphs.append(' '.join(current))
            current = []
    if current:
        paragraphs.append(' '.join(current))
    return paragraphs

def get_sentences(text):
    """テキストを文に分割"""
    # 。で分割
    sentences = re.split(r'。', text)
    return [s.strip() for s in sentences if len(s.strip()) > 30]

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

def check_duplicate_sentences(sentences):
    """重複文を検出（30文字以上が3回以上）"""
    issues = []
    counter = Counter(sentences)
    for sentence, count in counter.items():
        if count >= 3 and len(sentence) > 30:
            issues.append(f"文重複({count}回): 「{sentence[:40]}...」")
    return issues

def check_content_issues(text, paragraphs):
    """コンテンツの問題を検出"""
    issues = []
    
    # 異常な連続空行
    if '\n\n\n\n' in text:
        issues.append("4行以上の連続空行")
    
    # 段落が少なすぎる
    if len(paragraphs) < 25:
        issues.append(f"段落数が少ない({len(paragraphs)}段落)")
    
    # 文字数チェック
    if len(text) < 5000:
        issues.append(f"短すぎ: {len(text)}文字")
    
    return issues

def check_script(video_id):
    """個別スクリプトのチェック"""
    path = DATA_DIR / video_id / "content/assembled.md"
    if not path.exists():
        return {"id": video_id, "exists": False, "issues": ["ファイルなし"]}
    
    text = path.read_text(encoding='utf-8')
    paragraphs = get_paragraphs(text)
    sentences = get_sentences(text)
    
    result = {
        "id": video_id,
        "exists": True,
        "chars": len(text),
        "paragraphs": len(paragraphs),
        "issues": []
    }
    
    # 重複段落チェック
    dup_para = check_duplicate_paragraphs(paragraphs)
    result["issues"].extend(dup_para)
    
    # 重複文チェック
    dup_sent = check_duplicate_sentences(sentences)
    result["issues"].extend(dup_sent)
    
    # コンテンツ問題チェック
    content_issues = check_content_issues(text, paragraphs)
    result["issues"].extend(content_issues)
    
    return result

def main():
    print("=" * 70)
    print("CH02 全スクリプト徹底品質チェック (001-082)")
    print("=" * 70)
    
    all_results = []
    problem_count = 0
    
    for i in range(1, 83):
        video_id = f"{i:03d}"
        result = check_script(video_id)
        all_results.append(result)
        
        if result["issues"]:
            problem_count += 1
            print(f"\n[WARN] {video_id}: {result.get('chars', 0)}文字, {result.get('paragraphs', 0)}段落")
            for issue in result["issues"]:
                print(f"       ⚠️  {issue}")
        else:
            print(f"[OK] {video_id}: {result['chars']}文字, {result['paragraphs']}段落")
    
    print("\n" + "=" * 70)
    print("サマリー")
    print("=" * 70)
    print(f"チェック完了: {len(all_results)}本")
    print(f"問題あり: {problem_count}本")
    
    if problem_count > 0:
        print("\n⚠️  要修正スクリプト一覧:")
        for r in all_results:
            if r.get("issues"):
                print(f"  - {r['id']}: {', '.join(r['issues'][:3])}")
        return 1
    else:
        print("\n✅ 全82本スクリプト問題なし！音声生成可能です。")
        return 0

if __name__ == "__main__":
    exit(main())
