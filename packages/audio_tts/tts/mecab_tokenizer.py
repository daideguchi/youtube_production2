from __future__ import annotations

import re
from typing import Dict, List
import os
from pathlib import Path

# Ensure MeCab can find mecabrc (Homebrew on macOS ARM: /opt/homebrew/etc/mecabrc)
if not os.getenv("MECABRC"):
    for rc in ("/opt/homebrew/etc/mecabrc", "/usr/local/etc/mecabrc"):
        if Path(rc).exists():
            os.environ["MECABRC"] = rc
            break

SILENCE_TAG_PATTERN = re.compile(r"\[(\d+(?:\.\d+)?)\]")


def _mecab_parse(text: str):
    try:
        import MeCab  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("mecab-python3 is required") from exc
    tagger = MeCab.Tagger()
    tagger.parse("")
    node = tagger.parseToNode(text)
    while node:
        if node.stat in (MeCab.MECAB_BOS_NODE, MeCab.MECAB_EOS_NODE):
            node = node.next
            continue
        yield node
        node = node.next

_KATAKANA_RE = re.compile(r"^[ァ-ヴー・]+$")
_HIRAGANA_RE = re.compile(r"[ぁ-ゖ]")
_HIRAGANA_TO_KATAKANA = str.maketrans(
    {chr(h): chr(h + 0x60) for h in range(ord("ぁ"), ord("ゔ") + 1)}
)


def _normalize_reading(features: list[str], surface: str) -> str:
    """
    MeCabのfeatureから読みを取得。
    IPADIC(index 7,8) と UniDic(index 6,9等) の両方に対応するため、
    index 6以降をスキャンし、最後の「カタカナのみ」のフィールドを優先する。

    NOTE:
    - UniDic では index 6 付近に「語彙素（lemma）読み」が入ることがあり、
      これを採用すると活用形が辞書形になってしまう（例: 疲れ -> ツカレル）。
    - 後ろ側のフィールドには「表層（surface）読み/発音」が入るケースが多いため、
      最後のカタカナ候補を採用する。
    """
    # 検索範囲: 6〜14 (十分な深さ)
    # IPADIC: [6]=Base(Kanji), [7]=Reading(Kana), [8]=Pronunciation(Kana)
    # UniDic: [6]=Lemma(Kana), [9]=SurfaceReading(Kana) (common)
    limit = min(len(features), 15)

    def _coerce_katakana(raw: str) -> str | None:
        if not raw or raw == "*":
            return None
        if _KATAKANA_RE.match(raw):
            return raw
        if _HIRAGANA_RE.search(raw):
            converted = raw.translate(_HIRAGANA_TO_KATAKANA)
            if _KATAKANA_RE.match(converted):
                return converted
        return None

    # UniDic: prefer surface reading when present (index 9).
    if len(features) > 9:
        cand = _coerce_katakana(features[9])
        if cand:
            return cand

    # Common: scan reading/pron fields (skip index 6 lemma by default).
    for i in range(7, limit):
        cand = _coerce_katakana(features[i])
        if cand:
            return cand

    # Last resort: accept index 6 if it's the only kana-like candidate.
    if len(features) > 6:
        cand = _coerce_katakana(features[6])
        if cand:
            return cand

    # 見つからない場合はsurfaceを返す (漢字のままなど)
    return surface


def _tokenize_segment(segment: str, offset: int) -> List[Dict[str, object]]:
    """
    MeCabのsurfaceが改行などをスキップするため、char_start/char_end を
    元の文字列上の実位置に合わせる。segment 内で逐次検索し、ずれを防止。
    """
    tokens: List[Dict[str, object]] = []
    search_pos = 0
    for node in _mecab_parse(segment):
        surface = node.surface
        if not surface:
            continue
        pos_in_seg = segment.find(surface, search_pos)
        if pos_in_seg == -1:
            # フォールバック: 連続長さで積算（最悪ケース）
            pos_in_seg = search_pos
        start = offset + pos_in_seg
        end = start + len(surface)
        search_pos = pos_in_seg + len(surface)
        features = (node.feature or "").split(",")
        pos = features[0] if len(features) > 0 else ""
        subpos = features[1] if len(features) > 1 else ""
        reading = _normalize_reading(features, surface)
        tokens.append(
            {
                "index": len(tokens),
                "surface": surface,
                "base": features[6] if len(features) > 6 else surface,
                "pos": pos,
                "subpos": subpos,
                "reading_mecab": reading,
                "char_start": start,
                "char_end": end,
            }
        )
    return tokens


def tokenize_with_mecab(a_text: str) -> List[Dict[str, object]]:
    tokens: List[Dict[str, object]] = []
    last = 0
    index_counter = 0
    for m in SILENCE_TAG_PATTERN.finditer(a_text):
        if m.start() > last:
            seg_tokens = _tokenize_segment(a_text[last:m.start()], offset=last)
            for t in seg_tokens:
                t["index"] = index_counter
                index_counter += 1
                tokens.append(t)
        tokens.append(
            {
                "index": index_counter,
                "surface": m.group(0),
                "base": m.group(0),
                "pos": "silence_tag",
                "subpos": "",
                "reading_mecab": "",
                "char_start": m.start(),
                "char_end": m.end(),
            }
        )
        index_counter += 1
        last = m.end()
    if last < len(a_text):
        seg_tokens = _tokenize_segment(a_text[last:], offset=last)
        for t in seg_tokens:
            t["index"] = index_counter
            index_counter += 1
            tokens.append(t)
    return tokens
