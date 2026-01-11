"""
Belt generation system using LLMRouter instead of direct Google GenAI calls.

This module provides functionality to generate dynamic Japanese belt text
from SRT content for CapCut draft generation, using the configured LLMRouter.
"""
from __future__ import annotations
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, TypedDict, Any
import sys

from factory_common.llm_router import get_router
from factory_common.paths import video_pkg_root

logger = logging.getLogger(__name__)

class BeltConfig(TypedDict):
    """Structure for belt configuration."""
    episode: str
    total_duration: float
    belts: List[Dict[str, Any]]  # List of belt items with text, start, end
    opening_offset: float
    main_title: Optional[str]  # Optional main title


def generate_belt_from_script(
    cues_path: Path,
    opening_offset: float = 0.0,
    sections: int = 4,
    channel_id: str = None
) -> Optional[BeltConfig]:
    """
    Generate belt configuration from image_cues.json using LLMRouter.

    Args:
        cues_path: Path to image_cues.json file
        opening_offset: Time offset for belt display
        sections: Target number of belt sections (4-6 recommended)
        channel_id: Channel ID to customize generation (required)

    Returns:
        BeltConfig dict or None if generation fails
    """
    if channel_id is None:
        raise ValueError("channel_id must be provided for belt generation")
    video_id = cues_path.parent.name  # Extract video ID from parent directory
    total_duration = 0.0  # Initialize to avoid UnboundLocalError

    try:
        if not cues_path.exists():
            raise FileNotFoundError(f"image_cues.json not found in {cues_path}")

        cues_data = json.loads(cues_path.read_text(encoding="utf-8"))
        cues = cues_data.get("cues", [])

        if not cues:
            raise ValueError("No cues found in image_cues.json")

        total_duration = max((c.get("end_sec", 0) for c in cues), default=0.0)

        if total_duration <= 0:
            logger.warning(f"[{video_id}][belt_generation][FALLBACK] reason='total_duration invalid' total_duration={total_duration}")
            empty_config = {
                "episode": "",
                "total_duration": 0.0,
                "belts": [],
                "opening_offset": opening_offset,
                "main_title": None
            }
            return empty_config

        # Load channel preset to determine belt generation parameters
        channel_preset = _load_channel_preset(channel_id)

        # Determine number of sections based on channel preset
        original_sections = sections  # Store original value for logging
        if channel_preset and "belt" in channel_preset:
            belt_config = channel_preset["belt"]
            if "max_sections" in belt_config:
                sections = belt_config["max_sections"]
                logger.info(f"[{video_id}][belt_generation][INFO] max_sections from preset={sections} (was {original_sections})")
            elif "mode" in belt_config and belt_config["mode"] == "main_only":
                sections = 1
                logger.info(f"[{video_id}][belt_generation][INFO] mode='main_only' setting sections=1 (was {original_sections})")

        logger.info(f"[{video_id}][belt_generation][INFO] total_duration={total_duration:.3f}s channel={channel_id} target_sections={sections}")

        # Prepare summaries for LLM
        summaries = []
        for c in cues:
            summaries.append({
                "start": round(float(c.get("start_sec", 0.0)), 3),
                "end": round(float(c.get("end_sec", 0.0)), 3),
                "summary": c.get("summary") or _truncate_summary(c.get("text", ""), 60),
                "visual_focus": c.get("visual_focus", "")
            })

        # Create a prompt for the LLM to generate belts
        prompt = _create_belt_generation_prompt(summaries, total_duration, sections, channel_id)

        # Use LLMRouter to call the configured model
        router = get_router()
        responses = router.call(
            task="belt_generation",  # This task should be defined in the config
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            response_format="json_object"  # Request JSON response - this may not work with all providers
        )

        # Parse the LLM response
        result = _parse_belt_response(responses, total_duration, opening_offset)

        if result is None:
            logger.warning(f"[{video_id}][belt_generation][FALLBACK] reason='could not parse LLM response' total_duration={total_duration:.3f}s")
            empty_config = _create_empty_belt_config(opening_offset, total_duration)
            logger.info(f"[{video_id}][belt_generation][RESULT] status=empty_fallback belts=0")
            return empty_config

        belts_count = len(result.get('belts', []))
        if belts_count > 0:
            sample_text = result['belts'][0]['text'][:50] if result['belts'] else ''
            logger.info(f"[{video_id}][belt_generation][OK] sections={belts_count} sample_title='{sample_text}'")
        else:
            logger.warning(f"[{video_id}][belt_generation][FALLBACK] reason='LLM returned no belts' total_duration={total_duration:.3f}s")

        return result

    except Exception as e:
        logger.warning(f"[{video_id}][belt_generation][FALLBACK] reason='exception' error='{str(e)}' total_duration={total_duration:.3f}s")
        # Don't raise exception - just return empty config to allow pipeline to continue
        empty_config = _create_empty_belt_config(opening_offset, total_duration)
        logger.info(f"[{video_id}][belt_generation][RESULT] status=empty_fallback_exception belts=0")
        return empty_config


def _create_belt_generation_prompt(summaries: List[Dict], total_duration: float, target_sections: int, channel_id: str) -> str:
    """
    Create a prompt for LLM to generate Japanese belt text from SRT content.

    Args:
        summaries: List of cue summaries with start/end times
        total_duration: Total video duration
        target_sections: Target number of belt sections
        channel_id: Channel ID to customize generation

    Returns:
        Formatted prompt string
    """
    # Limit summaries to prevent exceeding token limits
    limited_summaries = summaries[:50]  # Limit to first 50 summaries

    content_lines = [f"{s['start']:.3f}-{s['end']:.3f}: {s['summary']}" for s in limited_summaries]
    content_snippet = "\n".join(content_lines)

    # 動的に例を生成
    example_start_times = [0.0]
    for i in range(1, target_sections):
        example_start_times.append(round(total_duration * i / target_sections, 1))
    example_end_times = [round(total_duration * (i + 1) / target_sections, 1) for i in range(target_sections)]
    example_end_times[-1] = round(total_duration, 3)  # 最後の終了時間を正確に設定

    example_belts = []
    for i in range(target_sections):
        example_belts.append(f'{{"text": "{i+1}. タイトル例", "start": {example_start_times[i]}, "end": {example_end_times[i]}}}')

    example_json = ",\n    ".join(example_belts)

    # チャンネル固有の指示を設定ファイルから取得
    channel_preset = _load_channel_preset(channel_id)
    belt_mode = None
    if channel_preset and "belt" in channel_preset:
        belt_config = channel_preset["belt"]
        belt_mode = belt_config.get("mode")

    # 帯生成モードに基づいた特別指示
    special_instructions = ""
    if belt_mode == "main_only":
        special_instructions = "\nサブ帯は不要で、メインタイトルのみを生成してください。"

    prompt = f"""以下はJSON ONLYのフォーマットで返してください。説明文や余計なテキストは一切不要です。
```json
{{
  "belts": [
    {",\n    ".join(example_belts)}
  ]
}}
```

台本内容から、時間軸({total_duration:.3f}s)全体をカバーする{target_sections}個の帯タイトルをJSON形式で生成してください。
- 各セクションは時間的に連続（隙間・重複なし）
- 日本語の短いタイトル（12文字以内）、視聴者を惹きつけるキャッチーな文言
- 必ず「text」「start」「end」キーを含めること

{special_instructions}

重要: 実際の値は台本内容に基づいて生成すること。上記の例は JSON 形式の例示に過ぎず、内容は台本から抽出した情報に置き換えること。

台本内容:
{content_snippet}
"""
    return prompt


def _parse_belt_response(response: str, total_duration: float, opening_offset: float) -> Optional[BeltConfig]:
    """
    Parse the LLM response into belt configuration.

    Args:
        response: Raw response from LLM
        total_duration: Total video duration
        opening_offset: Opening offset for belts

    Returns:
        BeltConfig dict or None if parsing fails
    """
    logger.debug(f"Raw LLM response: {response[:500]}...")  # Log the full response for debugging

    try:
        # First, try to find JSON between ```json ``` or just ``` ```
        code_block_match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\n?\s*```', response, re.DOTALL | re.IGNORECASE)
        if code_block_match:
            json_str = code_block_match.group(1)
            logger.debug(f"Found JSON in code block: {json_str}")
        else:
            # Try to find JSON in ```json ``` format with more flexible pattern
            code_block_match2 = re.search(r'```\s*(?:json)?\s*\n?({.*?})\s*\n?```', response, re.DOTALL | re.IGNORECASE)
            if code_block_match2:
                json_str = code_block_match2.group(1)
                logger.debug(f"Found JSON in code block (alt): {json_str}")
            else:
                # Try to find a complete JSON object in the response
                # Use a more flexible pattern that handles various JSON formats
                # Find content between first { and last }
                start_idx = response.find('{')
                end_idx = response.rfind('}')
                if start_idx != -1 and end_idx != -1 and start_idx < end_idx:
                    json_str = response[start_idx:end_idx+1]
                    logger.debug(f"Extracted JSON from braces: {json_str}")
                else:
                    # Try to find a JSON array pattern as fallback
                    array_start = response.find('[')
                    array_end = response.rfind(']')
                    if array_start != -1 and array_end != -1 and array_start < array_end:
                        # If it's just an array without "belts" wrapper, wrap it
                        array_str = response[array_start:array_end+1]
                        json_str = f'{{"belts": {array_str}}}'
                        logger.debug(f"Wrapped array in JSON object: {json_str}")
                    else:
                        # If no JSON structure found, return None
                        logger.warning(f"Could not find JSON structure in LLM response: {response[:200]}...")
                        return None

        # Clean up common formatting issues
        json_str = json_str.strip()
        # Remove trailing commas, etc.
        json_str = re.sub(r',\s*}', '}', json_str)
        json_str = re.sub(r',\s*]', ']', json_str)

        logger.debug(f"Cleaned JSON string: {json_str}")

        belt_data = json.loads(json_str)

        # Handle both formats: { "belts": [...] } or just an array
        if "belts" in belt_data:
            belts = belt_data["belts"]
        else:
            # If the root is the belts array itself
            belts = belt_data if isinstance(belt_data, list) else []

        if not belts:
            logger.warning("No belts found in LLM response")
            return None

        # Process and validate belts
        processed_belts = []
        last_end = 0.0

        for i, belt in enumerate(belts):
            if not isinstance(belt, dict):
                logger.warning(f"Belt item {i} is not a dict: {belt}")
                continue

            start = float(belt.get("start", last_end))
            end = float(belt.get("end", start))
            text = belt.get("text", f"セクション{i+1}")

            # Ensure time contiguity
            if i == 0 and start > 0:
                start = 0.0
            if end <= start:
                # Set a reasonable default if end <= start
                end = start + max(1.0, total_duration / len(belts))

            # Ensure text starts with number if it doesn't already
            if not re.match(r'^\d+\.?\s*', text):
                text = f"{i+1}. {text}"

            processed_belt = {
                "text": text.strip(),
                "start": round(start, 3),
                "end": round(end, 3)
            }
            processed_belts.append(processed_belt)
            last_end = end

        # Ensure the final belt ends at total_duration
        if processed_belts:
            processed_belts[-1]["end"] = round(total_duration, 3)

        return {
            "episode": "",
            "total_duration": round(total_duration, 3),
            "belts": processed_belts,
            "opening_offset": opening_offset,
            "main_title": None
        }

    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error in LLM response: {e}")
        logger.debug(f"Response content: {response[:500]}...")
        return None
    except Exception as e:
        logger.warning(f"Error processing LLM response: {e}")
        logger.debug(f"Response content: {response[:500]}...")
        return None


def _truncate_summary(text: str, limit: int = 60) -> str:
    """Truncate text for LLM prompts; keep it safe for JSON."""
    if not text:
        return ""
    sanitized = " ".join(str(text).split())
    if len(sanitized) <= limit:
        return sanitized
    return sanitized[: max(0, limit - 1)] + "…"


def _create_empty_belt_config(opening_offset: float, total_duration: float = 0.0) -> BeltConfig:
    """
    Create an empty belt configuration as fallback.
    
    Args:
        opening_offset: Opening offset for belts
        total_duration: Total duration for the video
    
    Returns:
        Empty belt configuration
    """
    return {
        "episode": "",
        "total_duration": total_duration,
        "belts": [],
        "opening_offset": opening_offset,
        "main_title": None
    }


def _load_channel_preset(channel_id: str) -> Optional[Dict[str, Any]]:
    """
    Load channel preset configuration from channel_presets.json

    Args:
        channel_id: Channel identifier (e.g., "CH02")

    Returns:
        Channel preset configuration or None if file not found
    """
    try:
        config_path = video_pkg_root() / "config" / "channel_presets.json"

        if not config_path.exists():
            logger.warning(f"Channel preset file not found: {config_path}")
            return None

        with open(config_path, 'r', encoding='utf-8') as f:
            presets_data = json.load(f)

        channel_presets = presets_data.get("channels", {})
        return channel_presets.get(channel_id)

    except Exception as e:
        logger.warning(f"Failed to load channel preset for {channel_id}: {e}")
        return None
