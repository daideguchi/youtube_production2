from __future__ import annotations

import json
import logging
import re
from typing import List, Tuple

from fastapi import HTTPException

from backend.app.scripts_models import NaturalCommandAction

logger = logging.getLogger(__name__)


def _heuristic_natural_command(command: str, tts_content: str) -> Tuple[List[NaturalCommandAction], str]:
    command = command.strip()
    if not command:
        return [], "コマンドが空です。"

    pause_match = re.search(
        r"(?:\[(?P<tag>\d+(?:\.\d+)?)\s*(?:s|秒)?\])|(?P<num>\d+(?:\.\d+)?)\s*(?:秒|s|sec)\s*(?:待って|ポーズ|休止|間|入れて|追加)",
        command,
        re.IGNORECASE,
    )
    if pause_match:
        seconds_str = pause_match.group("tag") or pause_match.group("num")
        try:
            seconds_value = float(seconds_str)
        except (TypeError, ValueError):
            seconds_value = None
        if seconds_value and seconds_value > 0:
            return [
                NaturalCommandAction(
                    type="insert_pause",
                    pause_seconds=seconds_value,
                    pause_scope="line_end",
                )
            ], f"{seconds_value:.2f}秒のポーズタグを挿入します。"

    quote_patterns = [
        r"「([^」]+)」を「([^」]+)」に",
        r"『([^』]+)』を『([^』]+)』に",
        r'"([^"\\]+)"を"([^"\\]+)"に',
        r"“([^”]+)”を“([^”]+)”に",
    ]
    original = replacement = None
    for pattern in quote_patterns:
        match = re.search(pattern, command)
        if match:
            original, replacement = match.group(1), match.group(2)
            break

    if not original or not replacement:
        return [], "自動解釈できませんでした。"

    if original not in tts_content:
        return [], f"指定したテキスト「{original}」が音声用テキスト内に見つかりませんでした。"

    scope = "all" if re.search(r"全部|全て|すべて", command) else "first"
    target = "tts"
    if "字幕" in command:
        target = "srt"

    action = NaturalCommandAction(
        type="replace",
        target=target,
        original=original,
        replacement=replacement,
        scope=scope,
        update_assembled=target == "tts",
        regenerate_audio=True,
    )
    return [action], "テキストの置換を実行します。"


def _call_llm_for_command(command: str, tts_content: str) -> Tuple[List[NaturalCommandAction], str]:
    truncated_tts = tts_content[:4000]
    prompt = f"""
あなたは台本編集アシスタントです。以下の音声用テキストに対してユーザーの指示を構造化されたアクションとして返してください。

音声用テキスト(抜粋):\n```\n{truncated_tts}\n```\n
出力は以下のJSON形式のみで返してください。説明や日本語の文章は含めないでください。\n
{{
  "message": "ユーザーへの短い説明",
  "actions": [
    {{
      "type": "replace",
      "target": "tts" または "srt",
      "original": "変更前テキスト",
      "replacement": "変更後テキスト",
      "scope": "first" または "all",
      "update_assembled": true/false,
      "regenerate_audio": true/false
    }},
    {{
      "type": "insert_pause",
      "pause_seconds": 0.5,
      "pause_scope": "cursor" または "line_end" または "section_end"
    }}
  ]
}}

ルール:
1. replace アクションを返す場合、original に含める文字列は必ずテキスト内に存在するものにすること。
2. pause 指示がある場合は insert_pause アクションを使い、pause_seconds を秒単位の数値で設定すること。
3. scope は「全て」「全部」などがある場合のみ "all" とし、それ以外は "first"。
4. 「字幕」「SRT」などが含まれる場合は target="srt"、それ以外は target="tts"。
5. 生成するJSON以外の文字を出力しない。

ユーザーコマンド: {command}
"""
    try:
        from factory_common.llm_router import get_router
    except Exception as exc:  # pragma: no cover - optional dependency mismatch
        raise RuntimeError(f"LLMRouter is not available: {exc}") from exc

    router = get_router()
    result = router.call_with_raw(
        task="tts_natural_command",
        messages=[{"role": "user", "content": prompt}],
        response_format="json_object",
    )
    response_text = str(result.get("content") or "").strip()

    if response_text.startswith("```"):
        response_text = response_text.strip("`").strip()
    if response_text.lower().startswith("json"):
        response_text = response_text[4:].strip()
    for prefix in ("```json", "```JSON"):
        if response_text.startswith(prefix):
            response_text = response_text[len(prefix) :].strip()
    for marker in (
        "<|begin_of_text|>",
        "<|end_of_text|>",
        "<|start_header_id|assistant|end_header_id|>",
        "<|eot_id|>",
        "<｜begin▁of▁sentence｜>",
        "<｜end▁of▁sentence｜>",
    ):
        response_text = response_text.replace(marker, "")
    response_text = response_text.strip()

    payload = json.loads(response_text)
    if not isinstance(payload, dict):
        raise ValueError("LLM response is not a JSON object")

    actions_data = payload.get("actions", [])
    message = payload.get("message") or "LLMで指示を解釈しました。"
    actions: List[NaturalCommandAction] = []
    for action_data in actions_data:
        try:
            action = NaturalCommandAction(**action_data)
        except Exception as exc:  # pragma: no cover - validation fallback
            logger.warning("Invalid action from LLM: %s -- %s", action_data, exc)
            continue
        if action.original and action.original in tts_content:
            actions.append(action)
        else:
            logger.warning("LLM suggested original text not found: %s", action.original)
    return actions, message


def interpret_natural_command(command: str, tts_content: str) -> Tuple[List[NaturalCommandAction], str]:
    try:
        actions, message = _call_llm_for_command(command, tts_content)
        if actions:
            return actions, message
    except SystemExit as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Natural command LLM failed: %s", exc)

    # fallback to heuristic parser
    return _heuristic_natural_command(command, tts_content)

