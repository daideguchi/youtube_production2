from __future__ import annotations

import re

GLOBAL_IMAGE_GUARDRAILS = (
    "Keep characters consistent if present; do NOT add people unless needed. "
    "NO text in image (subtitles, captions, signage, UI, logos, watermarks, handwriting, letters, numbers)."
)


def build_prompt_from_template(template_text: str, prepend_summary: bool = False, **kwargs) -> str:
    # Ensure all known placeholders exist
    summary = kwargs.get("summary", "")
    placeholders = {
        "summary": summary,
        "visual_focus": kwargs.get("visual_focus", ""),
        "main_character": kwargs.get("main_character", ""),
        "style": kwargs.get("style", ""),
        "seed": kwargs.get("seed", ""),
        "size": kwargs.get("size", ""),
        "negative": kwargs.get("negative", ""),
    }
    
    # Basic formatting
    out = template_text
    for k, v in placeholders.items():
        out = out.replace("{" + k + "}", str(v))

    # Drop any unreplaced placeholders to avoid leaking "{...}" into prompts
    out = re.sub(r"\{[a-zA-Z0-9_]+\}", "", out)
    
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
