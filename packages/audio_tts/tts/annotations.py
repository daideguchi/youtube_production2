from __future__ import annotations

from typing import Dict, List, Tuple
import re


def build_prompt_payload(original_text: str, tokens: List[Dict[str, object]], kana_engine_normalized: str) -> Dict[str, object]:
    slim_tokens: List[Dict[str, object]] = []
    for t in tokens:
        slim_tokens.append(
            {
                "index": t.get("index"),
                "surface": t.get("surface"),
                "reading_mecab": t.get("reading_mecab"),
                "pos": t.get("pos"),
            }
        )
    return {
        "original_text": original_text,
        "tokens": slim_tokens,
        "kana_engine_normalized": kana_engine_normalized,
    }


# --- Risky token detection utilities ---
_KANA_RE = re.compile(r"^[ぁ-ゟァ-ヿー]+$")
_KANJI_RE = re.compile(r"[一-龯]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_DIGIT_RE = re.compile(r"\d")
_DANGEROUS_UNITS = {"％", "%", "円", "万", "億", "兆", "人", "回", "年", "日", "時間", "分", "秒"}
# プロジェクトで頻出しそうな曖昧語を最低限リスト化（必要に応じて拡張）
_AMBIGUOUS_WORDS = {"怒り", "生", "上手", "重ねる", "供養", "菩薩", "悟り", "観音"}


def _has_latin(text: str) -> bool:
    return _LATIN_RE.search(text) is not None


def _has_digit(text: str) -> bool:
    return _DIGIT_RE.search(text) is not None


def _has_kanji(text: str) -> bool:
    return _KANJI_RE.search(text) is not None


def is_safe_token(token: Dict[str, object]) -> bool:
    surface = str(token.get("surface", ""))
    pos = str(token.get("pos", ""))

    # 記号・句読点は安全
    if surface in {"。", "、", "，", "．", "？", "！", "・", "「", "」", "『", "』", "（", "）", "(", ")", "［", "］", "[", "]", "…", "—", "-", "─", "〜"}:
        return True

    # ひらがな/カタカナのみ → 安全
    if _KANA_RE.fullmatch(surface):
        return True

    # アルファベットを含む → 危険
    if _has_latin(surface):
        return False

    # 数字を含む → 危険寄り（単独1桁以外）
    if _has_digit(surface):
        if len(surface) <= 2 and surface.isdigit():
            return True
        for u in _DANGEROUS_UNITS:
            if u in surface:
                return False
        return False

    # 固有名詞は危険
    if "固有名詞" in pos:
        return False

    # 漢字が含まれない → だいたい安全
    if not _has_kanji(surface):
        return True

    # 曖昧語は危険
    if surface in _AMBIGUOUS_WORDS:
        return False

    # 短い漢字（1-2文字）は安全寄り
    if len(surface) <= 2:
        return True

    # それ以外の漢字語は危険寄り
    return False


def split_safe_and_risky(tokens: List[Dict[str, object]]) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    safe: List[Dict[str, object]] = []
    risky: List[Dict[str, object]] = []
    for t in tokens:
        if is_safe_token(t):
            safe.append(t)
        else:
            risky.append(t)
    return safe, risky


def build_risky_candidates(a_text: str, tokens: List[Dict[str, object]], window: int = 30) -> List[Dict[str, object]]:
    """
    危険トークンだけを LLM に渡すための候補を作成する。
    context は前後 window 文字を抜き出して簡易文脈として付与。
    """
    _, risky = split_safe_and_risky(tokens)
    candidates: List[Dict[str, object]] = []
    for t in risky:
        idx = int(t.get("index", len(candidates)))
        surface = str(t.get("surface", ""))
        reading_mecab = str(t.get("reading_mecab", ""))
        start = int(t.get("char_start", 0))
        end = int(t.get("char_end", start))
        ctx_start = max(0, start - window)
        ctx_end = min(len(a_text), end + window)
        context = a_text[ctx_start:ctx_end]
        candidates.append(
            {
                "index": idx,
                "surface": surface,
                "reading_mecab": reading_mecab,
                "context": context,
            }
        )
    return candidates


def validate_llm_response(payload: Dict[str, object]) -> List[Dict[str, object]]:
    if not isinstance(payload, dict):
        raise ValueError("LLM response must be an object")
    anns = payload.get("token_annotations")
    if not isinstance(anns, list):
        raise ValueError("token_annotations must be a list")
    validated: List[Dict[str, object]] = []
    for ann in anns:
        if not isinstance(ann, dict):
            raise ValueError("annotation must be object")
        if "index" not in ann or "write_mode" not in ann:
            raise ValueError("annotation missing index/write_mode")
        validated.append(
            {
                "index": int(ann["index"]),
                "surface": ann.get("surface"),
                "llm_reading_kana": ann.get("llm_reading_kana"),
                "write_mode": ann.get("write_mode"),
                "risk_level": ann.get("risk_level"),
                "reason": ann.get("reason", ""),
                "reading_mecab": ann.get("reading_mecab"),
            }
        )
    return validated
