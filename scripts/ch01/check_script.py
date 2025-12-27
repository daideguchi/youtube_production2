#!/usr/bin/env python3
"""CH01（人生の道標）Aテキストセルフチェック補助ツール

Usage:
  python3 scripts/ch01/check_script.py path/to/assembled_human.md --names 主人公名 敵役名
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
    return data.replace("\r\n", "\n").replace("\r", "\n")


def extract_sentences(text: str) -> List[str]:
    sentences = SENTENCE_PATTERN.findall(text)
    tail = text[text.rfind(sentences[-1]) + len(sentences[-1]) :] if sentences else text
    if tail.strip():
        sentences.append(tail.strip())
    return [s.strip() for s in sentences if s.strip()]


def sentence_ending(sentence: str) -> str:
    trimmed = sentence.strip()
    if not trimmed:
        return ""
    trimmed = trimmed.rstrip("。！？!?")
    trimmed = trimmed.rstrip("」")
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


def main() -> None:
    parser = argparse.ArgumentParser(description="CH01 Aテキスト セルフチェック補助ツール")
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
            sample = sentences[start : start + length]
            preview = " / ".join(s[:30] + ("…" if len(s) > 30 else "") for s in sample)
            print(f"NG: 文末 '{ending}' が {length} 回連続 (文{start+1}〜{start+length})")
            print(f"  例: {preview}")

    print("\n==== 主語（登場人物名）チェック ====")
    if not args.names:
        print("SKIP: --names 未指定")
    elif not subject_gaps:
        print("OK: 指定した登場人物名が各段落に含まれています（セリフ除外）。")
    else:
        for para_idx, preview in subject_gaps[:20]:
            print(f"NG: 段落{para_idx} に主語候補が見当たりません: {preview}…")
        if len(subject_gaps) > 20:
            print(f"...（他 {len(subject_gaps) - 20} 件）")

    print("\n==== 記号/英単語チェック ====")
    if not language_issues:
        print("OK: 明らかな英単語/禁止記号は見つかりませんでした。")
    else:
        for item in language_issues:
            print(f"NG: {item}")


if __name__ == "__main__":
    main()

