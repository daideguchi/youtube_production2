"""
Google Gemini & Azure OpenAI LLM文脈理解システム
SRTから自然なセクション分けを行う
"""
from __future__ import annotations
import os
import json
import logging
import re
from typing import List, Dict, Any
from dataclasses import dataclass
import math
from pathlib import Path

from factory_common.llm_router import get_router
from factory_common.paths import logs_root, repo_root

# Legacy global cache path (deprecated). Prefer passing visual_bible explicitly.
_LEGACY_VISUAL_BIBLE_PATH = repo_root() / "data" / "visual_bible.json"
LLM_LOG_PATH = logs_root() / "llm_context_analyzer.log"

@dataclass
class SectionBreak:
    """セクション境界情報"""
    start_segment: int
    end_segment: int
    reason: str
    emotional_tone: str
    summary: str
    visual_focus: str
    section_type: str | None = None
    persona_needed: bool = False
    role_tag: str | None = None


class LLMContextAnalyzer:
    """LLM文脈分析システム (Wrapper around LLMRouter)"""
    MAX_SEGMENTS_PER_CALL = 600
    OVERLAP_SEGMENTS = 3
    MIN_SECTION_SECONDS = 3.0
    MAX_SECTION_SECONDS = 40.0
    
    def __init__(
        self,
        api_key: str = None,
        model: str = None,
        channel_id: str | None = None,
        visual_bible: Dict[str, Any] | None = None,
    ):
        self.channel_id = channel_id
        # Note: api_key and model args are deprecated as LLMRouter handles configuration.
        # However, we keep them in signature for compatibility.

        # Check for strict mode environment variable
        strict_mode = os.getenv("LLM_CONTEXT_ANALYZER_STRICT", "false").lower() in ("true", "1", "yes", "on")
        self.strict_mode = strict_mode

        # Visual bible should be scoped per-run (passed from pipeline) to avoid cross-channel leakage.
        self.visual_bible: Dict[str, Any] = visual_bible if isinstance(visual_bible, dict) else {}
        if not self.visual_bible:
            # Optional opt-in via env var (absolute or repo-relative path).
            env_path = (os.getenv("SRT2IMAGES_VISUAL_BIBLE_PATH") or "").strip()
            if env_path:
                p = Path(env_path)
                if not p.is_absolute():
                    p = repo_root() / p
                if p.exists():
                    try:
                        self.visual_bible = json.loads(p.read_text(encoding="utf-8"))
                        logging.info("Loaded Visual Bible from %s", p)
                    except Exception as e:
                        logging.warning("Failed to load Visual Bible from %s: %s", p, e)
                else:
                    logging.warning("SRT2IMAGES_VISUAL_BIBLE_PATH set but file not found: %s", p)
        LLM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    def analyze_story_sections(self, segments: List[Dict], target_sections: int = 20) -> List[SectionBreak]:
        """
        ストーリー全体を分析して自然なセクション境界を決定
        """
        if not segments:
            return []

        total_duration = segments[-1]["end"] - segments[0]["start"]
        desired_avg = total_duration / max(1, target_sections)

        ch = (self.channel_id or "").upper()
        # CH22 requires strict 30–40s pacing per image. LLM boundary selection can drift and
        # trigger expensive multi-pass splitting. Prefer a deterministic, semantic partition
        # (punctuation/topic-shift/silence-gap based) and let per-cue prompt refinement handle
        # chunk-accurate visuals.
        if ch == "CH22":
            force_llm = (os.getenv("SRT2IMAGES_CH22_USE_LLM_SECTION_BOUNDARIES") or "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            if not force_llm:
                out = self._semantic_partition_ch22(
                    segments,
                    min_sec=30.0,
                    max_sec=40.0,
                    desired_avg=desired_avg,
                )
                logging.info("CH22 semantic sectioning complete: %d sections", len(out))
                return out

        initial_sections = self._generate_initial_sections(segments, target_sections)

        # IMPORTANT: Mechanical fallback segmentation is forbidden.
        if not initial_sections:
            raise RuntimeError(
                "🚨 LLM segmentation produced zero sections. "
                "Mechanical fallback is DISABLED; fix the LLM call/prompt/parsing and rerun."
            )

        refined = self._refine_overlong_sections(segments, initial_sections, target_sections, desired_avg)
        short_adjusted = self._merge_short_sections(segments, refined)
        filled_sections = self._ensure_min_sections(segments, short_adjusted, target_sections, desired_avg)
        final_sections = self._merge_short_sections(segments, filled_sections)
        if len(final_sections) < target_sections:
            final_sections = self._ensure_min_sections(segments, final_sections, target_sections, desired_avg)
            final_sections = self._merge_short_sections(segments, final_sections)
        final_sections = self._merge_sections(final_sections)
        final_sections = self._fill_gaps(final_sections, len(segments))

        # Post-gap-fill guardrail:
        # `_fill_gaps` may extend the tail section when the LLM misses the final indices.
        # That can create an overlong section that bypasses `_refine_overlong_sections`.
        max_duration = self._max_section_seconds(desired_avg)
        needs_refine = any(
            self._calculate_duration(segments, br.start_segment, br.end_segment) > max_duration
            for br in final_sections
        )
        if needs_refine:
            final_sections = self._refine_overlong_sections(segments, final_sections, target_sections, desired_avg)
            final_sections = self._merge_short_sections(segments, final_sections)
            final_sections = self._merge_sections(final_sections)
            final_sections = self._fill_gaps(final_sections, len(segments))

        final_sections = self._enforce_duration_bounds(segments, final_sections, target_sections, desired_avg)

        logging.info("LLM文脈分割完了: %d セクション生成", len(final_sections))
        return final_sections

    # ---- Persona extraction (script → character bible) ----

    def generate_persona(self, segments: List[Dict], max_chars: int = 1200) -> str:
        """
        台本から人物設定を抽出して簡潔なペルソナテキストを生成。
        返り値はプレーンテキスト（箇条書き可）。人物がいない場合は空文字を返す。
        """
        if not segments:
            return ""

        story = self._combine_segments(segments, start_offset=0)
        prompt = f"""
You are creating a concise character bible for illustration consistency.
Input is a Japanese SRT story with [index@timestamp] markers.

Identify recurring human characters actually present in the script (narrator, named/implicit roles).
For each character, include:
- label/name (or role if unnamed)
- role/function in story
- age range, gender, ethnicity (Japanese/Asian if implied), build/face traits
- attire/colors/key props
- mood/traits that stay consistent
- consistency rule: keep face/clothes/hair/props identical across all frames where this character appears

Rules:
- Do NOT invent extra characters beyond what the script implies.
- If no human characters are described, return: "No characters; avoid drawing people."
- Keep it under {max_chars} chars, plain text (no JSON).

Script excerpts:
{story}
"""
        
        router = get_router()
        try:
            content, meta = self._invoke_llm(
                router,
                task="visual_persona",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return self._postprocess_persona_text(content, max_chars=max_chars)
        except Exception as e:
            logging.warning(f"Persona generation failed: {e}")
            return ""
        
    def _combine_segments(self, segments: List[Dict], start_offset: int = 0) -> str:
        """セグメントを統合して読みやすいストーリーテキストにする"""
        story_parts = []
        for local_idx, seg in enumerate(segments):
            global_idx = start_offset + local_idx
            text = seg.get("text", "").strip()
            if text:
                # セグメント番号と時間を含めてLLMが境界を特定しやすくする
                timestamp = f"{seg['start']:.1f}s"
                story_parts.append(f"[{global_idx:03d}@{timestamp}] {text}")

        return "\n".join(story_parts)

    def _extract_json_content(self, content: str) -> str | None:
        """Extract JSON content from LLM response, handling various formats"""
        # First, try to find JSON between code fences
        fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
        if fence_match:
            content = fence_match.group(1).strip()

        # Extract the first balanced JSON object/array from the response.
        # NOTE: Some providers may truncate responses mid-JSON; do NOT slice with end=-1
        #       (that produces an empty string and hides the real error).
        start_match = re.search(r"[\[{]", content)
        if not start_match:
            json_str = content.strip()
        else:
            start = start_match.start()
            stack: list[str] = []
            in_str = False
            escape = False
            end_idx: int | None = None
            for i in range(start, len(content)):
                ch = content[i]
                if in_str:
                    if escape:
                        escape = False
                        continue
                    if ch == "\\":
                        escape = True
                        continue
                    if ch == '"':
                        in_str = False
                    continue

                if ch == '"':
                    in_str = True
                    continue

                if ch == "{":
                    stack.append("}")
                elif ch == "[":
                    stack.append("]")
                elif ch in ("}", "]"):
                    if stack and ch == stack[-1]:
                        stack.pop()
                        if not stack:
                            end_idx = i
                            break

            if end_idx is not None:
                json_str = content[start:end_idx + 1]
            else:
                # Truncated/unbalanced: try a minimal repair by appending missing closers.
                json_str = content[start:].strip()
                if stack:
                    json_str = json_str + "".join(reversed(stack))

        # Clean up any trailing commas or other invalid JSON issues
        json_str = re.sub(r',\s*}', '}', json_str)  # Remove trailing commas in objects
        json_str = re.sub(r',\s*]', ']', json_str)  # Remove trailing commas in arrays

        return json_str

    def _call_llm_for_analysis(
        self,
        segments: List[Dict],
        target_sections: int,
        min_sections: int,
        max_sections: int,
        start_offset: int
    ) -> List[SectionBreak]:
        """LLM分析を実行 (via Router)"""

        story = self._combine_segments(segments, start_offset=start_offset)
        prompt = self._create_analysis_prompt(
            story=story,
            min_sections=min_sections,
            max_sections=max_sections
        )

        # プロンプト長のデバッグ
        logging.info(f"DEBUG: Prompt length: {len(prompt)} chars")

        router = get_router()

        # Inject Visual Bible if available
        system_instruction = ""
        if self.visual_bible:
            # Format bible as system instruction context
            # Token/cost optimization: compact + stable key order so repeated calls share a common prefix.
            bible_text = json.dumps(self.visual_bible, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            system_instruction = f"Visual Bible (Consistency Rules):\n{bible_text}\n\nUse these character/setting definitions to ensure visual consistency."

        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        # Token/cost optimization:
        # Scale output cap with the requested number of sections to avoid truncation,
        # while keeping the default relatively low for short videos.
        per_section = int(os.getenv("SRT2IMAGES_SECTION_PLAN_TOKENS_PER_SECTION", "55"))
        base_cap = int(os.getenv("SRT2IMAGES_SECTION_PLAN_BASE_TOKENS", "1200"))
        hard_cap = int(os.getenv("SRT2IMAGES_SECTION_PLAN_MAX_TOKENS", "8000"))
        max_tokens = min(hard_cap, max(base_cap, per_section * max_sections))

        try:
            content, meta = self._invoke_llm(
                router,
                task="visual_section_plan",
                messages=messages,
                temperature=0.3,
                max_tokens=max_tokens,
                response_format="json_object",
            )
            section_breaks = self._parse_llm_response(
                content,
                start_offset=start_offset,
                segment_count=len(segments),
            )

            if not section_breaks:
                raise RuntimeError(
                    "🚨 LLM returned a response but zero sections were parsed. "
                    "Mechanical fallback is DISABLED. "
                    f"First 500 chars: {content[:500]!r}"
                )

            return section_breaks

        except Exception as e:
            raise RuntimeError(f"LLM analysis failed (no fallback): {e}") from e

    def _postprocess_persona_text(self, text: str, max_chars: int = 1200) -> str:
        """軽い後処理: フェンス除去・長さ制限など"""
        if not text:
            return ""
        cleaned = text.strip()
        # remove markdown fences if present
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`").strip()
        if len(cleaned) > max_chars:
            cleaned = cleaned[:max_chars].rsplit("\n", 1)[0].strip()
        return cleaned
    
    def _create_analysis_prompt(self, story: str, min_sections: int, max_sections: int) -> str:
        """LLM分析用のプロンプトを作成"""
        extra_rapid = ""
        extra_ch02 = ""
        extra_ch12 = ""
        extra_ch22_23 = ""
        section_seconds_hint = "10–15"
        force_split_seconds = 20
        # CH01: align pacing with channel preset base_period (SSOT).
        if (self.channel_id or "").upper() == "CH01":
            min_sections = int(min_sections * 1.5)
            max_sections = int(max_sections * 1.5)
            extra_rapid = (
                "\n"
                "- **CRITICAL FOR CH01:** Maintain a steady visual pace (aim ~15–25s per image around the target average).\n"
                "- **ABSOLUTELY FORBIDDEN:** Do not merge distinct actions or thoughts into one long static shot.\n"
                "- **VISUAL VARIETY:** Adjacent sections MUST have distinctly different `visual_focus`. Change angle, subject, distance, or lighting.\n"
                "- If a segment is a list or has sharp beat changes, split it into shorter cuts (8–15s), but avoid micro-cuts unless the script truly warrants it.\n"
            )
        elif (self.channel_id or "").upper() == "CH02":
            extra_ch02 = (
                "\n"
                "- **CRITICAL FOR CH02:** Default to personless symbolic imagery. Avoid depicting humans/faces/bodies unless the script explicitly requires it.\n"
                "- Prefer quiet metaphorical motifs (mask, mirror, empty chair, door, clock shadow, haze, light beam) over literal character scenes.\n"
                "- `persona_needed` should be false unless a recurring character is explicitly described in the script.\n"
                "- `visual_focus` should be object/space focused (not a person) and must differ from adjacent sections.\n"
            )
        elif (self.channel_id or "").upper() == "CH12":
            # CH12 only: user requirement is ~25s per image (slower pacing than other channels).
            section_seconds_hint = "20–30"
            force_split_seconds = 40
            extra_ch12 = (
                "\n"
                "- **CRITICAL FOR CH12:** Use a calmer visual pace (aim ~25s per image). Prefer fewer, longer shots that sustain mood.\n"
                "- Avoid rapid-fire micro cuts unless there is a clear scene change or a hard list.\n"
            )
        elif (self.channel_id or "").upper() == "CH22":
            # CH22: user requirement is 30–40s per image for CapCut pacing.
            section_seconds_hint = "30–40"
            force_split_seconds = 40
            extra_ch22_23 = (
                "\n"
                "- **CRITICAL FOR CH22:** Each section duration MUST be within ~30–40s per image.\n"
                "- Avoid micro-cuts. If a beat change would create a <30s section, fold it into an adjacent section by changing camera angle/focus.\n"
                "- Only create shorter sections when there is an unavoidable hard scene change.\n"
            )
        elif (self.channel_id or "").upper() == "CH23":
            # CH23: user requirement is 20–30s per image for CapCut pacing.
            section_seconds_hint = "20–30"
            force_split_seconds = 40
            extra_ch22_23 = (
                "\n"
                "- **CRITICAL FOR CH23:** Aim ~20–30s per image for CapCut draft pacing.\n"
                "- Avoid micro-cuts unless there is a clear scene change, a sharp beat change, or a list.\n"
            )

        return f"""
You are preparing storyboards for a narrated YouTube video.
Input is a Japanese SRT segmented script. Produce between {min_sections} and {max_sections} visual sections.

Each section must:
  • Cover consecutive SRT segments (no overlap, no gaps).
  • Run roughly {section_seconds_hint} seconds (never longer than {force_split_seconds} seconds).
  • Capture a single idea the viewer should picture (example, anecdote, list item, metaphor, scene change, or emotional beat).
  • Be easy to illustrate without text, describing concrete subjects, objects, settings whenever possible (people only when the script requires it).
{extra_rapid}{extra_ch02}{extra_ch12}{extra_ch22_23}

Use the [index@timestamp] markers to reference SRT segments.

Return ONLY a JSON object (no markdown, no code fences, no extra keys) with this schema:

{{"sections":[[start_segment,end_segment,summary,visual_focus,emotional_tone,persona_needed,role_tag,section_type],...]}}

Field rules:
- start_segment/end_segment: int indices from the script markers.
- summary: <= 30 Japanese characters (short scene label).
- visual_focus: <= 12 English words (what to show; must differ from adjacent sections).
- emotional_tone: <= 2 words (e.g., calm, anxious, hopeful).
- persona_needed: boolean; true ONLY if recurring characters must stay consistent.
- role_tag: one of explanation|story|dialogue|list_item|metaphor|quote|hook|cta|recap|transition|viewer_address
- section_type: one of story|dialogue|exposition|list|analysis|instruction|context|other

Rules:
- Use the original SRT indices shown in the text.
- **FORCE SPLIT:** If a section would exceed {force_split_seconds} seconds, YOU MUST SPLIT IT even if the topic continues. Change the visual angle or focus.
- The JSON must contain at least {min_sections} entries and no more than {max_sections} entries.
- Each section entry MUST be an array of exactly 8 elements as in the schema (do NOT add extra fields).
- Keep all strings single-line and extremely short (no paragraphs, no newlines).
- Do not include any explanation outside the JSON.

Script excerpts:
{story}
"""

    def _parse_llm_response(self, content: str, start_offset: int, segment_count: int) -> List[SectionBreak]:
        """LLMの回答をパースしてSectionBreakオブジェクトに変換"""
        section_breaks: List[SectionBreak] = []

        try:
            # Use the robust JSON extraction helper
            json_str = self._extract_json_content(content)
            if not json_str:
                logging.warning(
                    "⚠️  Could not extract JSON from LLM response.\n"
                    "Response content (first 500 chars):\n%s",
                    content[:500]
                )
                return []

            # Try to parse the JSON
            try:
                parsed_data = json.loads(json_str)
            except json.JSONDecodeError:
                # Sometimes the LLM returns a JSON array but with extra text
                # Let's try to extract the first properly formed JSON array/object
                logging.warning("⚠️  Initial JSON parsing failed, trying to extract structured data...")

                # Try to find the first valid JSON structure
                obj_match = re.search(r'\{(?:[^{}]|(?R))*\}', json_str)
                arr_match = re.search(r'\[(?:[^\[\]]|(?R))*\]', json_str)

                if arr_match:
                    # Found an array structure
                    json_str = arr_match.group()
                    parsed_data = json.loads(json_str)
                elif obj_match:
                    # Found an object structure
                    json_str = obj_match.group()
                    parsed_obj = json.loads(json_str)
                    # If it has a 'sections' property, use that
                    if isinstance(parsed_obj, dict) and 'sections' in parsed_obj:
                        parsed_data = parsed_obj['sections']
                    else:
                        # If it's a single section object, wrap it in an array
                        parsed_data = [parsed_obj]
                else:
                    logging.error(
                        "🚨 JSON DECODE ERROR: Could not parse any valid JSON from response.\n"
                        "JSON string (first 500 chars):\n%s",
                        json_str[:500]
                    )
                    return []

            # Handle both array and object formats
            if isinstance(parsed_data, dict):
                # If the response is an object with a 'sections' property, use that
                if 'sections' in parsed_data:
                    breaks_data = parsed_data['sections']
                else:
                    # If it's a single section object, wrap it in a list
                    breaks_data = [parsed_data]
            elif isinstance(parsed_data, list):
                # It's already a list of sections
                breaks_data = parsed_data
            else:
                logging.error(
                    "🚨 PARSE ERROR: Parsed data is neither a list nor an object with sections. Type: %s\n"
                    "JSON content: %s",
                    type(parsed_data).__name__, json_str[:300]
                )
                return []

            for entry in breaks_data:
                raw_start = None
                raw_end = None
                reason = ""
                emotional_tone = ""
                summary = ""
                visual_focus = ""
                section_type = None
                persona_needed = False
                role_tag = None

                # v2 compact format: [start, end, summary, visual_focus, tone, persona_needed, role_tag, section_type]
                if isinstance(entry, (list, tuple)):
                    raw_start = entry[0] if len(entry) >= 1 else None
                    raw_end = entry[1] if len(entry) >= 2 else raw_start
                    summary = str(entry[2] or "").strip() if len(entry) >= 3 else ""
                    visual_focus = str(entry[3] or "").strip() if len(entry) >= 4 else ""
                    emotional_tone = str(entry[4] or "").strip() if len(entry) >= 5 else ""
                    persona_needed = bool(entry[5]) if len(entry) >= 6 else False
                    role_tag = (str(entry[6] or "").strip() or None) if len(entry) >= 7 else None
                    section_type = (str(entry[7] or "").strip() or None) if len(entry) >= 8 else None
                    # Optional extra fields (kept for backward compatibility)
                    reason = str(entry[8] or "").strip() if len(entry) >= 9 else ""

                elif isinstance(entry, dict):
                    raw_start = entry.get("start_segment")
                    raw_end = entry.get("end_segment", raw_start)
                    if raw_start is None:
                        raw_start = entry.get("start_index") or entry.get("segment_index")
                        raw_end = entry.get("end_index", raw_start)
                    reason = (entry.get("reason", entry.get("rationale", "")) or "").strip()
                    emotional_tone = (entry.get("emotional_tone", entry.get("tone", "")) or "").strip()
                    summary = (entry.get("summary", entry.get("content", "")) or "").strip()
                    visual_focus = (entry.get("visual_focus", entry.get("visual", entry.get("focus", ""))) or "").strip()
                    section_type = (entry.get("section_type") or entry.get("type") or "").strip() or None
                    persona_needed = bool(entry.get("persona_needed", False))
                    role_tag = (entry.get("role_tag") or entry.get("role") or entry.get("tag", "")).strip() or None

                else:
                    logging.warning(f"Skipping unsupported entry: {entry}")
                    continue

                if raw_start is None:
                    logging.warning(f"Skipping entry with no start information: {entry}")
                    continue

                start_seg = self._normalize_index(raw_start, start_offset, segment_count)
                end_seg = self._normalize_index(raw_end, start_offset, segment_count)

                if start_seg is None or end_seg is None:
                    logging.warning(
                        f"Index normalization failed: raw_start={raw_start}, raw_end={raw_end}, "
                        f"start_offset={start_offset}, segment_count={segment_count}, "
                        f"normalized: start_seg={start_seg}, end_seg={end_seg}"
                    )
                    continue

                if end_seg < start_seg:
                    start_seg, end_seg = end_seg, start_seg

                section_breaks.append(
                    SectionBreak(
                        start_segment=start_seg,
                        end_segment=end_seg,
                        reason=reason,
                        emotional_tone=emotional_tone,
                        summary=summary,
                        visual_focus=visual_focus,
                        section_type=section_type,
                        persona_needed=persona_needed,
                        role_tag=role_tag,
                    )
                )

        except json.JSONDecodeError as exc:
            logging.error(
                "🚨 JSON DECODE ERROR: %s\n"
                "JSON string (first 500 chars):\n%s",
                exc, json_str[:500] if 'json_str' in locals() else content[:500]
            )
        except Exception as e:
            logging.error(
                f"🚨 UNEXPECTED ERROR in _parse_llm_response: {e}\n"
                f"Content (first 500 chars):\n{content[:500]}"
            )

        return section_breaks

    def _normalize_index(self, value: Any, start_offset: int, segment_count: int) -> int | None:
        try:
            idx = int(value)
        except (ValueError, TypeError):
            return None

        # Accept inclusive end index (e.g., 1-based SRT style) when it equals the exclusive upper bound.
        # Example: segment_count=295, valid last index=294, but model may return 295.
        upper_exclusive = start_offset + segment_count
        if idx == upper_exclusive:
            return upper_exclusive - 1

        if start_offset <= idx < start_offset + segment_count:
            return idx

        # インデックスが相対値と推測される場合
        relative_idx = start_offset + idx
        if start_offset <= relative_idx < start_offset + segment_count:
            return relative_idx

        return None

    # ---- LLM呼び出しヘルパ ----
    def _invoke_llm(self, router, task: str, messages: List[Dict[str, str]], temperature: float, **options: Any):
        """
        call_with_raw があればメタ情報ごと取得しログに残す。無ければ従来 call() を使用。
        Returns (content, meta_dict).
        """
        meta: Dict[str, Any] = {}
        try:
            call_with_raw = getattr(router, "call_with_raw", None)
            if callable(call_with_raw):
                resp = call_with_raw(
                    task=task,
                    messages=messages,
                    temperature=temperature,
                    **options,
                )
                meta = {
                    "request_id": resp.get("request_id"),
                    "chain": resp.get("chain"),
                    "model": resp.get("model"),
                    "provider": resp.get("provider"),
                    "latency_ms": resp.get("latency_ms"),
                    "usage": resp.get("usage"),
                }
                self._log_llm_call(task, meta)
                return resp.get("content"), meta
            # fallback
            content = router.call(
                task=task,
                messages=messages,
                temperature=temperature,
                **options,
            )
            self._log_llm_call(task, {"provider": "legacy_router"})
            return content, meta
        except Exception:
            self._log_llm_call(task, {"provider": "legacy_router", "error": "invoke_failed"})
            raise

    def _log_llm_call(self, task: str, payload: Dict[str, Any]):
        try:
            record = {"task": task, **payload}
            with LLM_LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # ---- 解析フロー補助メソッド ----

    def _generate_initial_sections(self, segments: List[Dict], target_sections: int) -> List[SectionBreak]:
        total_duration = segments[-1]["end"] - segments[0]["start"]

        if len(segments) <= self.MAX_SEGMENTS_PER_CALL:
            min_sections = max(1, target_sections - 1)
            max_sections = target_sections + 1
            return self._call_llm_for_analysis(
                segments=segments,
                target_sections=target_sections,
                min_sections=min_sections,
                max_sections=max_sections,
                start_offset=0
            )

        logging.info(
            "LLM分析チャンク処理: segments=%d target=%d chunk=%d overlap=%d",
            len(segments), target_sections, self.MAX_SEGMENTS_PER_CALL, self.OVERLAP_SEGMENTS
        )

        chunk_ranges = []
        start = 0
        while start < len(segments):
            end = min(len(segments), start + self.MAX_SEGMENTS_PER_CALL)
            chunk_ranges.append((start, end))
            if end == len(segments):
                break
            start = max(0, end - self.OVERLAP_SEGMENTS)

        accumulated: list[SectionBreak] = []
        assigned_sections = 0

        for idx, (start_idx, end_idx) in enumerate(chunk_ranges):
            chunk_segments = segments[start_idx:end_idx]
            chunk_duration = chunk_segments[-1]["end"] - chunk_segments[0]["start"]

            remaining_sections = target_sections - assigned_sections
            remaining_chunks = len(chunk_ranges) - idx
            chunk_target = max(1, round(target_sections * (chunk_duration / total_duration)))
            max_allowed = remaining_sections - (remaining_chunks - 1)
            chunk_target = max(1, min(chunk_target, max_allowed))

            min_sections = max(1, chunk_target - 1)
            max_sections = chunk_target + 1

            logging.info(
                "チャンク %d/%d: segments=%d (global %d-%d) target=%d",
                idx + 1, len(chunk_ranges), len(chunk_segments), start_idx, end_idx - 1, chunk_target
            )

            chunk_breaks = self._call_llm_for_analysis(
                segments=chunk_segments,
                target_sections=chunk_target,
                min_sections=min_sections,
                max_sections=max_sections,
                start_offset=start_idx
            )

            for br in chunk_breaks:
                if br.end_segment < start_idx or br.start_segment >= end_idx:
                    continue

                if accumulated and br.start_segment <= accumulated[-1].end_segment:
                    adjusted_start = accumulated[-1].end_segment + 1
                    if adjusted_start > br.end_segment:
                        continue
                    br = SectionBreak(
                        start_segment=adjusted_start,
                        end_segment=br.end_segment,
                        reason=br.reason,
                        emotional_tone=br.emotional_tone,
                        summary=br.summary,
                        visual_focus=br.visual_focus,
                        section_type=br.section_type,
                        persona_needed=br.persona_needed,
                        role_tag=br.role_tag,
                    )

                accumulated.append(br)

            assigned_sections += chunk_target

        accumulated.sort(key=lambda b: b.start_segment)
        return accumulated

    def _refine_overlong_sections(
        self,
        segments: List[Dict],
        sections: List[SectionBreak],
        target_sections: int,
        desired_avg: float
    ) -> List[SectionBreak]:
        max_duration = self._max_section_seconds(desired_avg)
        refined: List[SectionBreak] = []

        for br in sections:
            duration = self._calculate_duration(segments, br.start_segment, br.end_segment)
            if duration <= max_duration:
                refined.append(br)
                continue

            desired_split = max(2, math.ceil(duration / desired_avg))
            chunk_segments = segments[br.start_segment: br.end_segment + 1]

            new_breaks = self._call_llm_for_analysis(
                segments=chunk_segments,
                target_sections=desired_split,
                min_sections=max(2, desired_split - 1),
                max_sections=desired_split + 1,
                start_offset=br.start_segment
            )

            if not new_breaks or len(new_breaks) <= 1:
                raise RuntimeError(
                    f"🚨 LLM failed to split overlong section (duration={duration:.1f}s). "
                    "Mechanical fallback is DISABLED. Fix the LLM call/prompt/parsing and rerun."
                )

            refined.extend(new_breaks)

        return self._merge_sections(refined)

    def _ensure_min_sections(
        self,
        segments: List[Dict],
        sections: List[SectionBreak],
        target_sections: int,
        desired_avg: float
    ) -> List[SectionBreak]:
        if len(sections) >= target_sections:
            return sections

        sections = self._merge_sections(sections)

        if len(sections) < target_sections:
            logging.warning(
                "🚨 LLM generated only %d sections (target: %d). "
                "Mechanical padding is DISABLED. Proceeding with LLM-generated sections only.",
                len(sections), target_sections
            )

        return sections

    # ---- ヘルパー ----

    def _max_section_seconds(self, desired_avg: float) -> float:
        ch = (self.channel_id or "").upper()
        if ch == "CH22":
            return 40.0
        return max(40.0, float(desired_avg) * 1.5)

    def _min_section_seconds_for_merge(self) -> float:
        ch = (self.channel_id or "").upper()
        if ch == "CH22":
            return 30.0
        return float(self.MIN_SECTION_SECONDS)

    def _enforce_duration_bounds(
        self,
        segments: List[Dict],
        sections: List[SectionBreak],
        target_sections: int,
        desired_avg: float,
    ) -> List[SectionBreak]:
        """
        Enforce per-channel section duration bounds without any mechanical equal-interval fallback.
        For CH22: each section must be 30–40s.
        """
        ch = (self.channel_id or "").upper()
        if ch != "CH22":
            return sections

        min_sec = 30.0
        max_sec = 40.0

        out = self._merge_sections(sections)
        out = self._fill_gaps(out, len(segments))

        for _ in range(4):
            out = self._refine_overlong_sections(segments, out, target_sections, desired_avg)
            out = self._merge_short_sections(segments, out)
            out = self._merge_sections(out)
            out = self._fill_gaps(out, len(segments))

            bad: list[tuple[int, int, float]] = []
            for br in out:
                d = self._calculate_duration(segments, br.start_segment, br.end_segment)
                if d < min_sec or d > max_sec:
                    bad.append((br.start_segment, br.end_segment, d))
            if not bad:
                return out

        preview = ", ".join([f"[{a}-{b}] {d:.1f}s" for a, b, d in bad[:10]])
        logging.warning(
            "CH22 section duration out of 30–40s bounds after LLM refinement (%s). "
            "Falling back to semantic boundary scoring partition (non-mechanical).",
            preview,
        )

        fallback = self._semantic_partition_ch22(segments, min_sec=min_sec, max_sec=max_sec, desired_avg=desired_avg)
        bad_fb: list[tuple[int, int, float]] = []
        for br in fallback:
            d = self._calculate_duration(segments, br.start_segment, br.end_segment)
            if d < min_sec or d > max_sec:
                bad_fb.append((br.start_segment, br.end_segment, d))
        if bad_fb:
            fb_preview = ", ".join([f"[{a}-{b}] {d:.1f}s" for a, b, d in bad_fb[:10]])
            raise RuntimeError(
                "CH22 semantic partition failed to satisfy 30–40s bounds (this likely means the SRT has an "
                f"unavoidable long span). Offenders: {fb_preview}"
            )
        return fallback

    def _semantic_partition_ch22(
        self,
        segments: List[Dict],
        *,
        min_sec: float,
        max_sec: float,
        desired_avg: float,
    ) -> List[SectionBreak]:
        """
        CH22 fallback: build a strict 30–40s partition using only semantic cues (punctuation, topic shifts, silence gaps).
        This is NOT mechanical equal-interval splitting.
        """
        n = len(segments)
        if n == 0:
            return []

        # Boundary score for cutting AFTER segment j.
        boundary_scores: list[float] = [0.0] * n
        for j in range(n - 1):
            prev_text = (segments[j].get("text") or "").strip()
            next_text = (segments[j + 1].get("text") or "").strip()
            gap = float(segments[j + 1]["start"]) - float(segments[j]["end"])
            boundary_scores[j] = self._semantic_boundary_score(prev_text, next_text, gap)
        boundary_scores[n - 1] = 0.0

        def _len_bonus(duration: float) -> float:
            if desired_avg <= 0:
                return 0.0
            # Light preference toward the configured average (e.g. 35s) without enforcing uniformity.
            return -abs(float(duration) - float(desired_avg)) * 0.04

        neg_inf = float("-inf")
        dp: list[float] = [neg_inf] * (n + 1)  # dp[pos] = best score up to pos (pos is next start index)
        prev_pos: list[int] = [-1] * (n + 1)
        prev_end: list[int] = [-1] * (n + 1)
        dp[0] = 0.0

        # Pre-cast times for speed/robustness.
        starts = [float(s["start"]) for s in segments]
        ends = [float(s["end"]) for s in segments]

        for i in range(n):
            if dp[i] == neg_inf:
                continue
            start_t = starts[i]
            for j in range(i, n):
                dur = ends[j] - start_t
                if dur > max_sec:
                    break
                if dur < min_sec:
                    continue
                next_pos = j + 1
                score = dp[i] + boundary_scores[j] + _len_bonus(dur)
                if score > dp[next_pos]:
                    dp[next_pos] = score
                    prev_pos[next_pos] = i
                    prev_end[next_pos] = j

        if dp[n] == neg_inf:
            raise RuntimeError(
                "CH22 semantic partition found no valid 30–40s segmentation. "
                "Try increasing SRT granularity (more subtitle splits) or review unusually long gaps."
            )

        pairs: list[tuple[int, int]] = []
        pos = n
        while pos > 0:
            i = prev_pos[pos]
            j = prev_end[pos]
            if i < 0 or j < 0:
                raise RuntimeError("CH22 semantic partition reconstruction failed (internal DP state).")
            pairs.append((i, j))
            pos = i
        pairs.reverse()

        out: list[SectionBreak] = []
        for a, b in pairs:
            summary = self._truncate_summary_from_segments(segments, a, b, limit=160)
            out.append(
                SectionBreak(
                    start_segment=a,
                    end_segment=b,
                    reason="semantic_partition",
                    emotional_tone="",
                    summary=summary,
                    visual_focus="",
                    # CH22 requires character consistency; treat as story unless explicitly overridden upstream.
                    section_type="story",
                    persona_needed=True,
                    role_tag="story",
                )
            )

        out = self._merge_sections(out)
        out = self._fill_gaps(out, len(segments))
        return out

    def _semantic_boundary_score(self, prev_text: str, next_text: str, gap_sec: float) -> float:
        score = 0.0

        # 1) Silence gap is a strong natural boundary.
        if gap_sec > 0:
            score += min(float(gap_sec), 2.0) * 0.8

        # 2) Sentence-ending punctuation.
        t = (prev_text or "").strip()
        if t.endswith(("。", "！", "？", "!", "?")):
            score += 1.4
        elif t.endswith(("…", "‥", "」", "』", "）", ")")):
            score += 0.9
        elif t.endswith(("、", ",")):
            score -= 0.4

        # 3) Topic shift openers (bonus) vs conjunction continuations (small penalty).
        nt = (next_text or "").strip()
        topic_shift = ("一方", "その頃", "さて", "ここで", "ところが", "ちなみに")
        continuation = ("そして", "でも", "だから", "それで", "しかし", "けれど", "ただ")
        if any(nt.startswith(x) for x in topic_shift):
            score += 0.6
        if any(nt.startswith(x) for x in continuation):
            score -= 0.2

        return score

    def _truncate_summary_from_segments(self, segments: List[Dict], start_idx: int, end_idx: int, limit: int = 160) -> str:
        parts: list[str] = []
        for i in range(start_idx, end_idx + 1):
            t = (segments[i].get("text") or "").strip()
            if t:
                parts.append(t)
        combined = " ".join(parts)
        combined = " ".join(combined.split())
        if not combined:
            return ""
        if len(combined) <= limit:
            return combined
        return combined[: limit - 1].rstrip() + "…"

    def _merge_short_sections(self, segments: List[Dict], sections: List[SectionBreak]) -> List[SectionBreak]:
        sections = self._merge_sections(sections)
        changed = True
        min_sec = self._min_section_seconds_for_merge()
        while changed:
            changed = False
            for idx, br in enumerate(list(sections)):
                duration = self._calculate_duration(segments, br.start_segment, br.end_segment)
                if duration >= min_sec or len(sections) <= 1:
                    continue

                if idx == 0:
                    merge_idx = 1
                elif idx == len(sections) - 1:
                    merge_idx = idx - 1
                else:
                    left_duration = self._calculate_duration(segments, sections[idx - 1].start_segment, sections[idx - 1].end_segment)
                    right_duration = self._calculate_duration(segments, sections[idx + 1].start_segment, sections[idx + 1].end_segment)
                    merge_idx = idx - 1 if left_duration <= right_duration else idx + 1

                partner = sections[merge_idx]
                # When merging, try to preserve important fields from the first section
                # as it's likely to have more appropriate content for the merged range
                new_section = SectionBreak(
                    start_segment=min(br.start_segment, partner.start_segment),
                    end_segment=max(br.end_segment, partner.end_segment),
                    reason="short merge",
                    emotional_tone=br.emotional_tone or partner.emotional_tone,
                    summary=br.summary or partner.summary,
                    visual_focus=br.visual_focus or partner.visual_focus,
                    section_type=br.section_type or partner.section_type,
                    persona_needed=br.persona_needed or partner.persona_needed,
                    role_tag=br.role_tag or partner.role_tag,
                )

                sections.pop(max(idx, merge_idx))
                sections.pop(min(idx, merge_idx))
                sections.append(new_section)
                sections = self._merge_sections(sections)
                changed = True
                break

        return sections

    def _calculate_duration(self, segments: List[Dict], start_idx: int, end_idx: int) -> float:
        start_sec = segments[start_idx]["start"]
        end_sec = segments[end_idx]["end"]
        return max(0.0, end_sec - start_sec)


    def _merge_sections(self, sections: List[SectionBreak]) -> List[SectionBreak]:
        if not sections:
            return []

        sections = sorted(sections, key=lambda br: (br.start_segment, br.end_segment))
        merged: List[SectionBreak] = []

        for br in sections:
            if not merged:
                merged.append(br)
                continue

            prev = merged[-1]
            if br.start_segment <= prev.end_segment:
                # オーバーラップ分を切り落とす
                adjusted_start = prev.end_segment + 1
                if adjusted_start > br.end_segment:
                    continue
                br = SectionBreak(
                    start_segment=adjusted_start,
                    end_segment=br.end_segment,
                    reason=br.reason,
                    emotional_tone=br.emotional_tone,
                    summary=br.summary,
                    visual_focus=br.visual_focus,
                    section_type=br.section_type,
                    persona_needed=br.persona_needed,
                    role_tag=br.role_tag,
                )

            merged.append(br)

        return merged

    def _fill_gaps(self, sections: List[SectionBreak], total_segments: int) -> List[SectionBreak]:
        if not sections:
            raise RuntimeError(
                "🚨 CRITICAL ERROR: No LLM-generated sections available. "
                "Cannot proceed without contextual understanding. "
                "Mechanical gap filling is DISABLED."
            )

        sections = sorted(sections, key=lambda b: b.start_segment)
        filled: List[SectionBreak] = []
        expected_start = 0

        for br in sections:
            start = br.start_segment
            end = br.end_segment

            if not filled and start > 0:
                start = 0

            if filled and start > expected_start:
                prev = filled[-1]
                filled[-1] = SectionBreak(
                    start_segment=prev.start_segment,
                    end_segment=start - 1,
                    reason=prev.reason,
                    emotional_tone=prev.emotional_tone,
                    summary=prev.summary,
                    visual_focus=prev.visual_focus,
                    section_type=prev.section_type,
                    persona_needed=prev.persona_needed,
                    role_tag=prev.role_tag,
                )

            if start < expected_start:
                start = expected_start
            if start > end:
                continue

            filled.append(SectionBreak(
                start_segment=start,
                end_segment=end,
                reason=br.reason,
                emotional_tone=br.emotional_tone,
                summary=br.summary,
                visual_focus=br.visual_focus,
                section_type=br.section_type,
                persona_needed=br.persona_needed,
                role_tag=br.role_tag,
            ))

            expected_start = end + 1

        if filled and expected_start < total_segments:
            prev = filled[-1]
            filled[-1] = SectionBreak(
                start_segment=prev.start_segment,
                end_segment=total_segments - 1,
                reason=prev.reason,
                emotional_tone=prev.emotional_tone,
                summary=prev.summary,
                visual_focus=prev.visual_focus,
                section_type=prev.section_type,
                persona_needed=prev.persona_needed,
                role_tag=prev.role_tag,
            )

        return filled
