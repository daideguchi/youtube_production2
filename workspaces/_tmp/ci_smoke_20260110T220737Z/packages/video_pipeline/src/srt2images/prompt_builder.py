from __future__ import annotations

import re

GLOBAL_IMAGE_GUARDRAILS = (
    "Fidelity: the image must accurately depict THIS cue's content; avoid generic filler images. "
    "Do NOT substitute abstract concepts with generic cliché symbols; pick a concrete object/action/space grounded in THIS cue. "
    "Avoid repeating the same prop/symbol across many cues; vary subject, composition, and setting while staying faithful. "
    "Consistency: keep recurring characters identical across shots (face, hairstyle, age, body type, clothing). "
    "Keep recurring locations consistent (layout, key props, lighting) unless the scene explicitly changes. "
    "If a reference image is provided, treat it as the strict style/character anchor and change only what the prompt describes. "
    "Do NOT add people unless needed. "
    "NO text in image (subtitles, captions, signage, UI, logos, watermarks, handwriting, letters, numbers)."
)

# Fireworks FLUX schnell has a strict prompt length budget (see factory_common.image_client.FireworksImageAdapter).
# Keep guardrails short to avoid truncation collapsing many cues into near-identical prompts.
FIREWORKS_SCHNELL_GUARDRAILS = (
    "Rules: Depict THIS cue's subject clearly (no generic filler). "
    "No humans unless explicitly required. "
    "No text/UI/signage/logos/watermarks. "
    "Avoid repeating the same location/composition across cues; vary angle/distance/lighting."
)


def _looks_like_fireworks_flux_schnell(model_key: object) -> bool:
    mk = str(model_key or "").strip().lower()
    if not mk:
        return False
    return mk in {
        "f-1",
        "img-flux-schnell-1",
        "fireworks_flux_1_schnell_fp8",
        "fireworks_flux_1_dev_fp8",
    }


def _compact_summary_for_fireworks_schnell(summary: str, *, max_chars: int = 420) -> str:
    """
    Reduce summary noise that can cause Fireworks prompt truncation to destroy cue uniqueness.

    - Drops long guideline blocks / meta fields (role/section_type) that are not needed for image generation.
    - Normalizes literal "\\n" sequences into newlines for consistent processing.
    """
    raw = str(summary or "").strip()
    if not raw:
        return ""

    normalized = raw.replace("\\n", "\n")
    lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]

    dropped_prefixes = (
        "Role:",
        "Section Type:",
        "Role Guidance:",
        "Visual Guidelines:",
        "人物ポリシー",
        "人物ポリシー:",
    )

    ja_re = re.compile(r"[一-龠ぁ-んァ-ン]")
    kept: list[str] = []
    for ln in lines:
        if len(ln) > 240:
            continue
        # Drop JP-heavy scene prose for schnell (it tends to bias outputs toward JP storefront/signage).
        if ln.startswith("Scene:") and ja_re.search(ln):
            continue
        if ln.startswith("Tone:") and ja_re.search(ln):
            continue
        if any(ln.startswith(p) for p in dropped_prefixes):
            continue
        lower_ln = ln.lower()
        if (
            "no text" in lower_ln
            or "no subtitle" in lower_ln
            or "no caption" in lower_ln
            or "no signage" in lower_ln
            or "no logo" in lower_ln
            or "no watermark" in lower_ln
            or "人物ポリシー" in ln
            or "ルール" in ln
            or "禁止" in ln
        ):
            continue
        kept.append(ln)
        if len(kept) >= 6:
            break

    out = "\n".join(kept).strip()
    if len(out) > max_chars:
        out = out[:max_chars].rstrip()
    return out


def build_prompt_from_template(
    template_text: str,
    prepend_summary: bool = False,
    *,
    guardrails: str | None = GLOBAL_IMAGE_GUARDRAILS,
    **kwargs,
) -> str:
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
        if guardrails and guardrails not in out:
            out = f"{out}\n\n{guardrails}"
    else:
        out = guardrails or ""
    return out


def build_prompt_for_image_model(
    template_text: str,
    *,
    model_key: str | None,
    summary: str,
    visual_focus: str = "",
    main_character: str = "",
    style: str = "",
    seed: int | str | None = None,
    size: str = "",
    negative: str = "",
    prepend_summary: bool = False,
) -> str:
    """
    Build a prompt optimized for the selected image model.

    Fireworks FLUX schnell:
    - Strong prompt-length constraints; long templates + guardrails get truncated.
    - Use a compact summary and short guardrails to preserve cue uniqueness.
    """
    if _looks_like_fireworks_flux_schnell(model_key):
        compact = _compact_summary_for_fireworks_schnell(summary)
        # Minimal template: keep style/negative placeholders available, but avoid long text blocks.
        schnell_template = "Style: {style}\nNegative: {negative}".strip()
        return build_prompt_from_template(
            schnell_template,
            prepend_summary=True,
            summary=compact,
            visual_focus=visual_focus,
            main_character=main_character,
            style=style,
            seed=seed or "",
            size=size,
            # NOTE: Fireworks FLUX schnell does not support a structured negative_prompt parameter.
            # Putting long "negative lists" inside the prompt can *prime* the model (SSOT rule),
            # so we intentionally omit them here and rely on short guardrails instead.
            negative="",
            guardrails=FIREWORKS_SCHNELL_GUARDRAILS,
        )

    return build_prompt_from_template(
        template_text,
        prepend_summary=prepend_summary,
        summary=summary,
        visual_focus=visual_focus,
        main_character=main_character,
        style=style,
        seed=seed or "",
        size=size,
        negative=negative,
        guardrails=GLOBAL_IMAGE_GUARDRAILS,
    )
