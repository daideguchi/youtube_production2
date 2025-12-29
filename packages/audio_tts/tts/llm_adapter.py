from __future__ import annotations

import json
import os
import re
from typing import Dict, Optional, List, Any
from pathlib import Path

from factory_common.llm_router import LLMRouter
from factory_common.paths import logs_root

LOCAL_INFERENCE_ONLY = os.getenv("LOCAL_INFERENCE_ONLY") == "1"
router = LLMRouter()
LLM_LOG_PATH = logs_root() / "tts_llm_usage.log"

SYSTEM_PROMPT = (
    "You are a TTS annotation engine. Output ONLY a JSON object:\n"
    "{\"token_annotations\":[{\"index\":int,\"llm_reading_kana\":string,\"write_mode\":\"original|hiragana|katakana\",\"risk_level\":int,\"reason\":string}]}\"\n"
    "Rules:\n"
    "- risk_level must be 0,1,2,3 (small integer).\n"
    "- reason is optional, <=40 chars, no newlines, and must NOT contain '{' '}' '[' ']'.\n"
    "- No extra text, comments, or explanations."
)

KATAKANA_PROMPT = (
    "You convert Japanese A-text into full katakana reading (numbers also as katakana words). "
    "Do not add explanations. Return JSON {\"katakana\": \"...\"} only."
)

# Filter out trivial tokens (numbers/punctuation) to reduce LLM payload for annotate
# Allow digits/whitespace/punctuation; escape + and - properly for char class
_TRIVIAL_RE = re.compile(r"^[0-9０-９\\s\\.,，。、％%\\-\\+/]+$")


def _is_trivial_token(tok: dict) -> bool:
    surface = str(tok.get("surface") or "").strip()
    if not surface:
        return True
    # Pure numbers/punctuation/symbols
    return bool(_TRIVIAL_RE.match(surface))


def _log_llm_meta(task: str, meta: dict):
    if not meta:
        return
    try:
        LLM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {"task": task, **meta}
        with LLM_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        if os.getenv("TTS_LLM_LOG_STDOUT") == "1":
            print(
                "[LLM_META]",
                f"task={task}",
                f"model={meta.get('model')}",
                f"provider={meta.get('provider')}",
                f"request_id={meta.get('request_id')}",
                f"latency_ms={meta.get('latency_ms')}",
                f"usage={meta.get('usage')}",
            )
    except Exception:
        pass

SRT_SEGMENT_PROMPT = (
    "You split Japanese text into short readable segments for subtitles. "
    "Goal: 1) Keep natural sentence/pause boundaries, 2) Each segment length <= {max_len} characters, "
    "3) Prefer splitting at sentence endings (。．.!！?？). Avoid splitting at 読点/カンマ（、,） unless the segment would exceed the limit. "
    "4) Never split numbers/decimals/units/dates (e.g., 0.003, 12.5%、2025年5月、20億、3日、15%) — keep them intact within one segment. "
    "5) ALWAYS split headings/titles (e.g., \"第1章：...\", \"## ...\", \"結び：...\") into their OWN SEPARATE segment. Do NOT merge a heading with the body text that follows it. "
    "6) **CRITICAL**: Preserve original text exactly, INCLUDING Markdown header markers (e.g. '##', '#'). Do NOT remove them. "
    "Output JSON only: {{\"segments\": [{{\"index\":0,\"text\":\"...\"}}, ...]}} with indices in order."
)

PAUSE_PROMPT = (
    "You receive a list of subtitle segments (already ordered). "
    "Decide natural pause length (seconds) AFTER each segment for comfortable listening. "
    "Allowed values: 0.0, 0.25, 0.5, 0.75, 1.0 only. "
    "Guidelines: "
    "- Headings/section titles (例: \"第1章：発見の経緯1972年5月\" や \"#\", \"【…】\", \":\" を含む見出し) は途中で切らず1まとまりとして扱い、その直後にしっかり間を置く（0.5–0.75）。タイトル中の数字・日付が続く場合もまとめて読んでから間を置く。 "
    "- 文中で助詞や読点だけで終わる短いフレーズには大きな間を入れない。普通の文末なら 0.25–0.5、明確な区切りなら 0.5–0.75。 "
    "- 新しい段落/トピック/箇条書きの切り替えは 0.5–0.75。 "
    "- 明らかに文途中（接続詞・助詞・語尾未完）の場合のみ 0.0–0.25 を使う。 "
    "Return JSON only: {\"pauses\": [{\"index\":0,\"pause_sec\":0.5,\"reason\":\"...\"}, ...]} "
    "Include all segments (last one can be 0.0). "
    "Be concise and context-aware; longer pause for major topic shift, shorter for minor shift, none inside a clause."
)

SRT_LINEBREAK_PROMPT = (
    "You are a Japanese subtitle line-break formatter for SRT.\n"
    "Input is JSON:\n"
    "{\n"
    "  \"max_lines\": int,\n"
    "  \"max_chars_per_line\": int,\n"
    "  \"items\": [{\"index\": int, \"text\": string}, ...]\n"
    "}\n"
    "\n"
    "Task:\n"
    "- For each item, decide where to insert line breaks to maximize readability.\n"
    "- You MUST preserve the original text EXACTLY. Do not paraphrase. Do not change characters.\n"
    "- Only decide break positions; output as an array of lines.\n"
    "\n"
    "Hard constraints (must satisfy):\n"
    "- 1 <= lines.length <= max_lines\n"
    "- For every line: len(line) <= max_chars_per_line\n"
    "- CRITICAL: concatenation of output lines (\"\".join(lines)) must be EXACTLY equal to input text.\n"
    "- Do not introduce or remove spaces/punctuation/characters.\n"
    "\n"
    "Quality preferences (best-effort, do not get stuck):\n"
    "- Do not break in the middle of a word/meaning chunk.\n"
    "- Avoid starting a line with particles (は/が/を/に/で/と/も/へ/や/の) when possible.\n"
    "- Avoid starting a line with punctuation (、。！？）」』）など) when possible.\n"
    "- Avoid splitting numbers + units/dates/percent (例: 2025年, 12.5%, 3日, 10万円).\n"
    "- If there is no good break, return a single line.\n"
    "\n"
    "Output JSON only (no extra text):\n"
    "{\n"
    "  \"items\": [{\"index\": int, \"lines\": [string, ...]}, ...]\n"
    "}\n"
)

READING_SYSTEM_PROMPT = (
    "You are a Japanese reading disambiguation assistant.\n"
    "Given a sentence and candidate tokens, return only JSON: "
    "{\"readings\":[{\"index\":int,\"llm_reading_katakana\":string}]} "
    "Use full-width Katakana. Do not add extra text."
)

READING_USER_TEMPLATE = """
sentence_snippet: {sentence}
candidates_with_context: {candidates}
(Use each candidate's context primarily; sentence_snippet is just a small reference.)
"""

USER_TEMPLATE = """
Input JSON:
{payload}

Goal:
- For each token, return llm_reading_kana, write_mode (original|hiragana|katakana), risk_level (0-3), and optional reason (<=40 chars).
- Keep token order and index unchanged.
- Output ONLY the JSON object with token_annotations; no prose.
"""

B_TEXT_GEN_PROMPT = """You are a professional narrator script writer.
Your task is to convert the provided Japanese display text (A-Text) into a reading script (B-Text) optimized for Text-to-Speech.

**Rules:**
1. **Misreading Correction:** Convert difficult kanji, proper nouns, or ambiguous readings into explicit Katakana or Hiragana readings where necessary to ensure correct pronunciation by the TTS engine (e.g., \"明日\" -> \"あす\" if context implies formal, or \"明日\" -> \"あした\" if casual. \"本気\" -> \"マヂ\" if indicated).
2. **Pauses:** Insert pause tags `[wait=X.Xs]` (e.g., `[wait=0.5s]`) to create a natural, cinematic rhythm.
   - Insert pauses after headings, between major sections, and for dramatic effect.
   - Use `[wait=0.2s]` for short breaths, `[wait=0.5s]` for standard pauses, `[wait=1.0s]` for long pauses/transitions.
3. **No Semantic Changes:** Do NOT change the meaning or the words themselves unless correcting the *reading*. The B-Text must maintain a 1:1 semantic mapping with the A-Text.
4. **Format:** Return the B-Text as a raw string. Do not use JSON. Just the text stream.
5. **Headings:** Keep markdown headings (e.g. `## Chapter`) as they help structure, but you can add pauses after them.
"""

READING_GENERATION_PROMPT = (
    "You are a professional Japanese narrator. Prepare text for high-quality TTS (Voicevox/Voicepeak).\n"
    "Input: A list of text segments (already split).\n"
    "Output: JSON only: {\"readings\": [\"...\"]} 1:1 with input.\n"
    "Rules:\n"
    "- No Latin letters or %/&; convert them to Katakana (e.g., DNA -> ディーエヌエー, 100% -> ヒャクパーセント).\n"
    "- Preserve Kanji/Kana mix except for disambiguation; do not over-convert.\n"
    "- If input has Kanji(Reading), replace with Reading.\n"
    "- Remove middle dots (・) from names/titles; keep hashtags (#) as-is.\n"
)


def annotate_tokens(payload: Dict[str, object], model: str | None = None, api_key: str | None = None, timeout: int = 120) -> Dict[str, object]:
    if LOCAL_INFERENCE_ONLY:
        # (Local logic unchanged)
        tokens_in = payload.get("tokens") or []
        anns = []
        for i, t in enumerate(tokens_in):
            idx = int(t.get("index", i))
            surface = t.get("surface", "")
            reading = t.get("reading_mecab") or surface
            anns.append(
                {
                    "index": idx,
                    "surface": surface,
                    "llm_reading_kana": reading,
                    "write_mode": "original",
                    "risk_level": 1,
                    "reason": "",
                    "reading_mecab": t.get("reading_mecab"),
                }
            )
        return {"token_annotations": anns}

    tokens_in_all = payload.get("tokens") or []
    token_map = {int(t.get("index", i)): t for i, t in enumerate(tokens_in_all)}
    tokens_for_llm = [t for t in tokens_in_all if not _is_trivial_token(t)]
    payload_for_llm = dict(payload)
    payload_for_llm["tokens"] = tokens_for_llm

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(payload=json.dumps(payload_for_llm, ensure_ascii=False)),
        },
    ]
    
    last_err: BaseException | None = None
    last_raw: Optional[str] = None
    
    # Try calling via LLM router
    try:
        call_with_raw = getattr(router, "call_with_raw", None)
        if callable(call_with_raw):
            result = call_with_raw(
                task="tts_annotate",
                messages=messages,
                response_format="json_object",
                timeout=timeout,
            )
            content = result.get("content")
            _log_llm_meta("tts_annotate", {k: result.get(k) for k in ("request_id", "model", "provider", "latency_ms", "usage")})
        else:
            content = router.call(
                task="tts_annotate",
                messages=messages,
                response_format="json_object",
                timeout=timeout,
            )
        last_raw = content
        
        try:
            obj = _parse_json_strict(content)
        except Exception:
            try:
                obj = _parse_json_lenient(content)
            except Exception:
                obj = _parse_json_salvage(content)
        
        annotations = []
        if obj.get("token_annotations"):
            annotations = _enrich_annotations(obj, token_map)
        else:
            raise ValueError("missing token_annotations in parsed/salvaged response")

        # Fill missing indexes with defaults to keep alignment
        seen = {int(a.get("index")) for a in annotations if isinstance(a, dict) and "index" in a}
        missing = set(token_map.keys()) - seen
        for idx in sorted(missing):
            tok = token_map[idx]
            annotations.append(
                {
                    "index": idx,
                    "surface": tok.get("surface"),
                    "llm_reading_kana": tok.get("reading_mecab") or tok.get("surface") or "",
                    "write_mode": "original",
                    "risk_level": 0,
                    "reason": "",
                    "reading_mecab": tok.get("reading_mecab"),
                }
            )

        annotations = sorted(annotations, key=lambda x: int(x.get("index", 0)))
        return {"token_annotations": annotations}
        
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt):  # pragma: no cover
            raise
        last_err = e
        # Log failure
        if last_raw:
            try:
                from pathlib import Path
                log_path = logs_root() / "annot_raw_fail.json"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text(last_raw, encoding="utf-8")
            except Exception:
                pass

    # Fallback: defaults
    if os.getenv("TTS_LLM_LOG_STDOUT") == "1":
        print(f"[LLM_FALLBACK] annotate_tokens: {last_err}")
    defaults = []
    for idx, tok in token_map.items():
        defaults.append(
            {
                "index": idx,
                "surface": tok.get("surface"),
                "llm_reading_kana": tok.get("reading_mecab") or tok.get("surface") or "",
                "write_mode": "original",
                "risk_level": 1,
                "reason": "",
                "reading_mecab": tok.get("reading_mecab"),
            }
        )
    return {"token_annotations": defaults}


def llm_readings_for_candidates(
    sentence: str,
    candidates: list[dict],
    model: str | None = None,
    api_key: str | None = None,
    timeout: int = 60,
    batch_size: int = 12,
) -> dict[int, str]:
    if not candidates:
        return {}
    if LOCAL_INFERENCE_ONLY:
        out: dict[int, str] = {}
        for c in candidates:
            idx = int(c.get("index", 0))
            reading = c.get("reading_mecab") or c.get("surface") or ""
            out[idx] = str(reading)
        return out

    out: dict[int, str] = {}

    def _call(batch: list[dict]) -> dict[int, str]:
        sentence_snippet = sentence[:300] if sentence else ""
        user_payload = READING_USER_TEMPLATE.format(
            sentence=sentence_snippet,
            candidates=json.dumps(batch, ensure_ascii=False),
        )
        messages = [
            {"role": "system", "content": READING_SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ]
        
        try:
            call_with_raw = getattr(router, "call_with_raw", None)
            if callable(call_with_raw):
                result = call_with_raw(
                    task="tts_reading",
                    messages=messages,
                    response_format="json_object",
                    timeout=timeout,
                )
                content = result.get("content")
                _log_llm_meta("tts_reading", {k: result.get(k) for k in ("request_id", "model", "provider", "latency_ms", "usage")})
            else:
                content = router.call(
                    task="tts_reading",
                    messages=messages,
                    response_format="json_object",
                    timeout=timeout,
                )
            
            if isinstance(content, str):
                try:
                    obj = _parse_json_strict(content)
                except Exception:
                    obj = _parse_json_lenient(content)
                
                if obj.get("readings"):
                    return {int(it["index"]): str(it["llm_reading_katakana"]) for it in obj["readings"]}
                
                # Salvage
                import re
                pattern = re.compile(r'\{{[^{}]*"index"\s*:\s*(\d+)[^{}]*"llm_reading_katakana"\s*:\s*"([^"]+)"[^{}]*\}}')
                salvage = {}
                for m in pattern.finditer(content):
                    try:
                        idx = int(m.group(1))
                        kana = str(m.group(2))
                        salvage[idx] = kana
                    except Exception:
                        continue
                if salvage:
                    return salvage
            
            raise ValueError("missing readings")
        except Exception as e:
            raise ValueError(f"LLM reading parse failed: {e}")

    # Process in batches
    for i in range(0, len(candidates), batch_size):
        batch = candidates[i : i + batch_size]
        try:
            result = _call(batch)
            out.update(result)
        except Exception as e:
            print(f"[LLM_WARN] llm_readings_for_candidates batch failed: {e}")
            # partial failure tolerated

    return out


def katakana_a_text(a_text: str, model: str | None = None, api_key: str | None = None, timeout: int = 20, max_tokens: int = 6000) -> str:
    if LOCAL_INFERENCE_ONLY:
        return ""
    
    payload = {"a_text": a_text}
    messages = [
        {"role": "system", "content": KATAKANA_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    try:
        call_with_raw = getattr(router, "call_with_raw", None)
        if callable(call_with_raw):
            result = call_with_raw(
                task="tts_text_prepare",
                messages=messages,
                response_format="json_object",
                timeout=timeout,
                max_tokens=max_tokens,
            )
            content = result.get("content")
            _log_llm_meta("tts_text_prepare", {k: result.get(k) for k in ("request_id", "model", "provider", "latency_ms", "usage")})
        else:
            content = router.call(
                task="tts_text_prepare",
                messages=messages,
                response_format="json_object",
                timeout=timeout,
                max_tokens=max_tokens,
            )
        obj = json.loads(content)
        katakana = obj.get("katakana")
        if katakana:
            return str(katakana)
        print("[LLM_WARN] katakana missing in response")
    except Exception as e:
        print(f"[LLM_WARN] katakana_a_text failed: {e}")
    raise ValueError("katakana_a_text failed")


def suggest_pauses(blocks: list[dict], model: str | None = None, api_key: str | None = None, timeout: int = 30, batch_size: int = 20, pause_model_override: str | None = None) -> list[float]:
    if LOCAL_INFERENCE_ONLY:
        # (Heuristic logic unchanged)
        pauses: list[float] = []
        for blk in blocks:
            txt = str(blk.get("text", "")).strip()
            is_heading = txt.startswith(("第", "#", "【", "■", "◆", "●", "◇", "◎", "▼", "・"))
            ends_sentence = txt.endswith(("。", "．", ".", "！", "!", "？", "?"))
            ends_comma = txt.endswith(("、", "，", ","))
            length = len(txt)
            if is_heading:
                p = 0.8
            elif ends_sentence:
                p = 0.35
            elif ends_comma:
                p = 0.25
            else:
                p = 0.18 if length <= 20 else 0.22
            p = max(0.0, min(p, 0.8))
            pauses.append(p)
        return pauses

    all_pauses: dict[int, float] = {}

    def _call(batch: list[dict]) -> list[dict]:
        payload = {"segments": [{"index": b.get("index", i), "text": b.get("text", "")} for i, b in enumerate(batch)]}
        messages = [
            {"role": "system", "content": PAUSE_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        
        try:
            call_with_raw = getattr(router, "call_with_raw", None)
            if callable(call_with_raw):
                result = call_with_raw(
                    task="tts_pause",
                    messages=messages,
                    response_format="json_object",
                    timeout=timeout,
                )
                content = result.get("content")
                _log_llm_meta("tts_pause", {k: result.get(k) for k in ("request_id", "model", "provider", "latency_ms", "usage")})
            else:
                content = router.call(
                    task="tts_pause",
                    messages=messages,
                    response_format="json_object",
                    timeout=timeout,
                )
            
            try:
                obj = _parse_json_strict(content)
            except Exception:
                obj = _parse_json_lenient(content)
            
            if obj.get("pauses"):
                return obj["pauses"]
            
            # Salvage
            import re
            out = []
            pattern = re.compile(r'\{{[^{}]*"index"\s*:\s*(\d+)[^{}]*"pause_sec"\s*:\s*([0-9\.]+)[^{}]*\}}')
            for m in pattern.finditer(content):
                try:
                    out.append({"index": int(m.group(1)), "pause_sec": float(m.group(2))})
                except Exception:
                    continue
            if out:
                return out
                
            raise ValueError("missing pauses")
        except Exception as e:
            raise ValueError(f"pause suggestion failed: {e}")

    for i in range(0, len(blocks), batch_size):
        batch = blocks[i : i + batch_size]
        try:
            items = _call(batch)
            for it in items:
                idx = int(it.get("index", 0))
                val = float(it.get("pause_sec", 0.0))
                all_pauses[idx] = max(0.0, min(val, 1.0))
        except Exception as e:
            print(f"[LLM_WARN] suggest_pauses batch failed: {e}")

    pauses: list[float] = []
    for i, _ in enumerate(blocks):
        pauses.append(all_pauses.get(i, 0.0))
    # Pad or truncate
    if len(pauses) < len(blocks):
        pauses.extend([0.0] * (len(blocks) - len(pauses)))
    elif len(pauses) > len(blocks):
        pauses = pauses[: len(blocks)]
    return pauses


def format_srt_lines(entries: list[dict], model: str, api_key: str, target_len: int = 24, timeout: int = 30, batch_size: int = 20) -> list[dict]:
    """
    Format SRT cue texts by inserting intentional newlines (\\n) to improve readability.

    Safety contract:
    - Content MUST NOT change (no paraphrase). Only newline insertion is allowed.
    - If LLM output violates constraints or content equality, fall back to the original cue text.

    Tuning (env overrides):
    - SRT_LINEBREAK_ENABLED (default: 1)
    - SRT_LINEBREAK_MODE (default: heuristic)  # heuristic|llm
    - SRT_LINEBREAK_MAX_LINES (default: 2)
    - SRT_LINEBREAK_MAX_CHARS_PER_LINE (default: target_len)
    - SRT_LINEBREAK_RETRY_LIMIT (default: 1)
    """
    if not entries:
        return entries

    enabled = (os.getenv("SRT_LINEBREAK_ENABLED", "1") or "").strip().lower() not in ("0", "false", "no", "off")
    if not enabled:
        return entries

    mode = (os.getenv("SRT_LINEBREAK_MODE", "heuristic") or "").strip().lower()
    if mode in ("0", "false", "no", "off", "disabled"):
        return entries
    if mode not in ("heuristic", "llm"):
        mode = "heuristic"

    def _to_int(value: object, default: int) -> int:
        try:
            return int(str(value).strip())
        except Exception:
            return default

    max_lines = max(1, _to_int(os.getenv("SRT_LINEBREAK_MAX_LINES", "2"), 2))
    max_chars = max(4, _to_int(os.getenv("SRT_LINEBREAK_MAX_CHARS_PER_LINE", str(target_len)), int(target_len)))
    retry_limit = max(0, _to_int(os.getenv("SRT_LINEBREAK_RETRY_LIMIT", "1"), 1))
    bs = max(1, _to_int(batch_size, 20))

    # --- Heuristic line break (fast, deterministic, no LLM) ---
    _OPEN_BRACKETS = set("（(「『【〈《[")
    _CLOSE_BRACKETS = set("）)」』】〉》]")
    _BAD_LINE_START = set("、。，．。！？!?」』】）)〉》]")
    _SMALL_KANA = set("ぁぃぅぇぉゃゅょっァィゥェォャュョッー")
    _PARTICLES = set("はがをにでともへやの")
    _PUNCT_STRONG = set("。！？!?…")
    _PUNCT_WEAK = set("、，,;；:：")

    def _is_kanji(ch: str) -> bool:
        return "\u4e00" <= ch <= "\u9fff"

    def _choose_break_two_lines(text: str, *, hard_max: int, max_lines: int) -> str:
        """
        Insert at most 1 newline into `text` (which must NOT contain newlines).
        - Prefer punctuation boundaries near the center.
        - Avoid starting a line with punctuation/particles when possible.
        - Avoid splitting Kanji compounds when possible.
        """
        if max_lines <= 1:
            return text
        if "\n" in text or "\r" in text:
            text = text.replace("\r", "").replace("\n", "")
        n = len(text)
        if n <= hard_max:
            return text
        if max_lines != 2 or n < 2:
            return text

        center = n / 2.0
        hard_possible = n <= (hard_max * max_lines)

        def _valid(i: int) -> bool:
            if i <= 0 or i >= n:
                return False
            left_len = i
            right_len = n - i
            if left_len < 6 or right_len < 6:
                return False
            prev = text[i - 1]
            nxt = text[i]
            if nxt in _BAD_LINE_START or nxt in _SMALL_KANA:
                return False
            if prev in _OPEN_BRACKETS or prev in _SMALL_KANA:
                return False
            if hard_possible and (left_len > hard_max or right_len > hard_max):
                return False
            return True

        def _score(i: int) -> float:
            if not _valid(i):
                return 1e9
            left_len = i
            right_len = n - i
            prev = text[i - 1]
            nxt = text[i]
            max_len = max(left_len, right_len)

            s = 0.0
            s += abs(left_len - right_len) * 0.85
            s += abs(i - center) * 0.10

            if not hard_possible and max_len > hard_max:
                s += (max_len - hard_max) * 4.0

            if prev in _PUNCT_STRONG:
                s -= 10.0
            elif prev in _PUNCT_WEAK:
                s -= 6.0
            elif prev in _CLOSE_BRACKETS:
                s -= 2.0

            if _is_kanji(prev) and _is_kanji(nxt):
                s += 10.0

            if nxt in _PARTICLES:
                s += 3.5

            if prev.isascii() and nxt.isascii() and prev.isalnum() and nxt.isalnum():
                s += 6.0
            if prev.isdigit() and nxt.isdigit():
                s += 4.0

            return s

        best_i = min(range(1, n), key=_score)
        if _score(best_i) >= 1e8:
            return text
        return text[:best_i] + "\n" + text[best_i:]

    def _needs_rewrap(raw_text: str) -> bool:
        raw = str(raw_text or "")
        if not raw.strip():
            return False
        if "\r" in raw:
            return True
        if "\n" not in raw:
            return len(raw) > max_chars

        lines = [ln for ln in raw.replace("\r", "").split("\n") if ln != ""]
        if not lines:
            return False
        if len(lines) > max_lines:
            return True
        for ln in lines:
            if not ln:
                continue
            if ln[0] in _BAD_LINE_START or ln[0] in _SMALL_KANA:
                return True
            if ln[-1] in _OPEN_BRACKETS or ln[-1] in _SMALL_KANA:
                return True

        compact = "".join(lines)
        if len(lines) == 2 and lines[0] and lines[1]:
            if _is_kanji(lines[0][-1]) and _is_kanji(lines[1][0]):
                return True

        if len(compact) <= (max_chars * max_lines):
            if any(len(ln) > max_chars for ln in lines):
                return True
        return False

    def _format_srt_lines_heuristic(entries_in: list[dict]) -> list[dict]:
        for ent in entries_in:
            raw = str(ent.get("text", "") or "")
            if not raw.strip():
                continue
            if not _needs_rewrap(raw):
                if "\r" in raw:
                    ent["text"] = raw.replace("\r", "")
                continue
            compact = raw.replace("\r", "").replace("\n", "")
            rewritten = _choose_break_two_lines(compact, hard_max=max_chars, max_lines=max_lines)
            if rewritten.replace("\n", "") == compact:
                ent["text"] = rewritten
        return entries_in

    if mode == "heuristic" or LOCAL_INFERENCE_ONLY:
        return _format_srt_lines_heuristic(entries)

    # Map entries by index (1-based).
    by_index: dict[int, dict] = {}
    originals: dict[int, str] = {}
    pending: list[int] = []
    for i, ent in enumerate(entries):
        try:
            idx = int(ent.get("index", i + 1))  # type: ignore[arg-type]
        except Exception:
            idx = i + 1
        by_index[idx] = ent
        raw = str(ent.get("text", "") or "")
        compact = raw.replace("\r", "").replace("\n", "")
        originals[idx] = compact
        if not compact.strip():
            continue
        # Skip if already fits in one line (no wrap expected under this char budget).
        if len(compact) <= max_chars:
            continue
        # Skip if impossible to satisfy strict constraint without changing characters.
        if len(compact) > (max_chars * max_lines):
            continue
        pending.append(idx)

    if not pending:
        return entries

    def _is_valid_lines(lines: object, original: str) -> bool:
        if not isinstance(lines, list) or not lines:
            return False
        if len(lines) > max_lines:
            return False
        out_parts: list[str] = []
        for ln in lines:
            s = str(ln)
            if "\n" in s or "\r" in s:
                return False
            if len(s) > max_chars:
                return False
            out_parts.append(s)
        return "".join(out_parts) == original

    def _chunks(seq: list[int], size: int) -> list[list[int]]:
        return [seq[i : i + size] for i in range(0, len(seq), size)]

    # Retry loop: only for items that fail validation.
    for attempt in range(retry_limit + 1):
        if not pending:
            break
        attempt_note = ""
        if attempt > 0:
            attempt_note = (
                "\nNOTE: Your previous output violated constraints. Fix strictly:\n"
                "- Ensure lines.length <= max_lines\n"
                "- Ensure each line length <= max_chars_per_line\n"
                "- Ensure concatenation equals input text exactly (no missing/extra characters)\n"
            )

        next_pending: list[int] = []
        for chunk in _chunks(pending, bs):
            payload = {
                "max_lines": max_lines,
                "max_chars_per_line": max_chars,
                "items": [{"index": idx, "text": originals.get(idx, "")} for idx in chunk],
            }
            messages = [
                {"role": "system", "content": SRT_LINEBREAK_PROMPT + attempt_note},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
            try:
                call_with_raw = getattr(router, "call_with_raw", None)
                if callable(call_with_raw):
                    result = call_with_raw(
                        task="tts_text_prepare",
                        messages=messages,
                        response_format="json_object",
                        timeout=timeout,
                        max_tokens=1200,
                    )
                    content = result.get("content")
                    _log_llm_meta(
                        "tts_text_prepare",
                        {k: result.get(k) for k in ("request_id", "model", "provider", "latency_ms", "usage")},
                    )
                else:
                    content = router.call(
                        task="tts_text_prepare",
                        messages=messages,
                        response_format="json_object",
                        timeout=timeout,
                        max_tokens=1200,
                    )
            except Exception as e:
                print(f"[LLM_WARN] format_srt_lines call failed: {e}")
                return _format_srt_lines_heuristic(entries)

            obj = _parse_json_lenient(str(content or ""))
            items = obj.get("items") if isinstance(obj, dict) else None
            if not isinstance(items, list):
                # Treat as batch failure: keep originals for this chunk and try next attempt.
                next_pending.extend(chunk)
                continue

            returned: dict[int, list[str]] = {}
            for it in items:
                if not isinstance(it, dict):
                    continue
                try:
                    idx = int(it.get("index"))
                except Exception:
                    continue
                lines = it.get("lines")
                if isinstance(lines, list):
                    returned[idx] = [str(x) for x in lines]

            for idx in chunk:
                original = originals.get(idx, "")
                lines = returned.get(idx)
                if lines is None or not _is_valid_lines(lines, original):
                    next_pending.append(idx)
                    continue
                by_index[idx]["text"] = "\n".join(lines)

        pending = next_pending

    # Final fallback:
    # - Any remaining pending cues are left untouched (LLM could not satisfy constraints).
    # - Any too-long cues are rewrapped heuristically (best-effort, still no text change).
    return _format_srt_lines_heuristic(entries)


def _split_for_segmentation(a_text: str, limit: int = 1200) -> list[str]:
    import re
    text = a_text.strip()
    if len(text) <= limit:
        return [text] if text else []
    sentences = re.split(r"(?<=[。．.!！?？\n])", text)
    chunks: list[str] = []
    buf = ""
    for sent in sentences:
        s = sent.strip()
        if not s:
            continue
        if len(buf) + len(s) <= limit:
            buf += s
        else:
            if buf:
                chunks.append(buf)
            if len(s) <= limit:
                buf = s
            else:
                step = max(600, limit // 2)
                for i in range(0, len(s), step):
                    part = s[i : i + step].strip()
                    if part:
                        chunks.append(part)
                buf = ""
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c]


def segment_text_llm(a_text: str, max_len: int, model: str | None = None, api_key: str | None = None, timeout: int = 15) -> Dict[str, object]:
    if LOCAL_INFERENCE_ONLY:
        # (Local logic unchanged)
        import re
        parts = re.split(r"(?<=[。．.!！?？])\s+|\n+", a_text.strip())
        merged: list[str] = []
        buf = ""
        for p in parts:
            t = p.strip()
            if not t:
                continue
            if len(buf) + len(t) <= max_len:
                buf = (buf + " " + t).strip()
            else:
                if buf:
                    merged.append(buf)
                buf = t
        if buf:
            merged.append(buf)
        segments = [{"index": i, "text": s} for i, s in enumerate(merged)]
        return {"segments": segments}

    chunks = _split_for_segmentation(a_text)
    segments: list[dict] = []
    last_err: Exception | None = None

    for chunk in chunks:
        prompt = SRT_SEGMENT_PROMPT.format(max_len=max_len)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"text": chunk}, ensure_ascii=False)},
        ]
        
        try:
            call_with_raw = getattr(router, "call_with_raw", None)
            if callable(call_with_raw):
                result = call_with_raw(
                    task="tts_segment",
                    messages=messages,
                    response_format="json_object",
                    timeout=timeout,
                )
                content = result.get("content")
                _log_llm_meta("tts_segment", {k: result.get(k) for k in ("request_id", "model", "provider", "latency_ms", "usage")})
            else:
                content = router.call(
                    task="tts_segment",
                    messages=messages,
                    response_format="json_object",
                    timeout=timeout,
                )
            
            segs = None
            if isinstance(content, str):
                obj = _parse_json_lenient(content)
                if obj.get("segments"):
                    segs = obj["segments"]
            
            if not segs:
                raise ValueError("LLM segmentation returned empty list")
            
            for s in segs:
                txt = str(s.get("text", ""))
                if not txt.strip():
                    continue
                s["text"] = txt
                segments.append(s)
                
        except Exception as e:
            last_err = e
            # Fallback for chunk
            segments.append({"text": chunk})

    normalized = [{"index": i, "text": seg.get("text", "")} for i, seg in enumerate(segments)]
    return {"segments": normalized}


def generate_reading_script(a_text: str, model: str | None = None, api_key: str | None = None, timeout: int = 30) -> str:
    if LOCAL_INFERENCE_ONLY:
        return a_text

    chunks = _split_for_segmentation(a_text, limit=800)
    b_text_parts: list[str] = []

    for chunk in chunks:
        try:
            # 1st pass: segmentation
            seg_result = segment_text_llm(chunk, max_len=120, timeout=timeout)
            segs = seg_result.get("segments") or []
            if not segs:
                raise ValueError("LLM segmentation returned empty list")

            # 2nd pass: reading generation per segment via tts_reading (json)
            seg_texts = [str(s.get("text", "")) for s in segs]
            payload = {"segments": seg_texts}
            messages_read = [
                {"role": "system", "content": READING_GENERATION_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ]
            call_with_raw = getattr(router, "call_with_raw", None)
            if callable(call_with_raw):
                read_result = call_with_raw(
                    task="tts_reading",
                    messages=messages_read,
                    response_format="json_object",
                    timeout=timeout,
                )
                content_read = read_result.get("content")
                _log_llm_meta("tts_reading", {k: read_result.get(k) for k in ("request_id", "model", "provider", "latency_ms", "usage")})
            else:
                content_read = router.call(
                    task="tts_reading",
                    messages=messages_read,
                    response_format="json_object",
                    timeout=timeout,
                )
            readings = []
            if isinstance(content_read, str):
                obj = _parse_json_lenient(content_read)
                readings = obj.get("readings") or []
            if not readings or len(readings) != len(seg_texts):
                raise ValueError("reading generation mismatch")
            b_text_parts.extend(readings)
        except Exception as e:
            print(f"[LLM_WARN] generate_reading_script failed for chunk, using raw A-Text: {e}")
            b_text_parts.append(chunk)

    return "\n".join(b_text_parts)


def generate_reading_for_blocks(blocks: list[dict], model: str | None = None, api_key: str | None = None, timeout: int = 60) -> list[str]:
    if LOCAL_INFERENCE_ONLY:
        return [str(b.get("text", "")) for b in blocks]

    payload = {"segments": [str(b.get("text", "")) for b in blocks]}
    messages = [
        {"role": "system", "content": READING_GENERATION_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]

    try:
        call_with_raw = getattr(router, "call_with_raw", None)
        if callable(call_with_raw):
            result = call_with_raw(
                task="tts_reading",
                messages=messages,
                response_format="json_object",
                timeout=timeout,
            )
            content = result.get("content")
            _log_llm_meta("tts_reading", {k: result.get(k) for k in ("request_id", "model", "provider", "latency_ms", "usage")})
        else:
            content = router.call(
                task="tts_reading",
                messages=messages,
                response_format="json_object",
                timeout=timeout,
            )
        extracted = None
        if isinstance(content, str):
            obj = _parse_json_lenient(content)
            if obj.get("readings"):
                extracted = obj["readings"]
        if extracted and len(extracted) == len(blocks):
            return extracted
        else:
            print(f"[LLM_WARN] Reading count mismatch. In={len(blocks)}, Out={len(extracted) if extracted else 0}.")
    except Exception as e:
        print(f"[LLM_WARN] generate_reading_for_blocks failed: {e}")

    # Fallback: return raw texts
    return [str(b.get("text", "")) for b in blocks]


def _parse_json_strict(text: str) -> dict:
    return json.loads(text)


def _parse_json_lenient(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json", "", 1)
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = cleaned[start : end + 1]
            snippet = snippet.replace("，", ",").replace("：", ":")
            try:
                return json.loads(snippet)
            except Exception:
                pass
        return json.loads(text) # Retry raw


def _parse_json_salvage(text: str) -> dict:
    import re
    objs = []
    pattern = re.compile(r"\{{[^{}]*\"index\"\s*:\s*\d+[^{}]*\"llm_reading_kana\"[^{}]*\}}", re.MULTILINE)
    for m in pattern.finditer(text):
        try:
            obj = json.loads(m.group(0))
            objs.append(obj)
        except Exception:
            continue
    if not objs:
        raise ValueError("salvage failed")
    return {"token_annotations": objs}


def _enrich_annotations(obj: dict, token_map: dict[int, dict]) -> dict:
    anns = obj.get("token_annotations") or []
    out = []
    for a in anns:
        idx = int(a.get("index", len(out)))
        tok = token_map.get(idx, {})
        surface = a.get("surface") or tok.get("surface")
        reading_mecab = a.get("reading_mecab") or tok.get("reading_mecab")
        write_mode = a.get("write_mode") or "original"
        risk_level = int(a.get("risk_level", 1) or 1)
        llm_read = a.get("llm_reading_kana") or reading_mecab or surface or ""
        reason = a.get("reason", "")
        out.append(
            {
                "index": idx,
                "surface": surface,
                "llm_reading_kana": llm_read,
                "write_mode": write_mode,
                "risk_level": risk_level,
                "reason": reason,
                "reading_mecab": reading_mecab,
            }
        )
    return out
