from __future__ import annotations

from typing import Dict, List


def build_qa_payload(a_text: str, b_text: str, b_text_build_log: List[Dict[str, object]]) -> Dict[str, object]:
    return {"a_text": a_text, "b_text": b_text, "b_text_build_log": b_text_build_log}


def validate_qa_response(payload: Dict[str, object]) -> List[Dict[str, object]]:
    if not isinstance(payload, dict):
        raise ValueError("QA response must be object")
    issues = payload.get("issues") or payload.get("token_issues") or []
    if not isinstance(issues, list):
        raise ValueError("QA issues must be list")
    validated: List[Dict[str, object]] = []
    for item in issues:
        if not isinstance(item, dict):
            raise ValueError("issue must be object")
        if "index" not in item:
            raise ValueError("issue missing index")
        validated.append({"index": item["index"], "suggestion": item.get("suggestion") or item.get("fix") or "", "reason": item.get("reason", "")})
    return validated
