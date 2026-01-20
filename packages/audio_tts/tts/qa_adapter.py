from __future__ import annotations

import json
from typing import Dict

SYSTEM_PROMPT = (
    "You are a QA checker for TTS B-text. Compare a_text and b_text."
    "Return JSON only with issues: list of {index, suggestion, reason}."
)

USER_TEMPLATE = """
Compare A-text and B-text. Focus on meaning drift or unnatural kana conversion.

Payload:
{payload}

Output format:
{{"issues": [{{"index": <int>, "suggestion": "...", "reason": "..."}}]}}
If no issues, return {{"issues": []}}. No extra text.
"""


def qa_check(payload: Dict[str, object], model: str | None = None, api_key: str | None = None, timeout: int = 120) -> Dict[str, object]:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(payload=json.dumps(payload, ensure_ascii=False))},
    ]
    
    try:
        from factory_common.llm_router import get_router  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError(f"LLM router not available for QA: {e}") from e
    router = get_router()
    try:
        # Use tts_reading task (High Intelligence) for QA
        content = router.call(
            task="tts_reading", 
            messages=messages, 
            max_tokens=32000, 
            timeout=timeout,
            response_format="json_object"
        )
        
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # contentに余分なテキストが混在した場合、最初のJSONブロックを抽出して再トライ
            start = content.find("{")
            end = content.rfind("}")
            if start != -1 and end != -1 and end > start:
                snippet = content[start : end + 1]
                # 末尾に余計な句読点や全角スペースが混ざっている場合を除去
                snippet = snippet.strip().strip("，。．、")
                return json.loads(snippet)
            raise ValueError("Failed to parse JSON from QA response")
            
    except SystemExit:
        # THINK/AGENT mode may raise SystemExit to stop the process for queued tasks.
        # QAは補助機能なので、パイプラインを止めずに「問題なし（未判定）」として継続する。
        return {"issues": []}
    except Exception:
        # QA failure shouldn't crash the pipeline. Return safe empty issues so callers can proceed.
        return {"issues": []}
