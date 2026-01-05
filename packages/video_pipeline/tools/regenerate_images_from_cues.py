#!/usr/bin/env python3
"""
Regenerate real images for an existing srt2images run_dir that already has `image_cues.json`.

Why this exists (CH02 issue):
- Some CH02 drafts were built in placeholder mode (noise PNGs) to avoid image API calls.
- The CapCut draft *does* have an image track, but the assets are noise placeholders, which looks like "no images".
- This tool fills `cue.prompt` using the channel preset template/style and regenerates PNGs via ImageClient (Gemini).

No text LLM calls are made here. It only uses the image generation API.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from factory_common.paths import video_pkg_root  # noqa: E402

PROJECT_ROOT = video_pkg_root()

from video_pipeline.src.config.channel_resolver import ChannelPresetResolver, infer_channel_id_from_path  # noqa: E402
from video_pipeline.src.srt2images.prompt_builder import build_prompt_from_template  # noqa: E402
from video_pipeline.src.srt2images.nanobanana_client import generate_image_batch  # noqa: E402


DEFAULT_CH02_NEGATIVE = (
    "people, human, face, portrait, body, hands, crowd, child, man, woman, elderly, old man, old woman, "
    "grandfather, grandmother, senior, wrinkles, japanese, asian, character design, mascot, "
    "text, letters, subtitle, caption, logo, watermark, UI, interface, signage, poster, typography"
)


def _is_ch02(channel: str) -> bool:
    return str(channel).upper() == "CH02"


def _write_persona_mode_off(run_dir: Path) -> None:
    # Ensure persona never leaks into CH02 prompts even if persona.txt exists.
    try:
        (run_dir / "persona_mode.txt").write_text("off\n", encoding="utf-8")
    except Exception:
        pass


_CH02_HUMAN_PATTERNS = re.compile(
    r"(老人|高齢|おじい|おばあ|老夫婦|年寄|爺|婆|老女|老婆|"
    r"\b(elderly|old man|old woman|grandfather|grandmother|senior|man|woman|boy|girl|person|people|portrait|face)\b"
    r")",
    re.IGNORECASE,
)


def _derive_ch02_visual_focus(cue: Dict[str, Any]) -> str:
    # SSOT: Do NOT pick `visual_focus` via keyword dictionaries / fixed motif pools.
    # Use the cue's planned `visual_focus` as-is; keep CH02 personless by default.
    vf = str(cue.get("visual_focus") or "").strip()
    if vf and not _CH02_HUMAN_PATTERNS.search(vf):
        return vf
    return "symbolic still life, negative space, soft gold light"


def _derive_ch02_main_character(_: Dict[str, Any]) -> str:
    return "None (personless scene; do not draw humans)"


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _infer_channel_from_run_dir(run_dir: Path) -> Optional[str]:
    m = re.search(r"(CH\\d{2})", run_dir.name.upper())
    if m:
        return m.group(1)
    # fallback: try path scan
    return infer_channel_id_from_path(str(run_dir))


def _truncate(text: str, limit: int = 120) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _sanitize_visual_focus_for_no_text(visual_focus: str) -> str:
    """
    Avoid accidentally prompting the model to render in-image text.

    This tool is LLM-free and may be used after a run has already been created, so we keep the
    sanitization small but practical (paper-like props often cause text hallucination).
    """
    s = str(visual_focus or "").strip()
    if not s:
        return ""
    lower = s.lower()

    # Anything that tends to make the model draw letters/numbers.
    risky_words = (
        "text",
        "subtitle",
        "caption",
        "sign",
        "signage",
        "logo",
        "watermark",
        "letter",
        "letters",
        "number",
        "numbers",
        "handwriting",
        "write",
        "writing",
        "calligraphy",
        # Paper-like props:
        "paper",
        "page",
        "notebook",
        "journal",
        "document",
        "newspaper",
        "brochure",
        "receipt",
        "bill",
        "form",
        "schedule",
        "calendar",
        "mail",
    )
    if any(w in lower for w in risky_words):
        # Keep the action but enforce "no readable text". Ensure idempotency.
        if "no readable text" in lower:
            # Collapse duplicates (some earlier runs may have appended the clause repeatedly).
            clause = "(NO readable text; blank/blurred pages; no letters/numbers)"
            parts = [p.strip() for p in s.split(clause) if p.strip()]
            return f"{parts[0]} {clause}" if parts else clause
        return f"{s} (NO readable text; blank/blurred pages; no letters/numbers)"
    return s


def _resolve_anchor_path(run_dir: Path, mode: str) -> Optional[str]:
    m = str(mode or "auto").strip().lower()
    guides_dir = run_dir / "guides"
    candidates: List[Path] = []

    # Prefer character-only anchor to avoid locking the background/composition.
    if m in {"auto", "characters"}:
        candidates.append(guides_dir / "style_anchor_characters.png")
    if m in {"auto", "scene"}:
        candidates.append(guides_dir / "style_anchor.png")

    for p in candidates:
        try:
            if p.exists() and p.is_file():
                return str(p)
        except Exception:
            continue
    return None


def _load_template_text(path: Optional[str]) -> str:
    if not path:
        return (
            "Scene: {summary}\\n"
            "Style: {style}\\n"
            "Composition: clear subject, cinematic, no text\\n"
            "Resolution: {size}\\n"
        )
    p = Path(path)
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    if not p.exists():
        return (
            "Scene: {summary}\\n"
            "Style: {style}\\n"
            "Composition: clear subject, cinematic, no text\\n"
            "Resolution: {size}\\n"
        )
    return p.read_text(encoding="utf-8")


def _build_prompt(
    cue: Dict[str, Any],
    *,
    template_text: str,
    style: str,
    negative: str,
    size_str: str,
    extra_suffix: str,
    include_script_excerpt: bool,
    visual_focus: str,
    main_character: str,
) -> str:
    # Mirror the pipeline prompt assembly, but keep it deterministic and LLM-free.
    parts: List[str] = []
    refined = (cue.get("refined_prompt") or "").strip()
    if refined:
        parts.append(refined)
    else:
        vf = _sanitize_visual_focus_for_no_text((cue.get("visual_focus") or "").strip())
        if vf:
            parts.append(f"Visual Focus: {vf}")
        summary = (cue.get("summary") or "").strip()
        if summary:
            parts.append(f"Scene: {summary}")
        tone = (cue.get("emotional_tone") or "").strip()
        if tone:
            parts.append(f"Tone: {tone}")
        role_tag = (cue.get("role_tag") or "").strip()
        if role_tag:
            parts.append(f"Role: {role_tag}")
        section_type = (cue.get("section_type") or "").strip()
        if section_type:
            parts.append(f"Section Type: {section_type}")
        if include_script_excerpt:
            txt = (cue.get("text") or "").strip()
            if txt:
                parts.append(f"Script excerpt: {_truncate(txt, 120)}")

    if extra_suffix:
        parts.append(extra_suffix)

    diversity = (cue.get("diversity_note") or "").strip()
    if diversity:
        parts.append(diversity)

    summary_for_prompt = " \\n".join([p for p in parts if p.strip()])
    return build_prompt_from_template(
        template_text,
        # Subject-first improves prompt adherence and reduces style overrides.
        prepend_summary=True,
        summary=summary_for_prompt,
        visual_focus=visual_focus,
        main_character=main_character,
        style=style or "",
        seed=0,
        size=size_str,
        negative=negative or "",
    )


def _delete_existing_pngs(images_dir: Path) -> int:
    count = 0
    for p in images_dir.glob("*.png"):
        try:
            p.unlink()
            count += 1
        except Exception:
            pass
    return count


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _backup_existing_pngs(images_dir: Path) -> Tuple[int, Optional[Path]]:
    pngs = sorted([p for p in images_dir.glob("*.png") if p.is_file()])
    if not pngs:
        return 0, None
    backup_dir = images_dir / f"_backup_{_utc_stamp()}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for p in pngs:
        try:
            p.rename(backup_dir / p.name)
            moved += 1
        except Exception:
            pass
    return moved, backup_dir if moved else None


def _verify_images(images_dir: Path, expected: int) -> Tuple[bool, List[int]]:
    missing: List[int] = []
    for i in range(1, expected + 1):
        p = images_dir / f"{i:04d}.png"
        if not p.exists():
            missing.append(i)
    return (len(missing) == 0), missing


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="srt2images output run dir (contains image_cues.json)")
    ap.add_argument("--channel", help="Override channel id (e.g., CH02). If omitted, inferred from run_dir name.")
    ap.add_argument("--force", action="store_true", help="Delete existing images/*.png before regeneration (destructive)")
    ap.add_argument(
        "--overwrite",
        action="store_true",
        help="Regenerate images even if images/*.png already exists (non-destructive; keeps existing files until overwritten).",
    )
    ap.add_argument("--max", type=int, default=0, help="Limit number of cues/images to generate (0 = all)")
    ap.add_argument(
        "--only-missing",
        action="store_true",
        help="Generate only missing images (resume-friendly; avoids rate-limit sleeps for existing files).",
    )
    ap.add_argument("--prompt-template", help="Override prompt template path")
    ap.add_argument("--style", help="Override style string")
    ap.add_argument("--negative", default="", help="Optional negative prompt string")
    ap.add_argument(
        "--include-script-excerpt",
        action="store_true",
        help="Include a short script excerpt in prompts (may cause in-image text).",
    )
    ap.add_argument(
        "--anchor",
        default="auto",
        choices=["auto", "characters", "scene", "none"],
        help="Attach a reference image from run_dir/guides (auto prefers characters-only anchor).",
    )
    ap.add_argument("--timeout-sec", type=int, default=300, help="Per-image timeout seconds")
    ap.add_argument("--retry-until-success", action="store_true", help="Do not write placeholders when generation fails")
    ap.add_argument("--max-retries", type=int, default=6, help="Max retries per image (used by generator)")
    args = ap.parse_args()

    run_dir = Path(args.run).resolve()
    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        raise SystemExit(f"Missing image_cues.json: {cues_path}")

    channel = (args.channel or _infer_channel_from_run_dir(run_dir) or "").upper()
    if not channel:
        raise SystemExit("Failed to infer --channel; pass --channel explicitly")

    preset = ChannelPresetResolver().resolve(channel)
    tpl_path = args.prompt_template
    if not tpl_path and preset and preset.prompt_template:
        tpl_path = preset.resolved_prompt_template()
    style = args.style or (preset.style if preset and preset.style else "")

    extra_suffix_parts: List[str] = []
    if preset and preset.prompt_suffix:
        extra_suffix_parts.append(str(preset.prompt_suffix))
    if preset and preset.character_note:
        extra_suffix_parts.append(str(preset.character_note))
    extra_suffix = "\n".join([x for x in extra_suffix_parts if x.strip()])

    payload = _read_json(cues_path)
    cues = payload.get("cues") or []
    if not isinstance(cues, list) or not cues:
        raise SystemExit(f"No cues in {cues_path}")

    size = payload.get("size") or {}
    width = int(size.get("width") or 1920)
    height = int(size.get("height") or 1080)
    size_str = f"{width}x{height}"
    template_text = _load_template_text(tpl_path)

    # In-image text tends to appear when raw script excerpts are included in prompts (esp. JP).
    # Default: DO NOT include script excerpt unless explicitly enabled.
    include_script_excerpt = bool(args.include_script_excerpt)
    if not include_script_excerpt:
        env = (os.getenv("SRT2IMAGES_INCLUDE_SCRIPT_EXCERPT") or "").strip().lower()
        include_script_excerpt = env in {"1", "true", "yes", "on"}

    anchor_mode = str(args.anchor or "auto").strip().lower()
    anchor_path = None if anchor_mode == "none" else _resolve_anchor_path(run_dir, anchor_mode)

    if _is_ch02(channel):
        _write_persona_mode_off(run_dir)
        if not (args.negative or "").strip():
            args.negative = DEFAULT_CH02_NEGATIVE

    total = len(cues)
    limit = int(args.max or 0)
    if limit > 0:
        total = min(total, limit)

    images_dir = run_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    if args.force:
        moved, backup_dir = _backup_existing_pngs(images_dir)
        if moved and backup_dir:
            print(f"[BACKUP] moved_pngs={moved} backup_dir={backup_dir}")
        else:
            deleted = _delete_existing_pngs(images_dir)
            print(f"[CLEAN] deleted_pngs={deleted} dir={images_dir}")
    force_generate = bool(args.force or args.overwrite)

    # Fill prompts + image_path for the subset we generate.
    gen_cues: List[Dict[str, Any]] = []
    for i in range(1, total + 1):
        cue = cues[i - 1]
        if not isinstance(cue, dict):
            continue

        # Channels that require strict visual continuity (characters/settings) across frames.
        if channel in {"CH01", "CH05", "CH22", "CH23"}:
            cue["use_persona"] = True

        visual_focus = (cue.get("visual_focus") or "").strip()
        main_character = ""
        if _is_ch02(channel):
            visual_focus = _derive_ch02_visual_focus(cue)
            main_character = _derive_ch02_main_character(cue)
            cue["use_persona"] = False
            cue["visual_focus"] = visual_focus
        elif visual_focus:
            cue["visual_focus"] = _sanitize_visual_focus_for_no_text(visual_focus)

        prompt = _build_prompt(
            cue,
            template_text=template_text,
            style=style,
            negative=args.negative,
            size_str=size_str,
            extra_suffix=extra_suffix,
            include_script_excerpt=include_script_excerpt,
            visual_focus=visual_focus,
            main_character=main_character,
        )
        cue["prompt"] = prompt
        cue["image_path"] = str(images_dir / f"{i:04d}.png")
        if anchor_path:
            cue["input_images"] = [anchor_path]
        gen_cues.append(cue)

    if args.only_missing:
        missing: List[Dict[str, Any]] = []
        for cue in gen_cues:
            try:
                out_path = Path(str(cue.get("image_path") or "")).resolve()
                if not out_path.exists() or out_path.stat().st_size <= 0:
                    missing.append(cue)
            except Exception:
                missing.append(cue)

        if not missing:
            print(f"[SKIP] only-missing: no missing images (total={len(gen_cues)}) dir={images_dir}")
            return

        # Bridge continuity: attach previous existing frame to the first missing cue.
        try:
            first_idx = int(missing[0].get("index") or 0)
        except Exception:
            first_idx = 0
        if first_idx > 1:
            prev_path = images_dir / f"{first_idx - 1:04d}.png"
            try:
                if prev_path.exists() and prev_path.is_file():
                    cur_inputs = missing[0].get("input_images")
                    if not isinstance(cur_inputs, list):
                        cur_inputs = []
                    merged: List[str] = []
                    for x in cur_inputs:
                        s = str(x).strip()
                        if s and s not in merged:
                            merged.append(s)
                    p = str(prev_path)
                    if p not in merged:
                        merged.append(p)
                    missing[0]["input_images"] = merged
            except Exception:
                pass

        print(
            f"[RESUME] only-missing: missing_images={len(missing)} total={len(gen_cues)} "
            f"first_missing_index={missing[0].get('index')}"
        )
        gen_cues = missing

    # Persist prompts back to image_cues.json (so future tools can reuse them).
    _write_json(cues_path, payload)
    print(f"[PROMPTS] updated={len(gen_cues)} cues_path={cues_path}")

    # Generate images (rate-limited inside nanobanana_client)
    generate_image_batch(
        gen_cues,
        mode="direct",
        concurrency=1,
        force=force_generate,
        width=width,
        height=height,
        timeout_sec=int(args.timeout_sec),
        config_path=None,
        retry_until_success=bool(args.retry_until_success),
        max_retries=int(args.max_retries),
        placeholder_text=None,
    )

    ok, missing = _verify_images(images_dir, expected=total)
    if not ok:
        raise SystemExit(f"Image generation incomplete: missing={missing[:10]} (total_missing={len(missing)})")
    print(f"[DONE] images={total} dir={images_dir}")


if __name__ == "__main__":
    main()
