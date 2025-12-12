from __future__ import annotations

import json
import re
from pathlib import Path
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
import difflib
import unicodedata
LLM_LOG_PATH = Path(__file__).resolve().parents[2] / "logs" / "tts_llm_usage.log"

def get_router():  # pragma: no cover
    """Module-level router accessor for monkeypatching in tests."""
    try:
        from factory_common.llm_router import get_router as inner_get_router  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"LLM router not available for auditor: {exc}") from exc
    return inner_get_router()

from .mecab_tokenizer import tokenize_with_mecab
from .reading_dict import (
    ReadingEntry,
    export_words_for_word_dict,
    is_banned_surface,
    merge_channel_readings,
    normalize_reading_kana,
    is_safe_reading,
)
from .risk_utils import (
    collect_risky_candidates,
    is_hazard_surface,
    is_trivial_diff,
    load_hazard_terms,
    _normalize_voicevox_kana,
)
from .reading_structs import KanaPatch, RiskySpan, RubyToken, align_moras_with_tokens

REVIEW_PROMPT = """
You are a Lead Japanese B-Text Auditor.
There is a disagreement between the Dictionary (MeCab) and the AI Engine (Voicevox).
Your goal is to decide the correct reading based on strict standard Japanese context.

**Inputs per block:**
- `text`: ORIGINAL Display text (Context).
- `b_text`: Current Draft (Usually Kanji-mixed).
- `mecab_kana`: Standard Dictionary Reading (Reference A).
- `voicevox_kana`: Engine Predicted Reading (Reference B).

**Logic:**
1. **Compare Readings**:
   - `voicevox_kana` is what the engine WILL say if you don't fix it.
   - `mecab_kana` is the Standard Japanese Morphological Analysis.
   - **PRIORITY**: Trust `mecab_kana` (Standard) over `voicevox_kana` (AI Prediction) unless context explicitly demands a deviation.

2. **Critical Fixes (MUST FIX)**:
   - If `voicevox_kana` has wrong accent/intonation causing semantic drift (e.g. 辛い: Tsurai vs Karai) -> **MUST FIX**.
   - If `voicevox_kana` contains Latin/English chars -> **MUST FIX** (Convert to Kana).

3. **Output Rule**:
   - If `voicevox_kana` is CORRECT (matches standard reading OR appropriate context reading), return `b_text` AS IS (Keep Kanji).
   - If `voicevox_kana` is WRONG (misread), return the **Corrected Kana Reading** (not Kanji).

4. **Dictionary Learning**:
   - If you make a correction, add it to `learned_words`.

**Output JSON:**
{
  "blocks": [
    {"index": 0, "b_text": "..."}
  ],
  "learned_words": {
    "Word": "Reading"
  }
}
"""


def load_learning_dict() -> Dict[str, str]:
    path = Path("audio_tts_v2/configs/learning_dict.json")
    if path.exists():
        try:
            data = json.loads(path.read_text())
            return {k: v for k, v in data.items() if not is_banned_surface(k)}
        except Exception:
            return {}
    return {}


def save_learning_dict(new_entries: Dict[str, str]) -> None:
    path = Path("audio_tts_v2/configs/learning_dict.json")
    current = load_learning_dict()
    for k, v in new_entries.items():
        if is_banned_surface(k):
            continue
        current[k] = v
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_surface_map(text: str, mapping: Dict[str, str]) -> str:
    if not mapping:
        return text
    patched = text
    for surface in sorted(mapping, key=len, reverse=True):
        reading = mapping[surface]
        if surface in patched:
            patched = patched.replace(surface, reading)
    return patched


def _tokenize_block(block: Dict[str, object]) -> List[RubyToken]:
    raw_text = str(block.get("text") or block.get("raw_text") or "")
    toks: List[RubyToken] = []
    for tok in tokenize_with_mecab(raw_text):
        reading = tok.get("reading_mecab") or tok.get("surface") or ""
        toks.append(
            RubyToken(
                surface=str(tok.get("surface", "")),
                reading_hira=reading,
                token_index=int(tok.get("index", 0)),
                line_id=int(block.get("index", 0)),
            )
        )
    return toks


def _rebuild_b_text(tokens: List[RubyToken], overrides: Dict[int, str]) -> str:
    """Reconstruct b_text from tokens, applying katakana overrides by token_index."""
    parts: List[str] = []
    for t in tokens:
        if t.token_index in overrides:
            parts.append(str(overrides[t.token_index]))
        else:
            parts.append(t.surface)
    return "".join(parts)


def _mora_length(text: str) -> int:
    """Approximate mora length by counting characters (simple heuristic)."""
    return len(text or "")


_KATAKANA_RE = re.compile(r"^[ァ-ンヴー・ッ]+$")


def _to_katakana(text: str) -> str:
    """Convert hiragana to katakana (other chars unchanged)."""
    buf: List[str] = []
    for ch in str(text or ""):
        code = ord(ch)
        if 0x3041 <= code <= 0x3096:  # ぁ〜ゖ
            buf.append(chr(code + 0x60))
        else:
            buf.append(ch)
    return "".join(buf)


def _validate_override(
    surface: str,
    reading: str,
    request: Dict[str, object],
    tokens_by_block: Dict[int, List[RubyToken]],
    surface_tokens: Dict[str, List[Tuple[int, int]]],
) -> Tuple[bool, str]:
    """Best-effort guardrail for LLM outputs to avoid harmful patches."""

    if not surface or not reading:
        return False, "empty"
    if is_banned_surface(surface):
        return False, "banned_surface"

    reading_kata = _to_katakana(reading)
    if not _KATAKANA_RE.match(reading_kata):
        return False, "non_kana"

    # Use first token occurrence for heuristics
    positions = surface_tokens.get(surface) or []
    token = None
    for bid, tidx in positions:
        token = next((t for t in tokens_by_block.get(bid, []) if t.token_index == tidx), None)
        if token:
            break

    expected_kata = _to_katakana(str(token.reading_hira or token.surface)) if token else ""
    if expected_kata:
        if abs(_mora_length(expected_kata) - _mora_length(reading_kata)) > 2:
            return False, "mora_mismatch"

    mecab_kana = _to_katakana(str(request.get("mecab_kana") or expected_kata or ""))
    # Reject wildly different readings (length-based) even if surface is hazard
    if mecab_kana:
        if abs(_mora_length(mecab_kana) - _mora_length(reading_kata)) > 3:
            return False, "mora_gap_vs_mecab"

    return True, ""


def _build_ruby_requests(
    surface_meta: Dict[str, Dict[str, object]],
    surface_tokens: Dict[str, List[Tuple[int, int]]],
    *,
    max_items: int = 400,
    max_calls: int = 80,
    include_level_b: bool = False,
) -> List[Dict[str, object]]:
    """
    Build surface-level items for Ruby LLM:
    - レベルA優先、Bは余裕があれば
    - surfaceごとの代表文脈・mecab_kana/voicevox_kana_normを保持
    - 対応する block_id/token_index のリストを保持（後段でパッチ適用用）
    - max_calls を考慮して max_items を切り詰める（calls<=2 前提）
    """
    requests: List[Dict[str, object]] = []
    level_a: List[Tuple[str, Dict[str, object]]] = []
    level_b: List[Tuple[str, Dict[str, object]]] = []
    for surface_key, meta in surface_meta.items():
        surface = str(meta.get("surface") or surface_key)
        lvl = meta.get("level") or "B"
        if lvl == "A":
            level_a.append((surface, meta))
        else:
            level_b.append((surface, meta))

    def _append(pairs):
        nonlocal requests
        for surface, meta in pairs:
            if len(requests) >= max_items:
                break
            hazard_tags = meta.get("hazard_tags") or []
            if not any(str(t).startswith("hazard:") for t in hazard_tags):
                # レベルA以外はレベルBとしてのみ扱う
                pass
            if is_banned_surface(surface):
                continue
            vv_norm = meta.get("voicevox_kana_norm") or ""
            mecab_kana = meta.get("mecab_kana") or ""
            # trivial な差なら送らない
            if vv_norm and mecab_kana and is_trivial_diff(mecab_kana, vv_norm):
                continue
            suspicion_score = float(meta.get("suspicion_score") or 0.0)
            context_list = meta.get("contexts") or []
            positions = surface_tokens.get(surface) or []
            request = {
                "surface": surface,
                "mecab_kana": mecab_kana,
                "voicevox_kana": meta.get("voicevox_kana") or "",
                "voicevox_kana_norm": vv_norm,
                "hazard_tags": hazard_tags,
                "suspicion_score": suspicion_score,
                "contexts": context_list[:3],
                "positions": positions,
            }
            requests.append(request)

    _append(level_a)
    if include_level_b and len(requests) < max_items:
        _append(level_b)

    if len(requests) > max_items:
        requests = requests[:max_items]
    # さらにコール数上限に基づいて切り詰める（1バッチ=20件、calls<=2なら最大40件）
    batch_size = 20
    allowed = batch_size * max_calls
    if len(requests) > allowed:
        requests = requests[:allowed]
    return requests


def _apply_ruby_overrides(
    requests: List[Dict[str, object]],
    *,
    blocks_by_index: Dict[int, Dict[str, object]],
    tokens_by_block: Dict[int, List[RubyToken]],
    max_terms: int,
    max_calls: int,
) -> Tuple[
    int,
    int,
    bool,
    Optional[str],
    Dict[int, List[KanaPatch]],
    Dict[Tuple[int, int], str],
    int,
    int,
    List[Dict[str, object]],
]:
    """Call LLM to get ruby overrides and convert to KanaPatch per block.

    Returns (llm_calls, terms_used, budget_exceeded, patches_by_block)
    """
    patches_by_block: Dict[int, List[KanaPatch]] = {}
    fallback_reasons: Dict[Tuple[int, int], str] = {}
    if not requests:
        return 0, 0, False, None, patches_by_block, fallback_reasons, 0, 0, []

    # ハード上限（コードに埋め込み）: Rubyは最大3コール/60件まで
    HARD_MAX_CALLS = 3
    HARD_MAX_TERMS = 60

    budget_exceeded = False
    budget_reason: Optional[str] = None
    # 呼び出し側指定とハード上限の小さい方を採用
    use_max_calls = min(HARD_MAX_CALLS, max_calls) if max_calls > 0 else HARD_MAX_CALLS
    use_max_terms = min(HARD_MAX_TERMS, max_terms) if max_terms > 0 else HARD_MAX_TERMS
    enforce_terms = use_max_terms > 0
    enforce_calls = use_max_calls > 0

    batch_size = 20
    router = get_router()
    llm_calls = 0
    terms_used = 0
    # surface -> correct_kana
    overrides: Dict[str, str] = {}
    rejected_surfaces: Dict[str, str] = {}
    ruby_logs: List[Dict[str, object]] = []
    request_map: Dict[str, Dict[str, object]] = {str(req.get("surface") or ""): req for req in requests}
    # surface -> positions [(block_id, token_index)]
    positions_map: Dict[str, List[Tuple[int, int]]] = {}
    for req in requests:
        surface = req.get("surface") or ""
        positions_map[surface] = req.get("positions") or []

    for i in range(0, len(requests), batch_size):
        if enforce_calls and llm_calls >= use_max_calls:
            budget_exceeded = True
            budget_reason = budget_reason or "ruby_calls"
            break
        if enforce_terms and terms_used >= use_max_terms:
            budget_exceeded = True
            budget_reason = budget_reason or "ruby_terms"
            break
        batch = requests[i : i + batch_size]
        payload = {"items": batch}
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a Japanese reading auditor. For each item (surface + MeCab/VOICEVOX readings + brief contexts), "
                    "judge whether the VOICEVOX reading is natural for humans. If it is natural, return decision=ok. "
                    "If it is clearly wrong, return decision=ng and provide correct_kana in Katakana. "
                    "Do NOT rewrite sentences. Accept VOICEVOX-friendly variations (long vowels, softened vowels). "
                    "If unsure, prefer ok (do not change). Output JSON: "
                    "{\"items\":[{\"surface\":str,\"decision\":\"ok|ng|skip\",\"correct_kana\":str}]}"
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            call_with_raw = getattr(router, "call_with_raw", None)
            if callable(call_with_raw):
                resp = call_with_raw(
                    task="tts_reading",
                    messages=messages,
                    max_tokens=4000,
                    timeout=120,
                    response_format="json_object",
                )
                content = resp.get("content")
                meta = {k: resp.get(k) for k in ("request_id", "model", "provider", "latency_ms", "usage")}
                # log meta to shared tts log
                if meta:
                    try:
                        LLM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                        with LLM_LOG_PATH.open("a", encoding="utf-8") as f:
                            f.write(json.dumps({"task": "tts_reading", **meta}, ensure_ascii=False) + "\n")
                        if os.getenv("TTS_LLM_LOG_STDOUT") == "1":
                            print(
                                "[LLM_META]",
                                "task=tts_reading",
                                f"model={meta.get('model')}",
                                f"provider={meta.get('provider')}",
                                f"request_id={meta.get('request_id')}",
                                f"latency_ms={meta.get('latency_ms')}",
                                f"usage={meta.get('usage')}",
                            )
                    except Exception:
                        pass
            else:
                content = router.call(
                    task="tts_reading",
                    messages=messages,
                    max_tokens=4000,
                    timeout=120,
                    response_format="json_object",
                )
            llm_calls += 1
            try:
                parsed = json.loads(content)
            except Exception:
                print("[WARN] Ruby LLM response empty/invalid; skipping batch")
                continue
            for item in parsed.get("items", []) or []:
                surface = item.get("surface") or ""
                decision = str(item.get("decision") or "").lower()
                reading = item.get("correct_kana") or ""
                if not surface:
                    continue
                req = request_map.get(surface, {})
                hazard_tags = req.get("hazard_tags") or []

                pos_list = positions_map.get(surface) or []
                first_pos = pos_list[0] if pos_list else (None, None)
                suspicion_score = float(req.get("suspicion_score") or 0.0)

                if decision != "ng":
                    ruby_logs.append(
                        {
                            "surface": surface,
                            "decision": decision or "ok",
                            "correct_kana": "",
                            "validation": "n/a",
                            "hazard_tags": hazard_tags,
                            "suspicion_score": suspicion_score,
                            "block_id": first_pos[0],
                            "token_index": first_pos[1],
                            "mecab_kana": req.get("mecab_kana", ""),
                            "voicevox_kana": req.get("voicevox_kana", ""),
                            "voicevox_kana_norm": req.get("voicevox_kana_norm", ""),
                        }
                    )
                    continue
                if not reading:
                    ruby_logs.append(
                        {
                            "surface": surface,
                            "decision": decision,
                            "correct_kana": "",
                            "validation": "reject:empty_reading",
                            "hazard_tags": hazard_tags,
                            "suspicion_score": suspicion_score,
                            "block_id": first_pos[0],
                            "token_index": first_pos[1],
                            "mecab_kana": req.get("mecab_kana", ""),
                            "voicevox_kana": req.get("voicevox_kana", ""),
                            "voicevox_kana_norm": req.get("voicevox_kana_norm", ""),
                        }
                    )
                    continue

                ok, reason = _validate_override(surface, reading, req, tokens_by_block, positions_map)
                if not ok:
                    rejected_surfaces[surface] = reason
                    ruby_logs.append(
                        {
                            "surface": surface,
                            "decision": decision,
                            "correct_kana": reading,
                            "validation": f"reject:{reason}",
                            "hazard_tags": hazard_tags,
                            "suspicion_score": suspicion_score,
                            "block_id": first_pos[0],
                            "token_index": first_pos[1],
                            "mecab_kana": req.get("mecab_kana", ""),
                            "voicevox_kana": req.get("voicevox_kana", ""),
                            "voicevox_kana_norm": req.get("voicevox_kana_norm", ""),
                        }
                    )
                    continue
                if enforce_terms and terms_used >= use_max_terms:
                    budget_exceeded = True
                    budget_reason = budget_reason or "ruby_terms"
                    break
                overrides[surface] = reading
                terms_used += 1
                ruby_logs.append(
                    {
                        "surface": surface,
                            "decision": decision,
                            "correct_kana": reading,
                            "validation": "accepted",
                            "hazard_tags": hazard_tags,
                            "suspicion_score": suspicion_score,
                            "block_id": first_pos[0],
                            "token_index": first_pos[1],
                            "mecab_kana": req.get("mecab_kana", ""),
                            "voicevox_kana": req.get("voicevox_kana", ""),
                            "voicevox_kana_norm": req.get("voicevox_kana_norm", ""),
                        }
                    )
        except Exception as e:
            # Fail fast: do not proceed if Ruby LLM fails
            raise RuntimeError(f"[AUDIT_RUBY] batch failed: {e}") from e

    applied_surfaces_set = set(overrides.keys())
    # Build KanaPatch per surface using positions
    for surface, reading in overrides.items():
        positions = positions_map.get(surface) or []
        for bid, tidx in positions:
            tokens = tokens_by_block.get(bid) or []
            block = blocks_by_index.get(bid) or {}
            tok = next((t for t in tokens if t.token_index == tidx), None)
            if tok is None:
                continue
            accent_phrases = block.get("accent_phrases") or []
            aligned = align_moras_with_tokens(accent_phrases, tokens)
            alignment_available = bool(accent_phrases)
            flat_len = sum(len(moras) for _, moras in aligned)

            # Find mora range from alignment
            mora_start = 0
            mora_end = 0
            used_fallback = False
            for t_aligned, moras in aligned:
                if t_aligned.token_index == tok.token_index:
                    mora_end = mora_start + len(moras)
                    break
                mora_start += len(moras)

            # Fallback: if alignment failed, approximate by length
            if mora_end <= mora_start:
                used_fallback = True
                start = sum(
                    _mora_length(t.reading_hira or t.surface)
                    for t, _ in aligned
                    if t.token_index < tok.token_index
                )
                end = start + _mora_length(reading)
                mora_start, mora_end = start, min(end, flat_len if flat_len > 0 else end)

            patch = KanaPatch(
                block_id=bid,
                token_index=tok.token_index,
                mora_range=(max(0, mora_start), max(mora_start, mora_end)),
                correct_kana=reading,
                correct_moras=list(reading),
            )
            patches_by_block.setdefault(bid, []).append(patch)
            if used_fallback or not alignment_available:
                fallback_reasons[(bid, tok.token_index)] = "align_fallback" if used_fallback else "align_missing"

    applied_surfaces = len(applied_surfaces_set)
    rejected_surfaces_count = len(rejected_surfaces)

    return (
        llm_calls,
        terms_used,
        budget_exceeded,
        budget_reason,
        patches_by_block,
        fallback_reasons,
        applied_surfaces,
        rejected_surfaces_count,
        ruby_logs,
    )


def _looks_like_kanji(surface: str) -> bool:
    return bool(surface and re.search(r"[一-龯々〆ヵヶ]", surface))

# ------------------ Suspicious reading detection helpers ------------------

# Tunable thresholds
KANA_SUSPICIOUS_SIMILARITY_MAX = 0.8
KANA_SUSPICIOUS_MORA_DIFF_MIN = 1
KANA_SUSPICIOUS_MORA_LEN_MIN = 4


def _normalize_kana_strict(s: str) -> str:
    """Normalize kana for comparison: NFKC, hiragana->katakana, remove separators."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s)
    res = []
    for ch in s:
        cp = ord(ch)
        if 0x3041 <= cp <= 0x3096:  # hiragana -> katakana
            res.append(chr(cp + 0x60))
        else:
            res.append(ch)
    s = "".join(res)
    remove = set("/'_ 　-ー")
    return "".join(ch for ch in s if ch not in remove)


def _mora_like_len(kana: str) -> int:
    """Rough mora length (character count after normalization)."""
    return len(kana or "")


def calc_kana_mismatch_score(mecab_kana: str, voicevox_kana_norm: str) -> Tuple[float, int, int]:
    """
    Compute similarity and length differences between MeCab and Voicevox readings.
    Returns (similarity 0..1, mora_diff, len_diff).
    """
    mec = _normalize_kana_strict(mecab_kana or "")
    vv = _normalize_kana_strict(voicevox_kana_norm or "")
    if not mec and not vv:
        return 1.0, 0, 0
    similarity = difflib.SequenceMatcher(None, mec, vv).ratio()
    mora_diff = abs(_mora_like_len(mec) - _mora_like_len(vv))
    len_diff = abs(len(mec) - len(vv))
    return similarity, mora_diff, len_diff


def is_suspicious_reading(mecab_kana: str, voicevox_kana_norm: str, surface: str) -> Tuple[bool, float]:
    """
    Heuristically decide if a reading difference is likely "clearly wrong".
    Returns (is_suspicious, suspicion_score).
    """
    similarity, mora_diff, _ = calc_kana_mismatch_score(mecab_kana, voicevox_kana_norm)

    # Ignore very short surfaces (揺れが大きい)
    if len(surface or "") <= 1:
        return False, 0.0

    # Too short readings are noisy
    if _mora_like_len(_normalize_kana_strict(mecab_kana or "")) < KANA_SUSPICIOUS_MORA_LEN_MIN:
        return False, 0.0

    if similarity <= KANA_SUSPICIOUS_SIMILARITY_MAX and mora_diff >= KANA_SUSPICIOUS_MORA_DIFF_MIN:
        return True, 1.0 - similarity
    return False, 0.0


def _build_vocab_requests(
    risky_spans: Sequence[RiskySpan],
    *,
    tokens_by_block: Dict[int, List[RubyToken]],
    blocks_by_index: Dict[int, Dict[str, object]],
    max_examples: int = 3,
) -> List[Dict[str, object]]:
    grouped: Dict[str, Dict[str, object]] = {}

    for span in risky_spans:
        # 語彙LLMも hazard レベルA のみに限定（hazard: 前置きがあるもの）
        reason = str(span.reason or "")
        if not reason.startswith("hazard:"):
            continue
        tokens = tokens_by_block.get(span.line_id, [])
        tok = next((t for t in tokens if t.token_index == span.token_index), None)
        if tok is None:
            continue
        surface = tok.surface
        block = blocks_by_index.get(span.line_id, {})

        if surface not in grouped:
            grouped[surface] = {
                "surface": surface,
                "mecab_reading": tok.reading_hira or tok.surface,
                "voicevox_kana": block.get("voicevox_kana", ""),
                "examples": [],
                "reasons": set(),
            }

        entry = grouped[surface]
        entry["reasons"].add(span.reason)

        # Keep a few representative contexts
        if len(entry["examples"]) < max_examples:
            context = block.get("text") or block.get("b_text") or ""
            entry["examples"].append(context)

    vocab_requests: List[Dict[str, object]] = []
    for payload in grouped.values():
        vocab_requests.append(
            {
                "surface": payload["surface"],
                "mecab_kana": payload.get("mecab_reading", ""),
                "voicevox_kana": payload.get("voicevox_kana", ""),
                "examples": payload.get("examples", []),
                "reasons": sorted(payload.get("reasons", [])),
            }
        )
    return vocab_requests


def _select_candidates(
    blocks: List[Dict[str, object]],
    *,
    channel_dict: Optional[Dict[str, Dict[str, object]]] = None,
    hazard_dict: Optional[Iterable[str]] = None,
) -> Tuple[
    List[Dict[str, object]],
    List[RiskySpan],
    Dict[int, List[RubyToken]],
    Dict[int, Dict[str, object]],
    Dict[str, Dict[str, object]],
]:
    hazard_dict = set(hazard_dict or load_hazard_terms())
    channel_surface_map = export_words_for_word_dict(channel_dict or {})
    learned = load_learning_dict()

    # surfaceレベルで代表文脈と hazardタグを持たせるための集計
    surface_meta: Dict[str, Dict[str, object]] = {}
    # manual force list to guarantee inspection of critical terms
    force_surfaces = set(["微調整", "肩甲骨"])

    candidates: List[Dict[str, object]] = []
    risky_spans: List[RiskySpan] = []
    tokens_by_block: Dict[int, List[RubyToken]] = {}
    blocks_by_index: Dict[int, Dict[str, object]] = {}
    surface_tokens: Dict[str, List[Tuple[int, int]]] = {}

    for b in blocks:
        txt = b.get("b_text", "")
        for k, v in learned.items():
            if k in txt:
                txt = txt.replace(k, v)
        patched_txt = _apply_surface_map(txt, channel_surface_map)
        if patched_txt != txt:
            b["b_text"] = patched_txt
            b["audit_needed"] = False
            continue

        if not b.get("audit_needed", True):
            continue

        mecab_kana = str(b.get("mecab_kana", ""))
        voicevox_kana = str(b.get("voicevox_kana", ""))

        # 短文かつ機械一致ならスキップ（LLMに送らない）
        if len(str(txt).strip()) <= 6 and is_trivial_diff(mecab_kana, voicevox_kana):
            b["audit_needed"] = False
            continue

        if is_trivial_diff(mecab_kana, voicevox_kana):
            b["audit_needed"] = False
            continue

        tokens = _tokenize_block(b)
        block_id = int(b.get("index", len(tokens_by_block)))
        tokens_by_block[block_id] = tokens
        blocks_by_index[block_id] = b
        ruby_map: Dict[int, str] = {}
        risky = collect_risky_candidates(tokens, ruby_map, hazard_dict, channel_surface_map.keys())
        # force specific surfaces into risky (e.g., 微調整, 肩甲骨)
        for tok in tokens:
            if tok.surface in force_surfaces and not is_banned_surface(tok.surface):
                risky.append(
                    RiskySpan(
                        line_id=block_id,
                        token_index=tok.token_index,
                        risk_score=1.0,
                        reason="hazard:force_surface",
                        surface=tok.surface,
                    )
                )
        # align Voicevox moras to tokens for auto suspicious detection
        vv_token_kana: Dict[int, str] = {}
        try:
            accent_phrases = b.get("accent_phrases") or []
            aligned = align_moras_with_tokens(accent_phrases, tokens)
            for tok, moras in aligned:
                vv_token_kana[tok.token_index] = "".join(moras)
        except Exception:
            vv_token_kana = {}

        # 文脈依存や1文字など禁止語はLLMに送らない
        risky = [span for span in risky if not is_banned_surface(span.surface)]

        # If we did not find explicit risky spans but the block is still marked audit_needed,
        # fall back to kanji-containing tokens so we do not miss semantic drifts.
        if not risky and not any(is_hazard_surface(t.surface, hazard_dict) for t in tokens):
            risky = [
                RiskySpan(
                    line_id=int(b.get("index", 0)),
                    token_index=t.token_index,
                    risk_score=0.5,
                    reason="block_diff",
                    surface=t.surface,
                )
                for t in tokens
                if _looks_like_kanji(t.surface) and t.surface not in channel_surface_map
            ]

        # Auto suspicious: add tokens whose MeCab vs Voicevox reading differ greatly
        auto_spans: List[RiskySpan] = []
        for tok in tokens:
            vv_kana_tok = vv_token_kana.get(tok.token_index)
            mec_read = tok.reading_hira or tok.surface
            # If alignment missing, still force-check long kanji tokens
            if not vv_kana_tok:
                if _looks_like_kanji(tok.surface) and len(tok.surface) >= 2 and not is_banned_surface(tok.surface):
                    auto_spans.append(
                        RiskySpan(
                            line_id=block_id,
                            token_index=tok.token_index,
                            risk_score=1.0,
                            reason="hazard:auto_missing_vv",
                            surface=tok.surface,
                        )
                    )
                continue
            is_susp, score = is_suspicious_reading(mec_read, vv_kana_tok, tok.surface)
            if is_susp and not is_banned_surface(tok.surface):
                auto_spans.append(
                    RiskySpan(
                        line_id=block_id,
                        token_index=tok.token_index,
                        risk_score=score if score > 0 else 1.0,
                        reason="hazard:auto_suspicious",
                        surface=tok.surface,
                    )
                )
        if auto_spans:
            risky.extend(auto_spans)

        if not risky:
            b["audit_needed"] = False
            continue

        # surface単位で代表文脈とhazardタグを収集（LLM入力用）
        for span in risky:
            meta = surface_meta.setdefault(
                span.surface,
                {
                    "surface": span.surface,
                    "hazard_tags": set(),
                    "contexts": [],
                    "mecab_kana": str(b.get("mecab_kana") or ""),
                    "voicevox_kana": str(b.get("voicevox_kana") or ""),
                    "voicevox_kana_norm": _normalize_voicevox_kana(str(b.get("voicevox_kana") or "")),
                    "suspicion_score": 0.0,
                },
            )
            surface_tokens.setdefault(span.surface, []).append((span.line_id, span.token_index))
            meta["hazard_tags"].add(span.reason or "")
            if hasattr(span, "risk_score") and span.risk_score:
                try:
                    meta["suspicion_score"] = max(meta.get("suspicion_score") or 0.0, float(span.risk_score))
                except Exception:
                    pass
            ctx_list = meta["contexts"]
            if isinstance(ctx_list, list) and len(ctx_list) < 3:
                ctx_list.append(str(b.get("text") or b.get("b_text") or ""))
            # レベル付け（A/B/Cの簡易タグ付与）
            reason = str(span.reason or "")
            # A: hazard: prefix
            # B: block_diff（Twin-Engine差分のみ）
            if reason.startswith("hazard:"):
                meta["level"] = "A"
            elif reason == "block_diff":
                meta.setdefault("level", "B")
            else:
                meta.setdefault("level", "B")

        risky_spans.extend(risky)
        candidates.append(b)

    # hazard_tags をセットからリストに戻す
    for meta in surface_meta.values():
        meta["hazard_tags"] = sorted(meta.get("hazard_tags") or [])

    return candidates, risky_spans, tokens_by_block, blocks_by_index, surface_meta, surface_tokens


def audit_blocks(
    blocks: List[Dict],
    *,
    channel: Optional[str] = None,
    video: Optional[str] = None,
    channel_dict: Optional[Dict[str, Dict[str, object]]] = None,
    hazard_dict: Optional[Iterable[str]] = None,
    max_vocab_terms: Optional[int] = None,
    max_llm_calls: Optional[int] = None,
    max_ruby_terms: Optional[int] = None,
    max_ruby_calls: Optional[int] = None,
    enable_vocab: bool = False,
) -> Tuple[List[Dict], Dict[int, List[KanaPatch]], int, int, bool]:
    """
    Audits blocks using LLM + Twin-Engine Consensus.
    ONLY sends blocks to LLM if `audit_needed` is True.
    Returns (blocks, patches_by_block, llm_calls, vocab_term_count, budget_exceeded)
    """

    if not blocks:
        return [], {}, 0, 0, False

    channel_dict = channel_dict or {}

    candidates, risky_spans, tokens_by_block, blocks_by_index, surface_meta, surface_tokens = _select_candidates(
        blocks, channel_dict=channel_dict, hazard_dict=hazard_dict
    )
    reason_map = {(span.line_id, span.token_index): span.reason for span in risky_spans}
    if not candidates:
        print("[AUDIT] All blocks achieved Consensus. No LLM Audit needed.")
        return blocks, {}, 0, 0, False

    vocab_requests: List[Dict[str, object]] = []
    vocab_term_count = 0
    if enable_vocab:
        pass  # vocabリクエストは現状なし（surface集約後に必要なら再導入）

    # Soft limit (fail-safe). None means no limit.
    MAX_VOCAB_TERMS = int(max_vocab_terms or 0)
    MAX_LLM_CALLS = int(max_llm_calls or 0)
    MAX_RUBY_TERMS = int(max_ruby_terms or 0)
    # allow generous defaults unless explicitly capped
    MAX_RUBY_CALLS = int(max_ruby_calls or 80)

    budget_exceeded = False
    batch_size = 40
    router = get_router()
    llm_calls = 0
    learned_words: Dict[str, str] = {}

    # 1) Ruby overrides for hazardous tokens（ゲートのみ、ソフト上限あり）
    RUBY_MAX_ITEMS = max_ruby_terms if max_ruby_terms else 400
    ruby_requests = _build_ruby_requests(
        surface_meta,
        surface_tokens,
        max_items=RUBY_MAX_ITEMS,
        max_calls=MAX_RUBY_CALLS if MAX_RUBY_CALLS else 80,
        include_level_b=False,  # デフォルトではレベルBを送らない（危険語のみ）
    )
    (
        ruby_calls,
        ruby_terms,
        ruby_budget,
        ruby_budget_reason,
        ruby_patches,
        ruby_fallbacks,
        ruby_surfaces,
        ruby_rejected,
        ruby_logs,
    ) = _apply_ruby_overrides(
        ruby_requests,
        blocks_by_index=blocks_by_index,
        tokens_by_block=tokens_by_block,
        max_terms=min(RUBY_MAX_ITEMS, MAX_RUBY_TERMS) if MAX_RUBY_TERMS else RUBY_MAX_ITEMS,
        max_calls=min(2, MAX_RUBY_CALLS) if MAX_RUBY_CALLS else 2,
    )
    llm_calls += ruby_calls
    vocab_term_count += ruby_terms
    budget_exceeded = budget_exceeded or ruby_budget
    budget_reasons: List[str] = []
    if ruby_budget and ruby_budget_reason:
        budget_reasons.append(ruby_budget_reason)

    # 2) 語彙LLM（残差）—ソフト上限あり、禁止語は適用しない
    for i in range(0, len(vocab_requests), batch_size):
        if MAX_LLM_CALLS > 0 and llm_calls >= MAX_LLM_CALLS:
            budget_exceeded = True
            budget_reasons.append("vocab_calls")
            break
        if MAX_VOCAB_TERMS > 0 and vocab_term_count >= MAX_VOCAB_TERMS:
            budget_exceeded = True
            budget_reasons.append("vocab_terms")
            break
        batch = vocab_requests[i : i + batch_size]
        payload = {"terms": batch}
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a Japanese reading adjudicator. Given surfaces with MeCab/VOICEVOX readings and brief contexts, "
                    "return JSON {\"terms\":[{\"surface\":str,\"reading_kana\":str,\"reading_hira\":str,\"accent_moras\":list|null}]}. "
                    "Prefer standard dictionary readings unless context demands otherwise. Always fill reading_kana in katakana."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]

        try:
            content = router.call(
                task="tts_reading",
                messages=messages,
                max_tokens=8000,
                timeout=120,
                response_format="json_object",
            )
            llm_calls += 1
            parsed = json.loads(content)
            for term in parsed.get("terms", []) or []:
                surface = term.get("surface")
                reading = term.get("reading_kana") or term.get("reading_hira")
                if surface and reading and not is_banned_surface(surface):
                    learned_words[surface] = reading
        except Exception as e:
            # Fail fast: do not proceed if vocab LLM fails
            raise RuntimeError(f"[AUDIT_ERROR] Vocab batch failed: {e}") from e

    if learned_words:
        voicevox_map = {
            str(req.get("surface")): str(req.get("voicevox_kana") or "")
            for req in vocab_requests
            if req.get("surface")
        }
        filtered_words: Dict[str, str] = {}
        for surface, reading in learned_words.items():
            surface_key = str(surface).strip()
            normalized = normalize_reading_kana(str(reading))
            if not is_safe_reading(normalized):
                continue
            if normalized == surface_key:
                # no-op override
                continue
            vv_kana = voicevox_map.get(surface_key) or ""
            if vv_kana and is_trivial_diff(normalized, vv_kana):
                # voicevox already reads it fine (trivial diff)
                continue
            filtered_words[surface_key] = normalized

        if filtered_words:
            channel_updates = {
                surface: ReadingEntry(
                    surface=surface,
                    reading_hira=reading,
                    reading_kana=reading,
                    voicevox_kana=voicevox_map.get(surface) or None,
                    source="llm",
                )
                for surface, reading in filtered_words.items()
            }
            save_learning_dict(filtered_words)
            if channel:
                channel_dict = merge_channel_readings(channel, channel_updates)
            else:
                channel_dict = channel_dict or {}
                for surface, entry in channel_updates.items():
                    channel_dict[surface] = entry.to_dict()

    # Apply resolved readings to all blocks.
    surface_map = export_words_for_word_dict(channel_dict or {})
    fixed_count = 0
    final_blocks: List[Dict[str, object]] = []
    for b in blocks:
        original = b.get("b_text", "")
        patched = _apply_surface_map(original, surface_map)
        if patched != original:
            b["b_text"] = patched
            fixed_count += 1
        b["audit_needed"] = False
        final_blocks.append(b)

    if fixed_count > 0:
        print(f"[AUDIT] Applied resolved readings to {fixed_count} blocks.")

    # Log ruby and vocab updates (best-effort)
    try:
        log_path = Path("logs/tts_voicevox_reading.jsonl")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().isoformat()
        records: List[Dict[str, object]] = []
        ruby_selected = len(ruby_requests)
        summary_reason = (
            f"summary:selected={ruby_selected},adopted={ruby_surfaces},rejected={ruby_rejected},calls={ruby_calls}"
        )
        records.append(
            {
                "timestamp": ts,
                "channel": channel,
                "video": video,
                "block_id": None,
                "token_index": None,
                "surface": "",
                "mecab_kana": "",
                "voicevox_kana": "",
                "ruby_kana": "",
                "after_kana": "",
                "mora_range": None,
                "source": "ruby_llm",
                "reason": summary_reason,
            }
        )
        # Budget overrun notice
        if budget_exceeded:
            records.append(
                {
                    "timestamp": ts,
                    "channel": channel,
                    "video": video,
                    "block_id": None,
                    "token_index": None,
                    "surface": "",
                    "mecab_kana": "",
                    "voicevox_kana": "",
                    "ruby_kana": "",
                    "after_kana": "",
                    "mora_range": None,
                    "source": "ruby_llm",
                    "reason": "budget_exceeded:" + ",".join(budget_reasons) if budget_reasons else "budget_exceeded",
                }
            )
        # Ruby per-item decisions (including ok/skip/reject/accepted)
        for item in ruby_logs:
            reason_bits = [f"decision={item.get('decision','')}"]
            if item.get("validation"):
                reason_bits.append(f"validation={item.get('validation')}")
            hazard_tags = item.get("hazard_tags") or []
            if hazard_tags:
                reason_bits.append("hazard=" + ",".join(hazard_tags))
            suspicion_score = item.get("suspicion_score", 0.0)
            rec = {
                "timestamp": ts,
                "channel": channel,
                "video": video,
                "block_id": item.get("block_id"),
                "token_index": item.get("token_index"),
                "surface": item.get("surface", ""),
                "mecab_kana": item.get("mecab_kana", ""),
                "voicevox_kana": item.get("voicevox_kana", ""),
                "voicevox_kana_norm": item.get("voicevox_kana_norm", ""),
                "ruby_kana": item.get("correct_kana", ""),
                "after_kana": item.get("correct_kana", ""),
                "mora_range": None,
                "source": "ruby_llm",
                "reason": "|".join(reason_bits),
                "suspicion_score": suspicion_score,
            }
            records.append(rec)

        # Ruby patches actually applied
        for bid, patches in (ruby_patches or {}).items():
            block = blocks_by_index.get(bid, {})
            mecab_kana = block.get("mecab_kana", "")
            vv_kana = block.get("voicevox_kana", "")
            for p in patches:
                surface = next(
                    (t.surface for t in tokens_by_block.get(bid, []) if t.token_index == p.token_index), ""
                )
                reason = reason_map.get((bid, p.token_index)) or "hazard"
                fb = ruby_fallbacks.get((bid, p.token_index))
                if fb:
                    reason = f"{reason}|{fb}"
                records.append(
                    {
                        "timestamp": ts,
                        "channel": channel,
                        "video": video,
                        "block_id": bid,
                        "token_index": p.token_index,
                        "surface": surface,
                        "mecab_kana": mecab_kana,
                        "voicevox_kana": vv_kana,
                        "ruby_kana": p.correct_kana,
                        "after_kana": p.correct_kana,
                        "mora_range": p.mora_range,
                        "source": "ruby_llm",
                        "reason": reason,
                    }
                )
        # Vocab updates
        for surface, reading in learned_words.items():
            records.append(
                {
                    "timestamp": ts,
                    "channel": channel,
                    "video": video,
                    "block_id": None,
                    "token_index": None,
                    "surface": surface,
                    "mecab_kana": "",
                    "voicevox_kana": "",
                    "ruby_kana": reading,
                    "after_kana": reading,
                    "mora_range": None,
                    "source": "vocab_llm",
                    "reason": "vocab_update",
                }
            )
        if records:
            with log_path.open("a", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

    return final_blocks, ruby_patches, llm_calls, vocab_term_count, budget_exceeded
