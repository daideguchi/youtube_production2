from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from factory_common.llm_router import get_router

from .builder import _katakana_to_hiragana


# --- Data classes ---


@dataclass
class RubyToken:
    surface: str
    reading_hira: Optional[str] = None
    reading_kana_candidates: Optional[List[str]] = None
    token_index: int = 0
    line_id: int = 0
    char_range: Optional[Tuple[int, int]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "RubyToken":
        return cls(
            surface=str(data.get("surface", "")),
            reading_hira=str(data.get("reading_hira")) if data.get("reading_hira") is not None else None,
            reading_kana_candidates=list(data.get("reading_kana_candidates", []) or []),
            token_index=int(data.get("token_index", data.get("index", 0) or 0)),
            line_id=int(data.get("line_id", 0)),
            char_range=tuple(data.get("char_range", ())) or None,
        )

    def to_dict(self) -> Dict[str, object]:
        out: Dict[str, object] = {
            "surface": self.surface,
            "reading_hira": self.reading_hira,
            "reading_kana_candidates": self.reading_kana_candidates or [],
            "token_index": self.token_index,
            "line_id": self.line_id,
        }
        if self.char_range:
            out["char_range"] = list(self.char_range)
        return out


@dataclass
class RubyLine:
    line_id: int
    text: str
    tokens: List[RubyToken] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "RubyLine":
        toks = [RubyToken.from_dict(t) for t in data.get("tokens", []) or []]
        return cls(line_id=int(data.get("line_id", 0)), text=str(data.get("text", "")), tokens=toks)

    def to_dict(self) -> Dict[str, object]:
        return {
            "line_id": self.line_id,
            "text": self.text,
            "tokens": [t.to_dict() for t in self.tokens],
        }


@dataclass
class RubyInfo:
    lines: List[RubyLine]
    raw_llm_payload: Optional[Dict[str, object]] = None


@dataclass
class RiskySpan:
    line_id: int
    token_index: int
    risk_score: float
    reason: str
    mora_range: Optional[Tuple[int, int]] = None
    surface: str = ""


@dataclass
class KanaPatch:
    block_id: int
    token_index: int
    mora_range: Tuple[int, int]
    correct_kana: str
    correct_moras: Optional[List[str]] = None


# --- Helpers ---


def _hiragana_to_katakana(text: str) -> str:
    table = {code: code + 0x60 for code in range(0x3041, 0x3097)}
    return text.translate(table)


def _normalize_katakana(text: str) -> str:
    if not text:
        return ""
    # Allow callers to pass in hiragana or katakana; normalize to katakana for comparison
    if any("\u3040" <= ch <= "\u309f" for ch in text):
        return _hiragana_to_katakana(text)
    return text


# --- Core functions ---


def call_llm_for_ruby(lines: Sequence[RubyLine], *, timeout: int = 120) -> RubyInfo:
    """
    Ask LLM for ruby (furigana) hints per token.
    - Accepts a list of RubyLine; each line has tokens with surface + optional reading_hira.
    - Returns RubyInfo with RubyTokens updated (reading_hira) and raw payload preserved.
    """

    router = get_router()
    serialized_lines = [ln.to_dict() for ln in lines]

    system_prompt = (
        "You are a Japanese ruby (furigana) generator. "
        "Return ONLY JSON with a 'lines' array. Each line contains 'line_id' and 'rubies' where "
        "'rubies' holds objects {token_index:int, ruby_katakana:string}."
    )
    user_payload = json.dumps({"lines": serialized_lines}, ensure_ascii=False)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_payload},
    ]

    raw_payload: Optional[Dict[str, object]] = None
    ruby_map: Dict[Tuple[int, int], str] = {}

    try:
        content = router.call(
            task="tts_reading",
            messages=messages,
            response_format="json_object",
            timeout=timeout,
        )
        raw_payload = json.loads(content)
        for line in raw_payload.get("lines", []) or []:
            lid = int(line.get("line_id", 0))
            for ruby in line.get("rubies", []) or []:
                idx = int(ruby.get("token_index", 0))
                reading = str(ruby.get("ruby_katakana") or ruby.get("reading") or "")
                if reading:
                    ruby_map[(lid, idx)] = reading
    except Exception:
        # Fallback to existing readings
        pass

    updated_lines: List[RubyLine] = []
    for line in lines:
        new_tokens: List[RubyToken] = []
        for tok in line.tokens:
            key = (tok.line_id, tok.token_index)
            if key in ruby_map:
                katakana = ruby_map[key]
                hira = _katakana_to_hiragana(katakana)
                new_tokens.append(
                    RubyToken(
                        surface=tok.surface,
                        reading_hira=hira,
                        reading_kana_candidates=tok.reading_kana_candidates,
                        token_index=tok.token_index,
                        line_id=tok.line_id,
                        char_range=tok.char_range,
                    )
                )
            else:
                new_tokens.append(tok)
        updated_lines.append(RubyLine(line_id=line.line_id, text=line.text, tokens=new_tokens))

    return RubyInfo(lines=updated_lines, raw_llm_payload=raw_payload)


def align_moras_with_tokens(
    accent_phrases: Optional[Iterable[Dict[str, object]]],
    tokens: Sequence[Union[RubyToken, Dict[str, object]]],
) -> List[Tuple[RubyToken, List[str]]]:
    """
    Align VOICEVOX accent phrase moras with RubyTokens in order.
    This is a best-effort greedy alignment that slices mora stream based on token reading length.
    When mora stream is shorter than expected, remaining tokens get empty slices (to be handled by caller).
    """

    if accent_phrases is None:
        return []

    mora_stream: List[str] = []
    for phrase in accent_phrases:
        for mora in phrase.get("moras", []) or []:
            text = str(mora.get("text", ""))
            if text:
                mora_stream.append(text)

    aligned: List[Tuple[RubyToken, List[str]]] = []
    cursor = 0
    for raw_tok in tokens:
        tok = raw_tok if isinstance(raw_tok, RubyToken) else RubyToken.from_dict(raw_tok)
        reading = tok.reading_hira or tok.surface
        target_len = max(len(reading), 1)
        slice_moras = mora_stream[cursor : cursor + target_len]
        aligned.append((tok, slice_moras))
        cursor += target_len
    return aligned


def evaluate_reading_diffs(
    aligned: Sequence[Tuple[RubyToken, List[str]]],
    llm_judger_fn: Optional[Callable[[RubyToken, List[str], bool], bool]] = None,
) -> List[RiskySpan]:
    """
    Evaluate differences between token readings and VOICEVOX mora alignment.
    - If `llm_judger_fn` is provided, it can override whether a mismatch is risky.
    """

    risky: List[RiskySpan] = []
    for tok, moras in aligned:
        expected = _normalize_katakana(tok.reading_hira or tok.surface)
        actual = _normalize_katakana("".join(moras))
        mismatch = bool(expected and actual and expected != actual)
        if llm_judger_fn:
            try:
                mismatch = llm_judger_fn(tok, moras, mismatch)
            except Exception:
                pass
        if mismatch:
            risky.append(
                RiskySpan(
                    line_id=tok.line_id,
                    token_index=tok.token_index,
                    risk_score=1.0,
                    reason=f"voicevox:{actual} != expected:{expected}",
                    mora_range=(0, len(moras)),
                    surface=tok.surface,
                )
            )
    return risky
