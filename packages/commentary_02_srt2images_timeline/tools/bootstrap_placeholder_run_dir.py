#!/usr/bin/env python3
"""
Bootstrap a srt2images-style run_dir for CapCut draft work.

IMPORTANT:
- Mechanical fixed-interval splitting is forbidden (contract/quality).
- This tool therefore uses the SAME cue planning task as the main pipeline:
    task=visual_image_cues_plan
  In THINK MODE, this becomes a single pending task (agent queue) instead of calling an API.

Creates/ensures:
  - image_cues.json   (LLM/agent planned sections → cues)
  - images/0001.png.. (placeholder images; optional but useful for template checks)
  - belt_config.json  (single belt spanning full duration; optional helper)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PIL import Image, ImageChops


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_ROOT.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from config.channel_resolver import ChannelPresetResolver, infer_channel_id_from_path  # noqa: E402
from srt2images.srt_parser import parse_srt  # noqa: E402
from srt2images.cues_plan import make_cues_from_sections, plan_sections_via_router  # noqa: E402
from factory_common.artifacts.srt_segments import build_srt_segments_artifact, write_srt_segments_artifact  # noqa: E402
from factory_common.artifacts.visual_cues_plan import (  # noqa: E402
    build_visual_cues_plan_artifact,
    load_visual_cues_plan,
    write_visual_cues_plan,
)
from factory_common.artifacts.utils import utc_now_iso  # noqa: E402
from factory_common.timeline_manifest import parse_episode_id, sha1_file  # noqa: E402


def _make_placeholder_image(width: int, height: int, seed: int) -> Image.Image:
    palette = [
        (10, 12, 18),
        (12, 16, 28),
        (18, 18, 24),
        (14, 22, 34),
        (20, 16, 28),
    ]
    base = Image.new("RGB", (width, height), palette[seed % len(palette)])
    noise = Image.effect_noise((width, height), 64).convert("L")
    noise_rgb = Image.merge("RGB", (noise, noise, noise))
    mixed = ImageChops.add_modulo(base, noise_rgb)
    return mixed


def _srt_total_sec(segments: List[Dict[str, Any]]) -> float:
    if not segments:
        return 0.0
    return float(max(float(s.get("end", 0.0)) for s in segments))


def bootstrap_run_dir(
    *,
    run_dir: Path,
    srt_path: Path,
    width: int,
    height: int,
    fps: int,
    imgdur: float,
    crossfade: float,
    main_title: str,
    force: bool,
    write_placeholders: bool,
) -> Tuple[int, float]:
    run_dir.mkdir(parents=True, exist_ok=True)

    segments = parse_srt(srt_path)
    try:
        episode = parse_episode_id(str(srt_path))
        episode_id = episode.episode if episode else None
        seg_art = build_srt_segments_artifact(srt_path=srt_path, segments=segments, episode=episode_id)
        write_srt_segments_artifact(run_dir / "srt_segments.json", seg_art)
    except Exception:
        # Not fatal; run_dir can still be bootstrapped.
        pass
    total = _srt_total_sec(segments)
    if total <= 0.0:
        raise SystemExit(f"SRT appears empty or invalid: {srt_path}")

    cues_path = run_dir / "image_cues.json"
    plan_path = run_dir / "visual_cues_plan.json"
    images_dir = run_dir / "images"
    belt_path = run_dir / "belt_config.json"

    if force and cues_path.exists():
        cues_path.unlink()
    if force and plan_path.exists():
        try:
            plan_path.unlink()
        except Exception:
            pass

    if not cues_path.exists():
        # Plan sections via the router (in THINK MODE this becomes one pending task).
        channel_id = infer_channel_id_from_path(str(srt_path)) or ""
        preset = ChannelPresetResolver().resolve(channel_id) if channel_id else None

        base_seconds = float(imgdur) if imgdur > 0 else 30.0
        try:
            if channel_id.upper() == "CH01":
                base_seconds = 12.0
            elif preset and preset.config_model and getattr(preset.config_model, "image_generation", None):
                cfg_period = float(preset.config_model.image_generation.base_period or 0)
                if cfg_period > 0:
                    base_seconds = cfg_period
        except Exception:
            pass

        style_hint_parts = []
        if preset:
            if preset.style:
                style_hint_parts.append(f"Style: {preset.style}")
            if preset.tone_profile:
                style_hint_parts.append(f"Tone: {preset.tone_profile}")
            if preset.prompt_suffix:
                style_hint_parts.append(f"Visual Guidelines: {preset.prompt_suffix}")
        style_hint = "\n".join(style_hint_parts)

        planned = None
        if plan_path.exists() and not force:
            try:
                plan = load_visual_cues_plan(plan_path, expected_srt_path=srt_path)
                if plan.status != "ready":
                    raise ValueError(f"status={plan.status}")
                from srt2images.cues_plan import PlannedSection as _PlannedSection  # noqa: WPS433

                planned = [
                    _PlannedSection(
                        start_segment=s.start_segment,
                        end_segment=s.end_segment,
                        summary=s.summary,
                        visual_focus=s.visual_focus,
                        emotional_tone=s.emotional_tone,
                        persona_needed=bool(s.persona_needed),
                        role_tag=s.role_tag,
                        section_type=s.section_type,
                    )
                    for s in plan.sections
                ]
            except Exception:
                planned = None

        if planned is None:
            try:
                planned = plan_sections_via_router(
                    segments=segments,
                    channel_id=channel_id,
                    base_seconds=base_seconds,
                    style_hint=style_hint,
                )
                episode = parse_episode_id(str(srt_path))
                episode_id = episode.episode if episode else None
                plan_art = build_visual_cues_plan_artifact(
                    srt_path=srt_path,
                    segment_count=len(segments),
                    base_seconds=base_seconds,
                    sections=[
                        {
                            "start_segment": s.start_segment,
                            "end_segment": s.end_segment,
                            "summary": s.summary,
                            "visual_focus": s.visual_focus,
                            "emotional_tone": s.emotional_tone,
                            "persona_needed": bool(s.persona_needed),
                            "role_tag": s.role_tag,
                            "section_type": s.section_type,
                        }
                        for s in planned
                    ],
                    episode=episode_id,
                    style_hint=style_hint,
                    status="ready",
                    llm_task={"task": "visual_image_cues_plan"},
                )
                write_visual_cues_plan(plan_path, plan_art)
            except SystemExit as e:
                if not plan_path.exists():
                    import re as _re
                    msg = str(e)
                    m = _re.search(r"task_id:\\s*([A-Za-z0-9_\\-]+)", msg)
                    task_id = m.group(1) if m else ""
                    episode = parse_episode_id(str(srt_path))
                    episode_id = episode.episode if episode else None
                    plan_art = build_visual_cues_plan_artifact(
                        srt_path=srt_path,
                        segment_count=len(segments),
                        base_seconds=base_seconds,
                        sections=[],
                        episode=episode_id,
                        style_hint=style_hint,
                        status="pending",
                        llm_task={
                            "task": "visual_image_cues_plan",
                            "task_id": task_id,
                            "note": "THINK/AGENT pending created; fill sections or complete agent task then rerun.",
                        },
                        meta={"pending_reason": msg},
                    )
                    write_visual_cues_plan(plan_path, plan_art)
                raise
        cues = make_cues_from_sections(segments=segments, sections=planned, fps=fps)

        payload = {
            "schema": "ytm.image_cues.v1",
            "generated_at": utc_now_iso(),
            "source_srt": {"path": str(srt_path), "sha1": sha1_file(srt_path)},
            "fps": int(fps),
            "size": {"width": int(width), "height": int(height)},
            "crossfade": float(crossfade),
            "imgdur": float(imgdur),
            "cues": cues,
        }
        cues_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        data = json.loads(cues_path.read_text(encoding="utf-8"))
        cues = data.get("cues") or []

    if write_placeholders:
        if images_dir.exists() and force:
            for p in images_dir.glob("*.png"):
                try:
                    p.unlink()
                except Exception:
                    pass
        images_dir.mkdir(parents=True, exist_ok=True)
        for i in range(len(cues)):
            p = images_dir / f"{i+1:04d}.png"
            if p.exists() and not force:
                continue
            img = _make_placeholder_image(width, height, seed=i)
            img.save(p, format="PNG", compress_level=1)

    # Minimal belt helper (optional). CapCut belt layer generation can override later.
    belt = {
        "episode": "",
        "total_duration": round(float(total), 3),
        "belts": [{"text": f"1. {main_title}".strip(), "start": 0.0, "end": round(float(total), 3)}],
        "opening_offset": 0.0,
        "main_title": main_title,
    }
    belt_path.write_text(json.dumps(belt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return int(len(cues)), float(total)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--srt", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--size", default="1920x1080")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--imgdur", type=float, default=20.0)
    ap.add_argument("--crossfade", type=float, default=0.5)
    ap.add_argument("--main-title", default="")
    ap.add_argument("--force", action="store_true")
    ap.add_argument(
        "--no-placeholders",
        action="store_true",
        help="Do not generate placeholder images/ (only write image_cues.json + belt_config.json)",
    )
    args = ap.parse_args()

    if not args.srt.exists():
        raise SystemExit(f"SRT not found: {args.srt}")
    m = re.match(r"^(?P<w>\\d+)x(?P<h>\\d+)$", str(args.size).strip())
    if not m:
        raise SystemExit(f"Invalid --size: {args.size} (expected 1920x1080)")
    width, height = int(m.group("w")), int(m.group("h"))

    cues, total = bootstrap_run_dir(
        run_dir=args.out,
        srt_path=args.srt,
        width=width,
        height=height,
        fps=args.fps,
        imgdur=args.imgdur,
        crossfade=args.crossfade,
        main_title=args.main_title.strip(),
        force=args.force,
        write_placeholders=not args.no_placeholders,
    )
    print(f"[BOOTSTRAP] out={args.out} cues={cues} total_sec={total:.3f}")


if __name__ == "__main__":
    main()
