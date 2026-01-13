#!/usr/bin/env python3
"""
Regenerate real images for an existing srt2images run_dir that already has `image_cues.json`.

Why this exists:
- Some drafts are built in placeholder mode (noise PNGs) to avoid image API calls during planning.
- The CapCut draft can have an image track, but assets are placeholders, which looks like "no images".
- This tool fills `cue.prompt` using the channel preset template/style and regenerates PNGs via ImageClient
  (model selection is controlled by routing; call-time overrides are guarded under lockdown).

No text LLM calls are made here. It only uses the image generation API.
"""

from __future__ import annotations

import argparse
import hashlib
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
from factory_common.routing_lockdown import lockdown_active  # noqa: E402

PROJECT_ROOT = video_pkg_root()

from video_pipeline.src.config.channel_resolver import ChannelPresetResolver, infer_channel_id_from_path  # noqa: E402
from video_pipeline.src.srt2images.prompt_builder import build_prompt_for_image_model  # noqa: E402
from video_pipeline.src.srt2images.nanobanana_client import generate_image_batch  # noqa: E402


DEFAULT_PERSONLESS_VISUAL_FOCUS = "symbolic still life, negative space, soft gold light"

DEFAULT_NEGATIVE_PERSONLESS = (
    "people, person, human, face, portrait, body, hands, fingers, crowd, child, man, woman"
)
DEFAULT_NEGATIVE_NO_TEXT = (
    "text, letters, numbers, subtitle, caption, logo, watermark, UI, interface, signage, poster, typography, handwriting"
)

_HUMAN_PATTERNS = re.compile(
    r"(人物|人間|顔|手|指|子供|子ども|男|女|老人|高齢|おじい|おばあ|老夫婦|年寄|爺|婆|"
    r"身体|肉体|裸体|裸|肌|"
    r"疲れた体|体調|体の状態|"
    r"\b(elderly|old man|old woman|grandfather|grandmother|senior|man|woman|boy|girl|person|people|portrait|face|hand|hands)\b"
    r"|\b(body|nude|naked|skin|torso|legs|arms|breast)\b"
    r")",
    re.IGNORECASE,
)


def _derive_personless_visual_focus(cue: Dict[str, Any]) -> str:
    vf = str(cue.get("visual_focus") or "").strip()
    summary = str(cue.get("summary") or "").strip()
    text = f"{vf} {summary}".strip()

    # Common body-state cues: keep the key prop but avoid depicting a person.
    if ("体温計" in text) or ("thermometer" in text.lower()):
        return "体温計が置かれた、しわのあるベッドシーツ／無人の部屋／柔らかな朝の光"
    if ("呼吸" in text) or ("breath" in text.lower()):
        return "冷たいガラスにうっすら残る曇りと空気の流れ／無人の静かな室内／淡い光"

    if vf and not _HUMAN_PATTERNS.search(text):
        return vf
    return DEFAULT_PERSONLESS_VISUAL_FOCUS


def _join_negative(*parts: str) -> str:
    items: list[str] = []
    for raw in parts:
        for tok in str(raw or "").split(","):
            s = tok.strip()
            if s:
                items.append(s)
    # stable unique
    out: list[str] = []
    seen: set[str] = set()
    for s in items:
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return ", ".join(out)


def _stable_cue_seed(run_dir: Path, idx: int, *, base_seed: int = 0) -> int:
    # Keep aligned with orchestration/pipeline.py (31-bit positive seed).
    label = f"{run_dir.name}:{idx}"
    h = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
    v = (int(h, 16) + int(base_seed or 0)) % 2147483647
    return v or 1


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


def _sanitize_visual_focus_for_no_text(
    visual_focus: str, *, enabled: bool, avoid_props: str = ""
) -> str:
    """
    Avoid accidentally prompting the model to render in-image text.

    This tool is LLM-free and may be used after a run has already been created, so we keep the
    sanitization small but practical (paper-like props often cause text hallucination).
    """
    s = str(visual_focus or "").strip()
    if not enabled:
        return s
    if not s:
        return ""
    lower = s.lower()

    # Targeted rewrites for high-risk props that often cause the model to invent letters/symbols.
    # Keep meaning but force "blank/unmarked" variants.
    if "チェックリスト" in s or "チェックボックス" in s:
        s = s.replace("チェックリスト", "石板に刻まれた空の正方形の枠（すべて空、印なし、文字なし）")
        s = s.replace("チェックボックス", "空の正方形の枠（印なし、文字なし）")
        lower = s.lower()
    if "checklist" in lower or "checkbox" in lower:
        s = re.sub(
            r"(?i)checklist|checkbox",
            "grid of empty squares (all blank, no marks, no symbols, no writing)",
            s,
        )
        lower = s.lower()
    if "カレンダー" in s:
        s = s.replace("カレンダー", "無地のカードが並んだ壁（文字なし・数字なし・印なし）")
        lower = s.lower()
    if "calendar" in lower:
        s = re.sub(r"(?i)calendar", "blank card grid on a wall (no writing, no numbers)", s)
        lower = s.lower()
    if "メモ" in s or "付箋" in s:
        s = s.replace("メモ", "無地のメモ用紙（印なし・文字なし）")
        s = s.replace("付箋", "無地のメモ用紙（印なし・文字なし）")
        lower = s.lower()
    if "memo" in lower or "sticky note" in lower:
        s = re.sub(r"(?i)sticky\\s*note|memo", "blank note paper (unmarked)", s)
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
        "memo",
        "sticky note",
        # Japanese (text/signage)
        "文字",
        "看板",
        "掲示板",
        "標識",
        "ロゴ",
        "字幕",
        # Number-prone props
        "clock",
        "watch",
        "timer",
        "stopwatch",
        "thermometer",
        "gauge",
        "meter",
        "時計",
        "懐中時計",
        "タイマー",
        "ストップウォッチ",
        "体温計",
        "温度計",
        "メーター",
        "ゲージ",
        "計器",
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
        # Japanese (paper-like)
        "紙",
        "本",
        "ページ",
        "ノート",
        "手帳",
        "日記",
        "書類",
        "書面",
        "メモ",
        "付箋",
        "レシート",
        "フォーム",
        "申請書",
        "予定表",
        "カレンダー",
        "日付",
    )

    avoid = [p.strip().lower() for p in str(avoid_props or "").split(",") if p.strip()]
    risky_hit = any(w in lower for w in risky_words) or any(tok in lower for tok in avoid)

    if risky_hit:
        # Keep the action but enforce "no readable text or numerals". Ensure idempotency.
        clause = "(NO readable text; blank/unmarked/unlabeled; no letters/numbers)"
        if clause.lower() in lower:
            # Collapse duplicates (some earlier runs may have appended the clause repeatedly).
            parts = [p.strip() for p in s.split(clause) if p.strip()]
            return f"{parts[0]} {clause}" if parts else clause
        return f"{s} {clause}"
    return s


def _sanitize_summary_for_no_text(summary: str, *, enabled: bool) -> str:
    s = str(summary or "").strip()
    if not enabled or not s:
        return s
    # Remove short quoted labels/titles that tend to be rendered as literal on-image text.
    s = re.sub(r"[「『\"][^」』\"]{1,60}[」』\"]", "", s)
    s = re.sub(r"(?i)\\b(question|title|caption|subtitle|label)\\b", "", s)
    return " ".join(s.split()).strip()


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
    model_key: str | None,
    style: str,
    negative: str,
    size_str: str,
    extra_suffix: str,
    include_script_excerpt: bool,
    forbid_text: bool,
    avoid_props: str,
    visual_focus: str,
    main_character: str,
) -> str:
    # Mirror the pipeline prompt assembly, but keep it deterministic and LLM-free.
    parts: List[str] = []
    refined = (cue.get("refined_prompt") or "").strip()
    if refined:
        parts.append(refined)
    else:
        vf = _sanitize_visual_focus_for_no_text(
            (cue.get("visual_focus") or "").strip(),
            enabled=bool(forbid_text),
            avoid_props=str(avoid_props or ""),
        )
        if vf:
            parts.append(f"Visual Focus: {vf}")
        summary = _sanitize_summary_for_no_text((cue.get("summary") or "").strip(), enabled=bool(forbid_text))
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
    return build_prompt_for_image_model(
        template_text,
        model_key=model_key,
        # Subject-first improves prompt adherence and reduces style overrides.
        prepend_summary=True,
        summary=summary_for_prompt,
        visual_focus=visual_focus,
        main_character=main_character,
        style=style or "",
        seed=(int(cue.get("seed")) if str(cue.get("seed") or "").strip().isdigit() else 0),
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


def _verify_selected_images(images_dir: Path, indices: List[int]) -> Tuple[bool, List[int]]:
    missing: List[int] = []
    for i in indices:
        p = images_dir / f"{i:04d}.png"
        if not p.exists():
            missing.append(i)
    return (len(missing) == 0), missing


def _parse_indices(expr: str) -> List[int]:
    """
    Parse a comma-separated list of indices and ranges.

    Examples:
      "6,8,10-12" -> [6, 8, 10, 11, 12]
      "28-30" -> [28, 29, 30]
    """
    raw = str(expr or "").strip()
    if not raw:
        return []
    out: List[int] = []
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        if "-" in t:
            a, b = [x.strip() for x in t.split("-", 1)]
            if not a.isdigit() or not b.isdigit():
                raise SystemExit(f"Invalid --indices range: {t!r}")
            lo = int(a)
            hi = int(b)
            if hi < lo:
                lo, hi = hi, lo
            out.extend(list(range(lo, hi + 1)))
            continue
        if not t.isdigit():
            raise SystemExit(f"Invalid --indices item: {t!r}")
        out.append(int(t))
    # Stable unique order
    return sorted(set([i for i in out if i > 0]))


def _diversity_note_for_index(idx: int) -> str:
    variants = [
        "Variation: different camera angle (high angle), slightly wider framing.",
        "Variation: close-up detail shot, shallow depth of field.",
        "Variation: side angle, asymmetrical composition, negative space.",
        "Variation: top-down flat lay composition, clean subject separation.",
        "Variation: low angle, longer lens feel, cinematic perspective.",
        "Variation: backlit silhouette / rim light, gentle haze.",
        "Variation: tighter crop on the key object, minimal background.",
        "Variation: wider establishing shot, different setting context.",
    ]
    return variants[(idx - 1) % len(variants)]


def _resolve_fixed_routing_model_key(*, task: str) -> Optional[str]:
    """
    Resolve the expected model key when image routing is fixed by env/profile.

    This mirrors ImageClient's lockdown check and is used to:
    - shape prompts for the expected model without forcing call-time overrides
    - avoid model_key conflicts when routing is fixed
    """
    try:
        from factory_common.image_client import ImageClient

        client = ImageClient()
        selector = client._resolve_forced_model_key(task=task)  # type: ignore[attr-defined]
        if not selector:
            override = client._resolve_profile_task_override(task=task)  # type: ignore[attr-defined]
            mk = override.get("model_key") if isinstance(override, dict) else None
            if isinstance(mk, str) and mk.strip():
                selector = mk.strip()
        if not selector:
            return None
        resolved = client._resolve_model_key_selector(task=task, selector=selector)  # type: ignore[attr-defined]
        out = (resolved or selector).strip()
        return out or None
    except Exception:
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="srt2images output run dir (contains image_cues.json)")
    ap.add_argument("--channel", help="Override channel id (e.g., CH02). If omitted, inferred from run_dir name.")
    ap.add_argument(
        "--nanobanana",
        default="batch",
        choices=["batch", "direct", "none"],
        help="Image generation mode: batch (Gemini Batch when supported), direct (sync ImageClient), none (skip). Default: batch.",
    )
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
        "--model-key",
        help="Force image model key for this run (e.g., f-1, g-1, img-gemini-flash-1). If omitted, uses cue.image_model_key or env.",
    )
    ap.add_argument(
        "--indices",
        help="Regenerate only these 1-based cue indices (comma-separated, supports ranges: 6,8,10-12).",
    )
    ap.add_argument(
        "--ensure-diversity-note",
        action="store_true",
        help="If diversity_note is missing, add a deterministic variation hint per cue index (helps avoid repeated compositions).",
    )
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
    ap.add_argument(
        "--personless",
        default=None,
        action=argparse.BooleanOptionalAction,
        help="Force personless scenes (no humans). Default comes from channel preset.",
    )
    ap.add_argument(
        "--forbid-text",
        default=None,
        action=argparse.BooleanOptionalAction,
        help="If true, add extra 'blank/unmarked' hints for text/number-prone props. Default comes from channel preset.",
    )
    ap.add_argument(
        "--use-persona",
        default=None,
        action=argparse.BooleanOptionalAction,
        help="Force persona prompt injection. Default comes from channel preset persona_required.",
    )
    ap.add_argument(
        "--avoid-props",
        default="",
        help="Comma-separated props to treat as text/number-prone when --forbid-text is enabled.",
    )
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
    # Historical default: these channels require strict visual continuity (characters/settings) across frames.
    legacy_persona_channels = {"CH01", "CH05", "CH22", "CH23"}
    use_persona_default = bool((preset.persona_required if preset else False) or (channel in legacy_persona_channels))
    gen_conf = preset.config_model.image_generation if preset and preset.config_model else None
    model_key_default = str(getattr(gen_conf, "model_key", "") or "")
    personless_default = bool(getattr(gen_conf, "personless_default", False))
    forbid_text_default = bool(getattr(gen_conf, "forbid_text_default", True))
    avoid_props_default = str(getattr(gen_conf, "avoid_props", "") or "")
    negative_default = str(getattr(gen_conf, "negative_prompt", "") or "")

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

    personless = bool(args.personless) if args.personless is not None else personless_default
    forbid_text = bool(args.forbid_text) if args.forbid_text is not None else forbid_text_default
    use_persona = bool(args.use_persona) if args.use_persona is not None else use_persona_default
    if personless:
        use_persona = False
    avoid_props = str(args.avoid_props or avoid_props_default)

    if not (args.negative or "").strip():
        if negative_default.strip():
            args.negative = negative_default.strip()
        else:
            args.negative = _join_negative(
                DEFAULT_NEGATIVE_PERSONLESS if personless else "",
                DEFAULT_NEGATIVE_NO_TEXT if forbid_text else "",
            )

    total = len(cues)
    limit = int(args.max or 0)
    if limit > 0:
        total = min(total, limit)

    fixed_routing_model_key: Optional[str] = None
    if lockdown_active():
        fixed_routing_model_key = _resolve_fixed_routing_model_key(task="visual_image_gen")

    selected_indices: List[int] = []
    if args.indices:
        selected_indices = _parse_indices(args.indices)
        if limit > 0:
            # Keep historical behavior: --max applies to the overall run length, but indices is explicit.
            pass
        # Validate against full cue length, not `total` (which may be --max limited).
        bad = [i for i in selected_indices if i < 1 or i > len(cues)]
        if bad:
            raise SystemExit(f"--indices out of range (1..{len(cues)}): {bad}")

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
    generated_indices: List[int] = []
    iter_indices = selected_indices if selected_indices else list(range(1, total + 1))
    for i in iter_indices:
        cue = cues[i - 1]
        if not isinstance(cue, dict):
            continue
        if cue.get("asset_relpath"):
            # Never regenerate b-roll / asset-backed frames.
            continue
        if not str(cue.get("seed") or "").strip().isdigit():
            cue["seed"] = _stable_cue_seed(run_dir, int(cue.get("index") or i))
        if args.ensure_diversity_note and not str(cue.get("diversity_note") or "").strip():
            cue["diversity_note"] = _diversity_note_for_index(i)

        # Default persona policy (can be overridden via CLI).
        cue["use_persona"] = bool(use_persona)

        visual_focus = (cue.get("visual_focus") or "").strip()
        main_character = ""
        if personless:
            visual_focus = _derive_personless_visual_focus(cue)
            main_character = "None (personless scene; do not draw humans)"
            cue["use_persona"] = False

        if visual_focus:
            visual_focus = _sanitize_visual_focus_for_no_text(
                visual_focus, enabled=bool(forbid_text), avoid_props=avoid_props
            )
            cue["visual_focus"] = visual_focus

        cue_model_key = str(cue.get("image_model_key") or "").strip()
        cli_model_key = str(args.model_key or "").strip()

        # If routing is fixed (env/profile) under lockdown, per-call model overrides are forbidden.
        # Avoid conflicts by removing cue-level overrides for regenerated indices.
        if fixed_routing_model_key:
            if cli_model_key:
                raise SystemExit(
                    "\n".join(
                        [
                            "[LOCKDOWN] --model-key is forbidden under fixed image routing.",
                            f"- expected: {fixed_routing_model_key}",
                            f"- call_time: {cli_model_key}",
                            "- fix: unset --model-key and let routing select, or change routing via UI (/image-model-routing).",
                            "- debug: set YTM_EMERGENCY_OVERRIDE=1 for this run (not for normal ops).",
                        ]
                    )
                )
            cue.pop("image_model_key", None)
            model_key_for_prompt = fixed_routing_model_key
        else:
            # No fixed routing: only override when explicitly requested.
            if cli_model_key:
                cue["image_model_key"] = cli_model_key
                model_key_for_prompt = cli_model_key
            elif cue_model_key:
                model_key_for_prompt = cue_model_key
            else:
                model_key_for_prompt = model_key_default or None

        prompt = _build_prompt(
            cue,
            template_text=template_text,
            model_key=(model_key_for_prompt or None),
            style=style,
            negative=args.negative,
            size_str=size_str,
            extra_suffix=extra_suffix,
            include_script_excerpt=include_script_excerpt,
            forbid_text=bool(forbid_text),
            avoid_props=avoid_props,
            visual_focus=visual_focus,
            main_character=main_character,
        )
        cue["prompt"] = prompt
        cue["image_path"] = str(images_dir / f"{i:04d}.png")
        if anchor_path:
            cue["input_images"] = [anchor_path]
        gen_cues.append(cue)
        generated_indices.append(i)

    if selected_indices:
        skipped = sorted(set(selected_indices) - set(generated_indices))
        if skipped:
            print(f"[SKIP] asset_relpath indices skipped: {skipped}")

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

    nanobanana_mode = str(args.nanobanana or "").strip().lower()
    if nanobanana_mode == "none":
        print("[SKIP] nanobanana=none (prompts updated only; image generation skipped)")
        return

    # Generate images (rate-limited inside nanobanana_client)
    generate_image_batch(
        gen_cues,
        mode=nanobanana_mode,
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

    if selected_indices:
        ok, missing = _verify_selected_images(images_dir, generated_indices)
    else:
        ok, missing = _verify_images(images_dir, expected=total)
    if not ok:
        raise SystemExit(f"Image generation incomplete: missing={missing[:10]} (total_missing={len(missing)})")
    print(f"[DONE] images={total} dir={images_dir}")


if __name__ == "__main__":
    main()
