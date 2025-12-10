from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from factory_common.llm_router import get_router

from .mecab_tokenizer import tokenize_with_mecab
from .reading_dict import ReadingEntry, export_words_for_word_dict, merge_channel_readings
from .risk_utils import collect_risky_candidates, is_hazard_surface, is_trivial_diff, load_hazard_terms
from .reading_structs import RiskySpan, RubyToken

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
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}


def save_learning_dict(new_entries: Dict[str, str]) -> None:
    path = Path("audio_tts_v2/configs/learning_dict.json")
    current = load_learning_dict()
    current.update(new_entries)
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


def _looks_like_kanji(surface: str) -> bool:
    return bool(surface and re.search(r"[一-龯々〆ヵヶ]", surface))


def _build_vocab_requests(
    risky_spans: Sequence[RiskySpan],
    *,
    tokens_by_block: Dict[int, List[RubyToken]],
    blocks_by_index: Dict[int, Dict[str, object]],
    max_examples: int = 3,
) -> List[Dict[str, object]]:
    grouped: Dict[str, Dict[str, object]] = {}

    for span in risky_spans:
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
) -> Tuple[List[Dict[str, object]], List[RiskySpan], Dict[int, List[RubyToken]], Dict[int, Dict[str, object]]]:
    hazard_dict = set(hazard_dict or load_hazard_terms())
    channel_surface_map = export_words_for_word_dict(channel_dict or {})
    learned = load_learning_dict()

    candidates: List[Dict[str, object]] = []
    risky_spans: List[RiskySpan] = []
    tokens_by_block: Dict[int, List[RubyToken]] = {}
    blocks_by_index: Dict[int, Dict[str, object]] = {}

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

        if is_trivial_diff(mecab_kana, voicevox_kana):
            b["audit_needed"] = False
            continue

        tokens = _tokenize_block(b)
        block_id = int(b.get("index", len(tokens_by_block)))
        tokens_by_block[block_id] = tokens
        blocks_by_index[block_id] = b
        ruby_map: Dict[int, str] = {}
        risky = collect_risky_candidates(tokens, ruby_map, hazard_dict, channel_surface_map.keys())

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

        if not risky:
            b["audit_needed"] = False
            continue

        risky_spans.extend(risky)
        candidates.append(b)

    return candidates, risky_spans, tokens_by_block, blocks_by_index


def audit_blocks(
    blocks: List[Dict],
    *,
    channel: Optional[str] = None,
    channel_dict: Optional[Dict[str, Dict[str, object]]] = None,
    hazard_dict: Optional[Iterable[str]] = None,
) -> Tuple[List[Dict], int, int]:
    """
    Audits blocks using LLM + Twin-Engine Consensus.
    ONLY sends blocks to LLM if `audit_needed` is True.
    Returns (blocks, llm_calls, vocab_term_count)
    """

    if not blocks:
        return [], 0, 0

    channel_dict = channel_dict or {}

    candidates, risky_spans, tokens_by_block, blocks_by_index = _select_candidates(
        blocks, channel_dict=channel_dict, hazard_dict=hazard_dict
    )
    if not candidates:
        print("[AUDIT] All blocks achieved Consensus. No LLM Audit needed.")
        return blocks, 0, 0

    vocab_requests = _build_vocab_requests(
        risky_spans, tokens_by_block=tokens_by_block, blocks_by_index=blocks_by_index
    )
    vocab_term_count = len(vocab_requests)
    if not vocab_requests:
        print("[AUDIT] No vocab requests after gating. Skipping LLM.")
        return blocks, 0, 0

    batch_size = 40
    router = get_router()
    llm_calls = 0
    learned_words: Dict[str, str] = {}

    for i in range(0, len(vocab_requests), batch_size):
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
                if surface and reading:
                    learned_words[surface] = reading
        except Exception as e:
            print(f"[AUDIT_ERROR] Vocab batch failed: {e}. Skipping this batch.")

    if learned_words:
        channel_updates = {
            surface: ReadingEntry(surface=surface, reading_hira=reading, reading_kana=reading, source="llm")
            for surface, reading in learned_words.items()
        }
        save_learning_dict(learned_words)
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

    return final_blocks, llm_calls, vocab_term_count
