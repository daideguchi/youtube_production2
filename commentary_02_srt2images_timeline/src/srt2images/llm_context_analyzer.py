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

# Visual Bible Path (New)
VISUAL_BIBLE_PATH = Path(__file__).resolve().parents[3] / "commentary_02_srt2images_timeline" / "data" / "visual_bible.json"
LLM_LOG_PATH = Path(__file__).resolve().parents[3] / "logs" / "llm_context_analyzer.log"

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
    
    def __init__(self, api_key: str = None, model: str = None, channel_id: str | None = None):
        self.channel_id = channel_id
        # Note: api_key and model args are deprecated as LLMRouter handles configuration.
        # However, we keep them in signature for compatibility.

        # Check for strict mode environment variable
        strict_mode = os.getenv("LLM_CONTEXT_ANALYZER_STRICT", "false").lower() in ("true", "1", "yes", "on")
        self.strict_mode = strict_mode

        self.visual_bible = {}
        if VISUAL_BIBLE_PATH.exists():
            try:
                self.visual_bible = json.loads(VISUAL_BIBLE_PATH.read_text(encoding="utf-8"))
                logging.info(f"Loaded Visual Bible from {VISUAL_BIBLE_PATH}")
            except Exception as e:
                logging.warning(f"Failed to load Visual Bible: {e}")
        LLM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    def analyze_story_sections(self, segments: List[Dict], target_sections: int = 20) -> List[SectionBreak]:
        """
        ストーリー全体を分析して自然なセクション境界を決定
        """
        if not segments:
            return []

        total_duration = segments[-1]["end"] - segments[0]["start"]
        desired_avg = total_duration / max(1, target_sections)

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

        # Extract content between first { and last }, or first [ and last ]
        # This handles cases where there's extra text around the JSON
        bracket_start = content.find('{')
        bracket_end = content.rfind('}')
        array_start = content.find('[')
        array_end = content.rfind(']')

        # Determine which type of JSON to extract
        if bracket_start != -1 and bracket_end != -1 and (bracket_start < bracket_end):
            if array_start == -1 or array_start > bracket_start:  # Object takes priority if it's earlier
                json_str = content[bracket_start:bracket_end+1]
            else:
                # Check which comes first: object or array
                if bracket_start < array_start:
                    json_str = content[bracket_start:bracket_end+1]
                else:
                    json_str = content[array_start:array_end+1]
        elif array_start != -1 and array_end != -1 and (array_start < array_end):
            json_str = content[array_start:array_end+1]
        else:
            # If we can't find JSON brackets, try parsing the full content
            json_str = content.strip()

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

        try:
            content, meta = self._invoke_llm(
                router,
                task="visual_section_plan",
                messages=messages,
                temperature=0.3,
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
        # Increase target sections for CH01 to ensure rapid pacing
        if (self.channel_id or "").upper() == "CH01":
            min_sections = int(min_sections * 1.5)
            max_sections = int(max_sections * 1.5)
            extra_rapid = (
                "\n"
                "- **CRITICAL FOR CH01:** Maintain a rapid visual pace (max 12-15s per image).\n"
                "- **ABSOLUTELY FORBIDDEN:** Do not merge distinct actions or thoughts into one long static shot.\n"
                "- **VISUAL VARIETY:** Adjacent sections MUST have distinctly different `visual_focus`. Change angle, subject, distance, or lighting.\n"
                "- If a segment expresses desire, regret, or a list, split it into rapid-fire short cuts (3-5s).\n"
            )

        return f"""
You are preparing storyboards for a narrated YouTube video.
Input is a Japanese SRT segmented script. Produce between {min_sections} and {max_sections} visual sections.

Each section must:
  • Cover consecutive SRT segments (no overlap, no gaps).
  • Run roughly 10–15 seconds (never longer than 20 seconds).
  • Capture a single idea the viewer should picture (example, anecdote, list item, metaphor, scene change, or emotional beat).
  • Be easy to illustrate without text, describing concrete people, objects, settings whenever possible.
{extra_rapid}

Use the [index@timestamp] markers to reference SRT segments.

Return ONLY a JSON array with objects in the following schema:

[
  {{
    "section_index": 1,
    "start_segment": <int>,
    "end_segment": <int>,
    "summary": "<=50 characters",
    "visual_focus": "What should be shown visually. MUST be distinct from previous section.",
    "emotional_tone": "calm | anxious | hopeful | ...",
    "reason": "Why this boundary is chosen",
    "section_type": "story | dialogue | exposition | list | analysis | instruction | context | other",
    "role_tag": "explanation | story | dialogue | list_item | metaphor | quote | hook | cta | recap | transition | viewer_address",
    "persona_needed": true/false  // true only when recurring characters should stay consistent across frames (story/dialogue)
  }},
  ...
]

Rules:
- Use the original SRT indices shown in the text.
- **FORCE SPLIT:** If a section would exceed 20 seconds, YOU MUST SPLIT IT even if the topic continues. Change the visual angle or focus.
- If multiple list items or examples appear, prefer separate sections for each item.
- The JSON must contain at least {min_sections} entries and no more than {max_sections} entries.
- persona_needed should be true only when characters recur across multiple sections (narrative or dialogue). For abstract/list/instructional sections, set false.
- role_tag should be concise and consistent: choose from explanation, story, dialogue, list_item, metaphor, quote, hook, cta, recap, transition, viewer_address.
- Do not include any explanation outside the JSON array.

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
                if not isinstance(entry, dict):
                    logging.warning(f"Skipping non-dict entry: {entry}")
                    continue

                # Try to extract start_segment and end_segment with fallbacks
                raw_start = entry.get("start_segment")
                raw_end = entry.get("end_segment", raw_start)

                # Try alternative field names
                if raw_start is None:
                    raw_start = entry.get("start_index") or entry.get("segment_index")
                    raw_end = entry.get("end_index", raw_start)

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

                # Create SectionBreak with as much information as available
                section_breaks.append(SectionBreak(
                    start_segment=start_seg,
                    end_segment=end_seg,
                    reason=entry.get("reason", entry.get("rationale", "")).strip(),
                    emotional_tone=entry.get("emotional_tone", entry.get("tone", "")).strip(),
                    summary=entry.get("summary", entry.get("content", "")).strip(),
                    visual_focus=entry.get("visual_focus", entry.get("visual", entry.get("focus", ""))).strip(),
                    section_type=(entry.get("section_type") or entry.get("type") or "").strip() or None,
                    persona_needed=bool(entry.get("persona_needed", False)),
                    role_tag=(entry.get("role_tag") or entry.get("role") or entry.get("tag", "")).strip() or None,
                ))

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

        if start_offset <= idx < start_offset + segment_count:
            return idx

        # インデックスが相対値と推測される場合
        relative_idx = start_offset + idx
        if start_offset <= relative_idx < start_offset + segment_count:
            return relative_idx

        return None

    # ---- LLM呼び出しヘルパ ----
    def _invoke_llm(self, router, task: str, messages: List[Dict[str, str]], temperature: float):
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
        max_duration = max(40.0, desired_avg * 1.5)
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

    def _merge_short_sections(self, segments: List[Dict], sections: List[SectionBreak]) -> List[SectionBreak]:
        sections = self._merge_sections(sections)
        changed = True
        while changed:
            changed = False
            for idx, br in enumerate(list(sections)):
                duration = self._calculate_duration(segments, br.start_segment, br.end_segment)
                if duration >= self.MIN_SECTION_SECONDS or len(sections) <= 1:
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
