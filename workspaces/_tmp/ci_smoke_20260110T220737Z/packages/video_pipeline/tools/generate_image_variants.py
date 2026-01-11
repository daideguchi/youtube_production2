#!/usr/bin/env python3
"""
Generate multiple style variants for an existing srt2images run_dir that already has `image_cues.json`.

- No text LLM calls are made here (it reuses existing cues + prompt template).
- Outputs are written under `<run_dir>/image_variants/<variant_id>/images/0001.png...`
- Each variant directory includes:
  - variant_meta.json (style/model/template summary)
  - image_cues.json (prompts + image_path pointing to the variant images)

Usage (repo root):
  ./scripts/with_ytm_env.sh python3 packages/video_pipeline/tools/generate_image_variants.py \
    --run workspaces/video/runs/CH02-001 \
    --preset watercolor_washi --preset cyberpunk_neon \
    --model-key fireworks_flux_1_schnell_fp8
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

import yaml

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from factory_common.paths import repo_root

from video_pipeline.src.config.channel_resolver import ChannelPresetResolver, infer_channel_id_from_path  # noqa: E402
from video_pipeline.src.srt2images.prompt_builder import build_prompt_for_image_model  # noqa: E402
from video_pipeline.src.srt2images.nanobanana_client import generate_image_batch  # noqa: E402


DEFAULT_CH02_NEGATIVE = (
    "people, human, face, portrait, body, hands, crowd, child, man, woman, elderly, old man, old woman, "
    "grandfather, grandmother, senior, wrinkles, japanese, asian, character design, mascot, "
    "text, letters, subtitle, caption, logo, watermark, UI, interface, signage, poster, typography"
)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _slugify(text: str, *, max_len: int = 48) -> str:
    raw = (text or "").strip().lower()
    if not raw:
        return "style"
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    if not raw:
        return "style"
    return raw[:max_len].rstrip("_")


def _style_slug(*, style_key: Optional[str], style_text: str) -> str:
    if style_key:
        return _slugify(style_key)
    base = _slugify(" ".join(style_text.split()[:6]))
    digest = hashlib.sha1(style_text.encode("utf-8")).hexdigest()[:8]
    return f"{base}__{digest}" if base else f"custom__{digest}"


def _load_style_presets() -> Dict[str, Dict[str, str]]:
    config_path = repo_root() / "configs" / "image_style_presets.yaml"
    if not config_path.exists():
        return {}
    conf = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    presets = conf.get("presets") if isinstance(conf, dict) else None
    if not isinstance(presets, dict):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for key, value in presets.items():
        if isinstance(value, str):
            out[str(key)] = {"label": str(key), "prompt": value}
            continue
        if not isinstance(value, dict):
            continue
        label = str(value.get("label") or key)
        prompt = str(value.get("prompt") or "").strip()
        if not prompt:
            continue
        out[str(key)] = {"label": label, "prompt": prompt}
    return out


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
        p = (repo_root() / p).resolve()
    if not p.exists():
        return (
            "Scene: {summary}\\n"
            "Style: {style}\\n"
            "Composition: clear subject, cinematic, no text\\n"
            "Resolution: {size}\\n"
        )
    return p.read_text(encoding="utf-8")


def _truncate(text: str, limit: int = 120) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def _build_prompt_parts(cue: Dict[str, Any], *, include_script_excerpt: bool) -> List[str]:
    parts: List[str] = []
    refined = (cue.get("refined_prompt") or "").strip()
    if refined:
        parts.append(refined)
        return parts

    vf = (cue.get("visual_focus") or "").strip()
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

    return parts


def _build_prompt(
    cue: Dict[str, Any],
    *,
    template_text: str,
    model_key: str | None,
    style: str,
    negative: str,
    size_str: str,
    extra_suffix: str,
    prepend_summary: bool,
) -> str:
    parts = _build_prompt_parts(cue, include_script_excerpt=True)

    if extra_suffix:
        parts.append(extra_suffix)

    diversity = (cue.get("diversity_note") or "").strip()
    if diversity:
        parts.append(diversity)

    summary_for_prompt = " \\n".join([p for p in parts if p.strip()])
    return build_prompt_for_image_model(
        template_text,
        model_key=model_key,
        prepend_summary=prepend_summary,
        summary=summary_for_prompt,
        visual_focus=str(cue.get("visual_focus") or ""),
        main_character=str(cue.get("main_character") or ""),
        style=style or "",
        seed=(int(cue.get("seed")) if str(cue.get("seed") or "").strip().isdigit() else 0),
        size=size_str,
        negative=negative or "",
    )


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


def _copy_if_exists(src: Path, dst: Path) -> None:
    if not src.exists() or not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst.write_bytes(src.read_bytes())
    except Exception:
        return


def _apply_forced_model_key(task: str, model_key: Optional[str]) -> None:
    env_key = f"IMAGE_CLIENT_FORCE_MODEL_KEY_{task.upper()}"
    if model_key:
        os.environ[env_key] = model_key
    else:
        os.environ.pop(env_key, None)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", required=True, help="srt2images run dir (contains image_cues.json)")
    ap.add_argument("--channel", help="Override channel id (e.g., CH02). If omitted, inferred.")
    ap.add_argument("--preset", action="append", default=None, help="Style preset key (repeatable)")
    ap.add_argument("--style", action="append", default=None, help="Raw style string (repeatable)")
    ap.add_argument("--model-key", default="", help="Force ImageClient model_key (default: channel preset or tier default)")
    ap.add_argument("--max", type=int, default=0, help="Limit number of cues/images to generate (0 = all)")
    ap.add_argument("--negative", default="", help="Optional negative prompt string")
    ap.add_argument("--timeout-sec", type=int, default=300, help="Per-image timeout seconds")
    ap.add_argument("--retry-until-success", action="store_true", help="Do not write placeholders when generation fails")
    ap.add_argument("--max-retries", type=int, default=6, help="Max retries per image (used by generator)")
    ap.add_argument("--force", action="store_true", help="If variant directory exists, back up existing images before regen")
    args = ap.parse_args(argv)

    run_dir = Path(args.run).expanduser().resolve()
    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        print(f"Missing image_cues.json: {cues_path}")
        return 2

    channel = (args.channel or infer_channel_id_from_path(str(run_dir)) or "").upper()
    if not channel:
        # fallback: try run dir name
        m = re.search(r"(CH\\d{2})", run_dir.name.upper())
        channel = m.group(1) if m else ""
    if not channel:
        print("Failed to infer --channel; pass --channel explicitly")
        return 2

    preset = ChannelPresetResolver().resolve(channel)
    tpl_path = preset.resolved_prompt_template() if preset else None
    template_text = _load_template_text(tpl_path)

    extra_suffix_parts: List[str] = []
    if preset and preset.prompt_suffix:
        extra_suffix_parts.append(str(preset.prompt_suffix))
    if preset and preset.character_note:
        extra_suffix_parts.append(str(preset.character_note))
    extra_suffix = "\n".join([x for x in extra_suffix_parts if x.strip()])

    forced_model_key: Optional[str] = (args.model_key or "").strip() or None
    if not forced_model_key:
        try:
            mk = None
            if preset and preset.config_model and getattr(preset.config_model, "image_generation", None):
                mk = getattr(preset.config_model.image_generation, "model_key", None)
            if isinstance(mk, str) and mk.strip():
                forced_model_key = mk.strip()
        except Exception:
            forced_model_key = None

    # Apply model forcing for the ImageClient task used by the pipeline (visual_image_gen).
    _apply_forced_model_key("visual_image_gen", forced_model_key)

    style_presets = _load_style_presets()
    requested_presets = [s for s in (args.preset or []) if s]
    requested_styles = [s for s in (args.style or []) if s]
    if not requested_presets and not requested_styles:
        print("No styles specified. Use --preset <key> and/or --style <text>.")
        return 2

    unknown = [k for k in requested_presets if k not in style_presets]
    if unknown:
        print("Unknown style preset(s): " + ", ".join(unknown))
        return 2

    style_jobs: List[Tuple[Optional[str], str]] = []
    for key in requested_presets:
        style_jobs.append((key, style_presets[key]["prompt"]))
    for raw in requested_styles:
        style_jobs.append((None, raw))

    payload = _read_json(cues_path)
    cues = payload.get("cues") or []
    if not isinstance(cues, list) or not cues:
        print(f"No cues in {cues_path}")
        return 2

    size = payload.get("size") or {}
    width = int(size.get("width") or 1920)
    height = int(size.get("height") or 1080)
    size_str = f"{width}x{height}"

    total = len(cues)
    limit = int(args.max or 0)
    if limit > 0:
        total = min(total, limit)

    variants_root = run_dir / "image_variants"
    variants_root.mkdir(parents=True, exist_ok=True)

    negative = (args.negative or "").strip()
    prepend_summary = channel == "CH01"
    if channel == "CH02" and not negative:
        negative = DEFAULT_CH02_NEGATIVE

    for style_key, style_text in style_jobs:
        slug = _style_slug(style_key=style_key, style_text=style_text)
        variant_id = f"{_utc_stamp()}__{slug}"
        variant_dir = variants_root / variant_id
        images_dir = variant_dir / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        # Copy persona artifacts so nanobanana_client's persona logic stays consistent per-variant.
        for name in ("persona.txt", "persona.json", "persona_mode.txt"):
            src = run_dir / name
            if src.exists():
                _copy_if_exists(src, variant_dir / name)

        if args.force:
            moved, backup_dir = _backup_existing_pngs(images_dir)
            if moved and backup_dir:
                print(f"[BACKUP] moved_pngs={moved} backup_dir={backup_dir}")

        # Build cues for this variant (copy subset only)
        gen_cues: List[Dict[str, Any]] = []
        cues_out: List[Dict[str, Any]] = []
        for i in range(1, total + 1):
            cue = cues[i - 1]
            if not isinstance(cue, dict):
                continue
            cue_copy = dict(cue)
            prompt = _build_prompt(
                cue_copy,
                template_text=template_text,
                model_key=forced_model_key,
                style=style_text,
                negative=negative,
                size_str=size_str,
                extra_suffix=extra_suffix,
                prepend_summary=prepend_summary,
            )
            cue_copy["prompt"] = prompt
            cue_copy["image_path"] = str(images_dir / f"{i:04d}.png")
            cues_out.append(cue_copy)
            gen_cues.append(cue_copy)

        variant_payload = dict(payload)
        variant_payload["generated_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        variant_payload["cues"] = cues_out
        variant_payload["variant"] = {
            "id": variant_id,
            "style_key": style_key,
            "style": style_text,
            "model_key": forced_model_key,
            "prompt_template": tpl_path,
            "channel": channel,
        }
        variant_dir.mkdir(parents=True, exist_ok=True)
        _write_json(variant_dir / "image_cues.json", variant_payload)

        meta = {
            "id": variant_id,
            "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "channel": channel,
            "style_key": style_key,
            "style": style_text,
            "model_key": forced_model_key,
            "prompt_template": tpl_path,
            "size": {"width": width, "height": height},
            "cues": total,
        }
        _write_json(variant_dir / "variant_meta.json", meta)
        print(f"[VARIANT] id={variant_id} style_key={style_key or 'custom'} model_key={forced_model_key or '(tier default)'}")

        generate_image_batch(
            gen_cues,
            mode="direct",
            concurrency=1,
            force=True,
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
            print(f"[ERROR] variant incomplete: id={variant_id} missing={missing[:10]} total_missing={len(missing)}")
            return 1
        print(f"[DONE] id={variant_id} images={total} dir={images_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
