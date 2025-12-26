from __future__ import annotations

import re
from typing import Dict, List

SILENCE_TAG_PATTERN = re.compile(r"\[(\d+(?:\.\d+)?)\]")


def _strip_short_quotes(text: str, max_len: int = 6) -> str:
    """
    「……」が過剰に入っていると語りが途切れるため、短い引用のみ括弧を外す。
    - max_len 以内かつ句読点を含まないものだけを対象にし、長文引用は残す。
    """
    def repl(match: re.Match[str]) -> str:
        inner = match.group(1)
        if len(inner) <= max_len and not re.search(r"[。、．，.!！?？]", inner):
            return inner
        return match.group(0)

    return re.sub(r"「([^」]{1,64})」", repl, text)


def _strip_heading_hashes(text: str) -> str:
    """
    視聴者向けSRTにはMarkdown記号（##など）を残さない。
    行頭の #*, および句読点直後に続く ## を除去してプレーン化する。
    """
    # 行頭（または改行直後）の # を除去
    text = re.sub(r"(^|\r?\n)\s*#+\s*", r"\1", text)
    # 句読点や終端記号の直後に続く # を除去
    text = re.sub(r"([。．！!？\?])\s*#+\s*", r"\1", text)
    return text


def _strip_markdown(line: str) -> str:
    """軽微なMarkdown体裁を字幕/音声前処理で除去する."""
    # 見出し/リストのマーカーを除去
    line = re.sub(r"^\s*#+\s*", "", line)
    line = re.sub(r"^\s*[-*+]\s+", "", line)
    # 強調/コードの装飾を剥がす
    line = re.sub(r"\*\*(.*?)\*\*", r"\1", line)
    line = re.sub(r"__(.*?)__", r"\1", line)
    line = re.sub(r"`([^`]*)`", r"\1", line)
    return line


def preprocess_a_text(a_text: str, strip_markdown: bool = True) -> Dict[str, object]:
    meta: Dict[str, List[Dict[str, object]]] = {"silence_tags": [], "warnings": []}
    cleaned_raw = a_text.lstrip("\ufeff").strip()
    
    if strip_markdown:
        # Markdown的な体裁を軽く掃除（見出し/リスト/強調/コード）
        cleaned = "\n".join(_strip_markdown(line) for line in cleaned_raw.splitlines())
        # 追加の体裁補正: 見出し記号/過剰な短い鉤括弧を除去
        cleaned = _strip_heading_hashes(cleaned)
    else:
        # Markdown除去スキップ（構造解析用）
        cleaned = cleaned_raw

    cleaned = _strip_short_quotes(cleaned)
    for idx, ch in enumerate(cleaned):
        if ch in ("\n", "\r", "\t"):
            continue
        if ord(ch) < 32 or (0x7F <= ord(ch) < 0xA0):
            meta["warnings"].append({"type": "control_char", "position": idx, "char_code": ord(ch)})
    for m in SILENCE_TAG_PATTERN.finditer(cleaned):
        meta["silence_tags"].append({"tag": m.group(0), "char_start": m.start(), "char_end": m.end()})
    return {"a_text": cleaned, "meta": meta}
