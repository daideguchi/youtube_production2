"""
Minimal, side-effect free helpers to normalize LLM call parameters before hitting a provider adapter.

LLMRouter/LLMClient から呼ばれる前提で、副作用なし・安全側の正規化のみを担当する。
"""

from typing import Dict, Any


# Params that reasoningモデルでは送らない（400防止）
_REASONING_BLOCKLIST = {"temperature", "top_p", "frequency_penalty", "presence_penalty"}


def sanitize_params(model_conf: Dict[str, Any], options: Dict[str, Any]) -> Dict[str, Any]:
    """
    capabilities を見て安全側に揃えたパラメータ dict を返す。
    - reasoning==True の場合は温度系パラメータを削除
    - max_tokens を max_completion_tokens / max_tokens に振り分け
    - None/空文字の値は落とす
    """
    caps = (model_conf or {}).get("capabilities", {}) or {}
    is_reasoning = bool(caps.get("reasoning"))

    out: Dict[str, Any] = {}
    for k, v in options.items():
        if v in (None, ""):
            continue
        if is_reasoning and k in _REASONING_BLOCKLIST:
            continue
        out[k] = v

    # max_tokens のキー切替
    if "max_tokens" in out:
        val = out.pop("max_tokens")
        key = "max_completion_tokens" if caps.get("max_completion_tokens") else "max_tokens"
        out[key] = val

    return out
