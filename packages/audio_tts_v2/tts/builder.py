from __future__ import annotations

from typing import Dict, List, Iterable, Tuple

_BOUNDARY_CHARS = set("。．.!！?？\n，、")


def _katakana_to_hiragana(text: str) -> str:
    table = {code: code - 0x60 for code in range(0x30A1, 0x30FA + 1)}
    return text.translate(table)


def build_b_text(
    a_text: str,
    tokens: List[Dict[str, object]],
    annotations: List[Dict[str, object]],
    ruby_hints: Dict[int, str] | None = None,
) -> Tuple[str, List[Dict[str, object]]]:
    ann_map = {int(a["index"]): a for a in annotations}
    tokens_sorted = sorted(tokens, key=lambda t: int(t.get("char_start", 0)))
    parts: List[str] = []
    log: List[Dict[str, object]] = []
    cursor = 0
    # 強制ルール（LLM出力に依存せず置換）
    force_original = {"ので", "要注意"}
    skip_tokens = {"オープニング", "クロージング"}
    special_katakana = {
        "AI": "エーアイ",
        "ＴＴＳ": "ティーティーエス",
        "TTS": "ティーティーエス",
        "GPT-5-MINI": "ジーピーティー5ミニ",
        "GPT-5": "ジーピーティー5",
        "GPT": "ジーピーティー",
    }
    risk_terms = {
        "生成": "セイセイ",
        "学校": "ガッコウ",  # 誤読防止（学校は必ず「ガッコウ」と読む）
    }
    prev_surface = ""
    prev_is_digit = False
    kanji_numerals = {"一", "二", "三", "四", "五", "六", "七", "八", "九", "十"}
    for tok in tokens_sorted:
        start = int(tok.get("char_start", 0))
        end = int(tok.get("char_end", start))
        surface = str(tok.get("surface", ""))
        idx = int(tok.get("index", len(log)))
        pos = str(tok.get("pos", ""))
        subpos = str(tok.get("subpos", ""))
        if start > cursor:
            parts.append(a_text[cursor:start])
        ann = ann_map.get(idx, {})
        mode = str(ann.get("write_mode", "original"))
        reading = str(ann.get("llm_reading_kana", "") or tok.get("reading_mecab", "") or surface)
        # If ruby hints exist, override reading with ruby hint (katakana/hiragana expected)
        if ruby_hints and idx in ruby_hints:
            reading = str(ruby_hints[idx])
            mode = "katakana"
        risk_level = int(ann.get("risk_level", 1) or 1)
        surface_upper = surface.upper()
        is_digit = surface.isdigit()  # 漢数字は別扱い
        is_kanji_num = surface in kanji_numerals
        is_counter = surface in {"分", "日", "月", "年"} and prev_is_digit

        # 無音タグやセクションタグ、ラベル語は読み上げない
        if (
            pos == "silence_tag"
            or (surface.startswith("[") and surface.endswith("]"))
            or surface in {"［", "］"}
            or surface in skip_tokens
        ):
            cursor = end
            prev_surface = surface
            continue

        # 強制ルール: 「ので」系は原文保持（分割された「の」「で」にも適用）
        if surface in force_original or (prev_surface == "の" and surface == "で"):
            mode = "original"
            reading = surface
        # 英略語・モデル名の表記統一
        elif surface_upper in special_katakana:
            mode = "katakana"
            reading = special_katakana[surface_upper]
        elif surface in risk_terms:
            mode = "katakana"
            reading = risk_terms[surface]
        else:
            # デフォルトでは漢字混じりを優先し、リスクが低いものは原文を維持する。
            # ただしアノテーションで明示的に write_mode が指定されている場合は尊重する。
            is_ascii_or_digit = any("0" <= c <= "9" or "A" <= c <= "Z" or "a" <= c <= "z" for c in surface)
            explicit_write_mode = "write_mode" in ann
            if (
                mode != "original"
                and not explicit_write_mode
                and risk_level <= 1
                and not is_ascii_or_digit
                and not is_digit
                and not is_counter
            ):
                mode = "original"
                reading = surface

        if mode == "hiragana":
            rep = _katakana_to_hiragana(reading)
        elif mode == "katakana":
            rep = reading
        else:
            rep = surface
        parts.append(rep)
        log.append({"index": idx, "original_fragment": surface, "replaced_fragment": rep, "write_mode": mode})
        cursor = end
        prev_surface = surface
        prev_is_digit = is_digit
    if cursor < len(a_text):
        parts.append(a_text[cursor:])
    b_text = "".join(parts)

    # 既知の不自然表記を後処理で補正
    replace_map = {
        "ジーピーティー-ファイブ-ミニ": "ジーピーティー5ミニ",
        "ジーピーティー-5-ミニ": "ジーピーティー5ミニ",
        "ジーピーティー-ご-ミニ": "ジーピーティー5ミニ",
        "ティー・ティー・エス": "ティーティーエス",
        "のだようちゅうい": "のでようちゅうい",
        "実運用": "じつうんよう",
        "3分": "さんぷん",
    }
    for bad, good in replace_map.items():
        if bad in b_text:
            b_text = b_text.replace(bad, good)

    return b_text, log


def _split_on_boundaries(text: str) -> List[str]:
    segments: List[str] = []
    buf: List[str] = []
    for ch in text:
        buf.append(ch)
        if ch in _BOUNDARY_CHARS:
            segments.append("".join(buf))
            buf = []
    if buf:
        segments.append("".join(buf))
    return segments


def _force_split(segment: str, max_len: int) -> Iterable[str]:
    for i in range(0, len(segment), max_len):
        yield segment[i : i + max_len]


def chunk_b_text(b_text: str, max_len: int = 140) -> List[Dict[str, object]]:
    segments = _split_on_boundaries(b_text.replace("\r\n", "\n"))
    chunks: List[str] = []
    current = ""
    for seg in segments:
        # 強制分割（句読点で区切っても長すぎる場合）
        if len(seg) > max_len:
            # 現在のバッファを吐き出し
            if current:
                chunks.append(current)
                current = ""
            for forced in _force_split(seg, max_len):
                chunks.append(forced)
            continue
        # 通常フロー
        if len(current) + len(seg) <= max_len:
            current += seg
            continue
        if current:
            chunks.append(current)
            current = ""
        current = seg
    if current:
        chunks.append(current)
    return [{"index": i, "text": c} for i, c in enumerate(chunks)]
