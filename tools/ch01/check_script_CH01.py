#!/usr/bin/env python3
"""人生の道標 台本セルフチェック補助ツール

Usage:
    python3 tools/check_script.py path/to/script.txt --names 主人公名 敵役名

機能:
1. 語尾多様化チェック: 文末2文字が同一の文が3回以上連続していないか検出。
2. 主語明示チェック: セリフ以外の段落に、指定した登場人物名が含まれているかを確認。

結果は標準出力に要約と問題箇所の抜粋を表示します。
"""

from __future__ import annotations

import argparse
import pathlib
import re
from typing import List, Sequence

SENTENCE_PATTERN = re.compile(r"([^。！？!?\n]+[。！？!?])", re.MULTILINE)
ASCII_WORD_PATTERN = re.compile(r"[A-Za-z]{4,}")
ALLOWED_ASCII = {"YouTube", "VOICEVOX", "HTTPS", "HTTP", "CSV", "URL", "AI"}
FORBIDDEN_CHARS = {"◯", "△", "×", "•", "●", "▪"}


def load_text(path: pathlib.Path) -> str:
    data = path.read_text(encoding="utf-8")
    # 正規化: Windows改行をLFに統一
    return data.replace("\r\n", "\n").replace("\r", "\n")


def extract_sentences(text: str) -> List[str]:
    sentences = SENTENCE_PATTERN.findall(text)
    # 正規表現の都合で末尾が句読点で終わらない場合を補完
    tail = text[text.rfind(sentences[-1]) + len(sentences[-1]) :] if sentences else text
    if tail.strip():
        sentences.append(tail.strip())
    return [s.strip() for s in sentences if s.strip()]


def sentence_ending(sentence: str) -> str:
    trimmed = sentence.strip()
    if not trimmed:
        return ""
    # 句読点や引用符を除いた末尾2文字
    trimmed = trimmed.rstrip("。！？!?")
    trimmed = trimmed.rstrip("」" )
    return trimmed[-2:] if len(trimmed) >= 2 else trimmed


def detect_repeated_endings(sentences: Sequence[str], min_run: int = 3):
    issues = []
    run_start = 0
    current_ending = None
    run_length = 0

    for idx, sentence in enumerate(sentences):
        ending = sentence_ending(sentence)
        if ending and ending == current_ending:
            run_length += 1
        else:
            if current_ending and run_length >= min_run:
                issues.append((run_start, run_length, current_ending))
            current_ending = ending
            run_start = idx
            run_length = 1

    if current_ending and run_length >= min_run:
        issues.append((run_start, run_length, current_ending))
    return issues


def split_paragraphs(text: str) -> List[str]:
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def is_dialogue_block(block: str) -> bool:
    stripped = block.strip()
    return stripped.startswith("「") or stripped.endswith("」")


def detect_subject_gaps(blocks: Sequence[str], names: Sequence[str], min_chars: int = 40):
    if not names:
        return []

    issues = []
    for idx, block in enumerate(blocks):
        if len(block) < min_chars:
            continue
        if is_dialogue_block(block):
            continue
        if not any(name in block for name in names):
            preview = block.replace("\n", " ")[:60]
            issues.append((idx + 1, preview))
    return issues


def detect_language_issues(text: str):
    issues = []
    lines = text.splitlines()
    for idx, line in enumerate(lines, 1):
        for match in ASCII_WORD_PATTERN.finditer(line):
            word = match.group()
            if word.upper() in ALLOWED_ASCII:
                continue
            issues.append(f"英単語らしき表現 '{word}' (行{idx}) - 日本語で言い換えてください。")
            break
        if any(ch in line for ch in FORBIDDEN_CHARS):
            issues.append(f"記号（◯/△/×など）が行{idx}に含まれています。音声用に言葉で説明してください。")
    return issues


def main():
    parser = argparse.ArgumentParser(description="人生の道標 台本セルフチェック補助ツール")
    parser.add_argument("path", type=pathlib.Path, help="チェックしたい台本ファイル")
    parser.add_argument("--names", nargs="*", default=[], help="主語チェックに使う登場人物名のリスト")
    parser.add_argument("--min-paragraph-chars", type=int, default=40, help="主語チェック対象とする段落の最小文字数")
    args = parser.parse_args()

    text = load_text(args.path)

    sentences = extract_sentences(text)
    ending_runs = detect_repeated_endings(sentences)

    blocks = split_paragraphs(text)
    subject_gaps = detect_subject_gaps(blocks, args.names, args.min_paragraph_chars)
    language_issues = detect_language_issues(text)

    print("==== 語尾多様化チェック ====")
    if not ending_runs:
        print("OK: 同一語尾が3連続以上の箇所は見つかりませんでした。")
    else:
        for start, length, ending in ending_runs:
            sample = sentences[start:start + length]
            preview = " / ".join(s[:30] + ("…" if len(s) > 30 else "") for s in sample)
            print(f"NG: {ending!r}が{length}連続 (文番号 {start + 1}-{start + length}): {preview}")

    print("\n==== 主語明示チェック ====")
    if not subject_gaps:
        print("OK: 指定された名前を含まない長めの地の文ブロックはありません。")
    else:
        for idx, preview in subject_gaps:
            print(f"注意: 段落{idx}に登場人物名がありません -> {preview}")

    print("\n==== 記号・言語チェック ====")
    if not language_issues:
        print("OK: 英単語・記号の問題は検出されませんでした。")
    else:
        for issue in language_issues:
            print(f"注意: {issue}")

    print("\n==== 概要 ====")
    print(f"総文数: {len(sentences)} / 段落数: {len(blocks)}")
    if args.names:
        print(f"主語チェック対象名: {', '.join(args.names)}")


if __name__ == "__main__":
    main()
