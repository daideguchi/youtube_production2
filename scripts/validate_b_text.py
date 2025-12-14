#!/usr/bin/env python3
"""
B-Text QA検証スクリプト

assembled.mdとscript_corrected.txtの品質問題を検出する。
問題を検出した場合は警告を出力し、修正を促す。

検出する問題:
1. 章の重複（同じ章番号が複数回登場）
2. 章の順序異常（第1章→第15章→第2章のようなパターン）
3. マークダウン記号の残存（##等）
4. 空のファイル
"""
import re
import sys
from pathlib import Path

from factory_common.paths import script_data_root
from typing import Optional


def extract_chapter_numbers(text: str) -> list[tuple[int, int, str]]:
    """
    テキストから章番号を抽出する。
    Returns: [(行番号, 章番号, 行内容), ...]
    """
    chapters = []
    lines = text.split('\n')
    # 「第N章」形式を検出（「、」や「：」や「:」の前にあるパターン）
    pattern = re.compile(r'^(?:##\s*)?第(\d+)章[：、:：]?')
    
    for i, line in enumerate(lines, 1):
        match = pattern.match(line.strip())
        if match:
            chapter_num = int(match.group(1))
            chapters.append((i, chapter_num, line.strip()))
    
    return chapters


def check_chapter_duplicates(chapters: list[tuple[int, int, str]]) -> list[str]:
    """章の重複を検出"""
    errors = []
    seen = {}
    
    for line_num, chapter_num, line_content in chapters:
        if chapter_num in seen:
            errors.append(
                f"  ❌ 重複: 第{chapter_num}章 が L{seen[chapter_num]} と L{line_num} で重複"
            )
        else:
            seen[chapter_num] = line_num
    
    return errors


def check_chapter_order(chapters: list[tuple[int, int, str]]) -> list[str]:
    """章の順序異常を検出"""
    errors = []
    
    if len(chapters) < 2:
        return errors
    
    for i in range(1, len(chapters)):
        prev_num = chapters[i-1][1]
        curr_num = chapters[i][1]
        prev_line = chapters[i-1][0]
        curr_line = chapters[i][0]
        
        # 通常は章番号は1ずつ増えるか、結びへ移行
        # 大きくジャンプする場合は異常
        if curr_num < prev_num:
            errors.append(
                f"  ❌ 順序異常: 第{prev_num}章(L{prev_line}) → 第{curr_num}章(L{curr_line}) 番号が逆行"
            )
        elif curr_num - prev_num > 2:
            errors.append(
                f"  ❌ 順序異常: 第{prev_num}章(L{prev_line}) → 第{curr_num}章(L{curr_line}) 番号ジャンプ"
            )
    
    return errors


def check_markdown_symbols(text: str) -> list[str]:
    """マークダウン記号の残存を検出"""
    errors = []
    lines = text.split('\n')
    
    for i, line in enumerate(lines, 1):
        if line.startswith('##'):
            errors.append(f"  ❌ MD記号: L{i} に ## が残存")
        if line.startswith('**') or line.endswith('**'):
            errors.append(f"  ❌ MD記号: L{i} に ** が残存")
        if '```' in line:
            errors.append(f"  ❌ MD記号: L{i} にコードブロックが残存")
    
    return errors


def check_empty_or_short(text: str, min_chars: int = 1000) -> list[str]:
    """ファイルが空または短すぎないか確認"""
    errors = []
    
    if len(text.strip()) == 0:
        errors.append("  ❌ ファイルが空です")
    elif len(text) < min_chars:
        errors.append(f"  ⚠️ ファイルが短すぎます ({len(text)}文字 < {min_chars}文字)")
    
    return errors


def validate_file(file_path: Path) -> tuple[bool, list[str]]:
    """ファイルを検証"""
    if not file_path.exists():
        return False, [f"  ❌ ファイルが存在しません: {file_path}"]
    
    with open(file_path, 'r', encoding='utf-8') as f:
        text = f.read()
    
    all_errors = []
    
    # 空チェック
    all_errors.extend(check_empty_or_short(text))
    
    # 章構造チェック
    chapters = extract_chapter_numbers(text)
    all_errors.extend(check_chapter_duplicates(chapters))
    all_errors.extend(check_chapter_order(chapters))
    
    # マークダウンチェック（script_corrected.txtの場合）
    if 'script_corrected' in file_path.name or 'script_sanitized' in file_path.name:
        all_errors.extend(check_markdown_symbols(text))
    
    is_valid = len(all_errors) == 0
    return is_valid, all_errors


def validate_episode(episode_dir: Path) -> tuple[bool, list[str]]:
    """エピソードディレクトリを検証"""
    results = []
    all_valid = True
    
    # assembled.md
    assembled = episode_dir / "content" / "assembled.md"
    if assembled.exists():
        valid, errors = validate_file(assembled)
        if not valid:
            all_valid = False
            results.append(f"  [assembled.md]")
            results.extend(errors)
    
    # script_corrected.txt
    corrected = episode_dir / "audio_prep" / "script_corrected.txt"
    if corrected.exists():
        valid, errors = validate_file(corrected)
        if not valid:
            all_valid = False
            results.append(f"  [script_corrected.txt]")
            results.extend(errors)
    
    return all_valid, results


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="B-Text品質検証")
    parser.add_argument("--channel", help="チャンネルID (例: CH06)")
    parser.add_argument("--episode", help="エピソード番号 (例: 004)")
    parser.add_argument("--all", action="store_true", help="全チャンネル・全エピソードをチェック")
    parser.add_argument("--fix", action="store_true", help="自動修正可能な問題を修正")
    args = parser.parse_args()
    
    base_path = script_data_root()
    
    if args.all:
        channels = [d for d in base_path.iterdir() if d.is_dir() and d.name.startswith('CH')]
    elif args.channel:
        channels = [base_path / args.channel]
    else:
        print("使用法: python validate_b_text.py --channel CH06 [--episode 004]")
        print("       python validate_b_text.py --all")
        return
    
    total_issues = 0
    total_checked = 0
    
    for channel_dir in sorted(channels):
        if not channel_dir.exists():
            print(f"⚠️ {channel_dir.name}: ディレクトリが存在しません")
            continue
        
        if args.episode:
            episodes = [channel_dir / args.episode]
        else:
            episodes = sorted([d for d in channel_dir.iterdir() if d.is_dir() and d.name.isdigit()])
        
        print(f"\n{'='*60}")
        print(f"📁 {channel_dir.name}: {len(episodes)}エピソード")
        print('='*60)
        
        channel_issues = 0
        for episode_dir in episodes:
            total_checked += 1
            valid, errors = validate_episode(episode_dir)
            
            if not valid:
                channel_issues += len(errors)
                print(f"\n❌ {episode_dir.name}:")
                for error in errors:
                    print(error)
            else:
                # 問題なしは表示しない（静かな成功）
                pass
        
        if channel_issues == 0:
            print(f"✅ 全エピソード正常")
        else:
            print(f"\n⚠️  {channel_dir.name}: {channel_issues}件の問題")
        
        total_issues += channel_issues
    
    print(f"\n{'='*60}")
    print(f"📊 合計: {total_checked}エピソードをチェック, {total_issues}件の問題")
    print('='*60)
    
    if total_issues > 0:
        sys.exit(1)
    else:
        print("✅ 全て正常です")
        sys.exit(0)


if __name__ == "__main__":
    main()
