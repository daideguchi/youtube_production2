from __future__ import annotations

GLOBAL_IMAGE_GUARDRAILS = (
    "人物描写は動画全体で一貫させる。登場人物の人種・性別・年齢層・髪型・服装・体型・雰囲気を毎枚そろえる。"
    " 画像の中にテキスト・字幕・サイン・看板・UI・ロゴ・スタンプなどの文字要素を絶対に描かない。"
)


def build_prompt_from_template(template_text: str, prepend_summary: bool = False, **kwargs) -> str:
    # Ensure all known placeholders exist
    summary = kwargs.get("summary", "")
    placeholders = {
        "summary": summary,
        "style": kwargs.get("style", ""),
        "seed": kwargs.get("seed", ""),
        "size": kwargs.get("size", ""),
        "negative": kwargs.get("negative", ""),
    }
    
    # Basic formatting
    out = template_text
    for k, v in placeholders.items():
        out = out.replace("{" + k + "}", str(v))
    
    # Subject-First reordering (Critical for preventing style override)
    if prepend_summary and summary:
        # Prepend the core action/subject to the top of the prompt
        # We use a clear marker so the model knows this is the primary instruction
        out = f"★VISUAL SUBJECT (PRIORITY 1): {summary}\n\n[Style & Mood Specifications]\n{out}"

    out = out.strip()
    if out:
        if GLOBAL_IMAGE_GUARDRAILS not in out:
            out = f"{out}\n\n{GLOBAL_IMAGE_GUARDRAILS}"
    else:
        out = GLOBAL_IMAGE_GUARDRAILS
    return out
