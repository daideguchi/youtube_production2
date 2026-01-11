#!/usr/bin/env python3
"""
Refresh `cues[*].prompt` inside an existing srt2images run_dir (image_cues.json),
without any LLM calls or image generation.

Why:
- Prompt template / global guardrails may evolve (e.g., removing priming terms).
- Existing run_dirs should be updatable without changing cue timings.

What it edits:
- Overwrites `<run_dir>/image_cues.json` (creates `image_cues.json.bak_<stamp>`).

Usage:
  PYTHONPATH=".:packages" python3 -m video_pipeline.tools.refresh_run_prompts \
    --run workspaces/video/runs/CH02-078_mix433_20260106
"""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from video_pipeline.tools._tool_bootstrap import bootstrap as tool_bootstrap
except Exception:
    from _tool_bootstrap import bootstrap as tool_bootstrap  # type: ignore

tool_bootstrap(load_env=False)

from factory_common.artifacts.utils import utc_now_iso  # noqa: E402
from video_pipeline.src.config.channel_resolver import (  # noqa: E402
    ChannelPresetResolver,
    infer_channel_id_from_path,
)
from video_pipeline.src.srt2images.prompt_builder import build_prompt_for_image_model  # noqa: E402


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _infer_channel_from_run_dir(run_dir: Path) -> Optional[str]:
    m = re.search(r"(CH\d{2})", run_dir.name.upper())
    if m:
        return m.group(1)
    return infer_channel_id_from_path(str(run_dir))


def _size_str(payload: Dict[str, Any]) -> str:
    size = payload.get("size") or {}
    try:
        w = int((size or {}).get("width") or 1920)
        h = int((size or {}).get("height") or 1080)
    except Exception:
        w, h = 1920, 1080
    return f"{w}x{h}"


def _backup_file(path: Path) -> Path:
    stamp = utc_now_iso().replace("-", "").replace(":", "")
    backup = path.with_suffix(path.suffix + f".bak_{stamp}")
    backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    return backup


def _build_summary_for_prompt(*, cue: Dict[str, Any], extra_suffix: str) -> str:
    parts: List[str] = []

    refined = str(cue.get("refined_prompt") or "").strip()
    if refined:
        parts.append(refined)
    else:
        visual_focus = str(cue.get("visual_focus") or "").strip()
        if visual_focus:
            parts.append(f"Visual Focus: {visual_focus}")

        summary = str(cue.get("summary") or "").strip()
        if summary:
            parts.append(f"Scene: {summary}")

        emotional_tone = str(cue.get("emotional_tone") or "").strip()
        if emotional_tone:
            parts.append(f"Tone: {emotional_tone}")

        role_tag = str(cue.get("role_tag") or "").strip()
        if role_tag:
            parts.append(f"Role: {role_tag}")

        section_type = str(cue.get("section_type") or "").strip()
        if section_type:
            parts.append(f"Section Type: {section_type}")

        shot_hint = str(cue.get("shot_hint") or "").strip()
        if shot_hint:
            parts.append(f"Shot Hint: {shot_hint}")

    diversity = str(cue.get("diversity_note") or "").strip()
    if diversity:
        parts.append(diversity)

    if extra_suffix.strip():
        parts.append(extra_suffix)

    return " \n".join([p for p in parts if p.strip()])


def refresh_run_prompts(*, run_dir: Path, channel_override: Optional[str], dry_run: bool) -> int:
    cues_path = run_dir / "image_cues.json"
    if not cues_path.exists():
        raise SystemExit(f"Missing image_cues.json: {cues_path}")

    payload = _read_json(cues_path)
    cues = payload.get("cues") or []
    if not isinstance(cues, list) or not cues:
        raise SystemExit(f"No cues in: {cues_path}")

    channel = (channel_override or _infer_channel_from_run_dir(run_dir) or "").upper()
    if not channel:
        raise SystemExit(f"Failed to infer channel from run_dir: {run_dir}")

    preset = ChannelPresetResolver().resolve(channel)
    if not preset or not preset.prompt_template:
        raise SystemExit(f"Channel preset missing prompt_template: {channel}")

    gen_conf = preset.config_model.image_generation if preset and preset.config_model else None
    personless_default = bool(getattr(gen_conf, "personless_default", False))
    model_key_default = str(getattr(gen_conf, "model_key", "") or "")

    template_path_str = preset.resolved_prompt_template()
    if not template_path_str:
        raise SystemExit(f"Failed to resolve prompt_template path for channel: {channel}")
    template_path = Path(template_path_str)
    if not template_path.exists():
        raise SystemExit(f"Prompt template not found: {template_path}")
    template_text = template_path.read_text(encoding="utf-8")
    style = str(preset.style or "").strip()

    extra_suffix_parts: List[str] = []
    if preset.prompt_suffix:
        extra_suffix_parts.append(str(preset.prompt_suffix))
    if preset.character_note:
        extra_suffix_parts.append(str(preset.character_note))
    extra_suffix = "\n".join([x for x in extra_suffix_parts if x.strip()])

    # Subject-first is only used where explicitly helpful (historically CH01).
    prepend_summary = channel == "CH01"

    updated = 0
    size_str = _size_str(payload)
    for cue in cues:
        if not isinstance(cue, dict):
            continue
        summary_for_prompt = _build_summary_for_prompt(cue=cue, extra_suffix=extra_suffix)
        main_character = ""
        if personless_default:
            main_character = "None (personless scene; do not draw humans)."
        elif cue.get("use_persona") is True:
            main_character = "Use recurring main character/persona (keep identical face/clothes/age)."

        effective_model_key = (
            str(cue.get("image_model_key") or model_key_default or os.getenv("IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN") or "")
            .strip()
            or None
        )

        cue["prompt"] = build_prompt_for_image_model(
            template_text,
            model_key=effective_model_key,
            prepend_summary=prepend_summary,
            summary=summary_for_prompt,
            visual_focus=str(cue.get("visual_focus") or ""),
            main_character=main_character,
            style=style,
            seed=(int(cue.get("seed")) if str(cue.get("seed") or "").strip().isdigit() else 0),
            size=size_str,
            negative="",
        )
        updated += 1

    # Keep original generated_at; write an explicit refresh marker.
    payload["cues"] = cues
    payload["prompts_refreshed_at"] = utc_now_iso()
    payload["prompts_refreshed_by"] = (os.getenv("LLM_AGENT_NAME") or "").strip() or "unknown"

    if dry_run:
        print(f"[DRY] {run_dir.name}: updated_prompts={updated} ({cues_path})")
        return updated

    backup = _backup_file(cues_path)
    _write_json(cues_path, payload)
    print(f"[OK] {run_dir.name}: updated_prompts={updated} backup={backup.name}")
    return updated


def main() -> int:
    ap = argparse.ArgumentParser(description="Refresh cue prompts in run_dir/image_cues.json (no LLM, no images).")
    ap.add_argument("--run", action="append", required=True, help="Target run_dir (repeatable)")
    ap.add_argument("--channel", help="Override channel id (e.g., CH02)")
    ap.add_argument("--dry-run", action="store_true", help="Do not write; print planned changes")
    args = ap.parse_args()

    total_updated = 0
    for raw in args.run:
        run_dir = Path(str(raw)).expanduser().resolve()
        total_updated += refresh_run_prompts(run_dir=run_dir, channel_override=args.channel, dry_run=bool(args.dry_run))
    print(f"[DONE] runs={len(args.run)} cues_updated={total_updated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
