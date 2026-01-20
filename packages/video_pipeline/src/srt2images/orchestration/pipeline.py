import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from ...config.channel_resolver import (
    ChannelPresetResolver,
    infer_channel_id_from_path,
)
from ..srt_parser import parse_srt
from ..cue_maker import make_cues
from ..cues_plan import (
    is_think_or_agent_mode,
    make_cues_from_sections,
    plan_sections_via_router,
)
from ..llm_context_analyzer import LLMContextAnalyzer
from ..prompt_builder import build_prompt_for_image_model, _looks_like_fireworks_flux_schnell
from ..llm_prompt_refiner import PromptRefiner
from ..role_asset_router import RoleAssetRouter
from ..generators import get_image_generator
from ..engines.remotion_engine import setup_and_render_remotion
from ..engines.capcut_engine import build_capcut_draft
from .utils import ensure_out_dirs, setup_logging, save_json, parse_size
from ..nanobanana_client import QuotaExhaustedError
from ..visual_bible import VisualBibleGenerator
from factory_common.artifacts.srt_segments import build_srt_segments_artifact, write_srt_segments_artifact
from factory_common.artifacts.visual_cues_plan import (
    build_visual_cues_plan_artifact,
    load_visual_cues_plan,
    write_visual_cues_plan,
)
from factory_common.artifacts.utils import utc_now_iso
from factory_common.timeline_manifest import parse_episode_id, sha1_file
from factory_common.paths import video_pkg_root

def run_pipeline(args):
    def _env_truthy(name: str) -> bool:
        return (os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on", "y"}

    disable_text_llm = _env_truthy("SRT2IMAGES_DISABLE_TEXT_LLM")
    resolver = ChannelPresetResolver()
    # NOTE: argparse defaults are not preserved after config merging, so detect explicit CLI overrides via argv.
    cli_overrides_prompt_template = "--prompt-template" in sys.argv
    cli_overrides_style = "--style" in sys.argv
    detected_channel = args.channel or infer_channel_id_from_path(args.srt)
    channel_preset = resolver.resolve(detected_channel)
    if channel_preset:
        args.channel = detected_channel
        if channel_preset.prompt_template and not cli_overrides_prompt_template:
            resolved_template = channel_preset.resolved_prompt_template()
            if resolved_template:
                logging.info(
                    "Applying channel preset prompt template (%s)",
                    resolved_template,
                )
                args.prompt_template = resolved_template
        if channel_preset.style and not cli_overrides_style:
            logging.info("Applying channel preset style (%s)", channel_preset.style)
            args.style = channel_preset.style
    else:
        args.channel = detected_channel

    channel_upper = (args.channel or detected_channel or "").upper() if (args.channel or detected_channel) else ""
    env_model_override = (os.getenv("IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN") or "").strip()
    forced_model_key_from_preset = None
    try:
        if channel_preset and channel_preset.config_model and getattr(channel_preset.config_model, "image_generation", None):
            mk = getattr(channel_preset.config_model.image_generation, "model_key", None)
            if isinstance(mk, str) and mk.strip():
                forced_model_key_from_preset = mk.strip()
    except Exception:
        forced_model_key_from_preset = None

    forced_model_key = env_model_override or forced_model_key_from_preset
    forced_model_source = (
        "env_override"
        if env_model_override
        else ("channel_preset" if forced_model_key_from_preset else "tier_default")
    )

    if forced_model_key:
        os.environ["IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN"] = forced_model_key
    else:
        os.environ.pop("IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN", None)

    logging.info(
        "[image_gen] channel=%s task=visual_image_gen model_key=%s source=%s",
        channel_upper or "unknown",
        forced_model_key or "(tier default)",
        forced_model_source,
    )

    out_dir = Path(args.out).resolve()
    ensure_out_dirs(out_dir)
    setup_logging(out_dir)

    if channel_preset:
        save_json(
            out_dir / "channel_preset.json",
            {
                "channel_id": channel_preset.channel_id,
                "name": channel_preset.name,
                "prompt_template": channel_preset.prompt_template,
                "style": channel_preset.style,
                "capcut_template": channel_preset.capcut_template,
                "position": channel_preset.position,
                "belt": channel_preset.belt,
                "status": channel_preset.status,
                "notes": channel_preset.notes,
            },
        )

    size = parse_size(args.size)

    # 1) Parse SRT
    logging.info("Parsing SRT: %s", args.srt)
    srt_path = Path(args.srt)
    segments = parse_srt(srt_path)
    logging.info("Parsed %d segments", len(segments))
    try:
        episode = parse_episode_id(str(srt_path))
        episode_id = episode.episode if episode else None
        # LLM trace key (enables prompt-level tracing in factory_common.llm_router).
        # Prefer episode id (CHxx-NNN); fallback to channel-only.
        if episode_id:
            os.environ["LLM_ROUTING_KEY"] = episode_id
        elif channel_upper and not (os.getenv("LLM_ROUTING_KEY") or "").strip():
            os.environ["LLM_ROUTING_KEY"] = channel_upper
        seg_art = build_srt_segments_artifact(srt_path=srt_path, segments=segments, episode=episode_id)
        write_srt_segments_artifact(out_dir / "srt_segments.json", seg_art)
        logging.info("Wrote %s", out_dir / "srt_segments.json")
    except Exception as e:
        logging.warning("Failed to write srt_segments.json (non-fatal): %s", e)

    # Decide cue planning strategy:
    # - Default: existing multi-step pipeline (visual_bible + visual_section_plan + prompt builder)
    # - THINK/AGENT mode: single-task cue planning to avoid repeated stop/resume loops
    use_cues_plan = is_think_or_agent_mode() or (os.getenv("SRT2IMAGES_CUES_PLAN_MODE") or "").strip().lower() in (
        "1",
        "true",
        "plan",
    )
    # CH02 defaults to FLUX schnell and needs per-cue "refined_prompt" to avoid repeated compositions.
    # Prefer deterministic cues_plan (single LLM task; THINK/AGENT friendly) over multi-pass refiners.
    if channel_upper == "CH02":
        use_cues_plan = True
    # CH12: slower pacing (~25s per image) is required; prefer deterministic cues_plan.
    if channel_upper == "CH12":
        use_cues_plan = True
        # NOTE: cues_plan is always driven by `visual_image_cues_plan` (API or THINK/AGENT pending).

    # cue-mode=single is a deterministic 1-cue flow (no planning/LLM needed).
    if args.cue_mode == "single":
        use_cues_plan = False

    # 1.5) Generate Visual Bible (Before cues)
    persona_text = ""
    visual_bible_data = None

    # CH02 is personless by default; do not generate/use Visual Bible persona unless explicitly needed.
    if channel_upper == "CH02":
        try:
            (out_dir / "persona_mode.txt").write_text("off\n", encoding="utf-8")
        except Exception:
            pass

    if args.cue_mode not in {"per_segment", "single"} and not use_cues_plan and channel_upper != "CH02":
        if disable_text_llm:
            logging.info("Skipping Visual Bible (SRT2IMAGES_DISABLE_TEXT_LLM=1)")
        else:
            logging.info("Generating/Loading Visual Bible...")
            try:
                bible_gen = VisualBibleGenerator()
                bible_data = bible_gen.generate(segments, out_dir=out_dir)
                visual_bible_data = bible_data
                
                # Convert to persona text for legacy prompt refiner compatibility
                chars = bible_data.get("characters", [])
                persona_lines = []
                for c in chars:
                    line = f"{c.get('name')}: {c.get('description')} (Rule: {c.get('consistency_rules','')})"
                    persona_lines.append(line)
                
                persona_text = "\n".join(persona_lines)
                if persona_text:
                    (out_dir / "persona.txt").write_text(persona_text, encoding="utf-8")
                    logging.info(f"Visual Bible loaded and persona.txt generated ({len(chars)} chars).")
                else:
                    logging.info("Visual Bible empty/no-characters.")
                    
            except SystemExit as e:
                # THINK/AGENT mode may raise SystemExit to stop the process for queued tasks.
                # Visual Bible is optional; do not abort the whole pipeline.
                logging.warning("Visual Bible generation halted (SystemExit=%s); continuing without it.", e)
                persona_text = ""
                visual_bible_data = None
            except Exception as e:
                logging.warning(f"Visual Bible generation failed: {e}")
                persona_text = ""
                visual_bible_data = None

    # 2) Build cues
    if args.cue_mode == "per_segment":
        logging.info("Building image cues per SRT segment (fps=%d)", args.fps)
        cues = []
        def _truncate_summary(text: str, limit: int = 150) -> str:
            t = " ".join(text.split())
            return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"
        for i, seg in enumerate(segments, start=1):
            start_sec = float(seg["start"])
            end_sec = float(seg["end"])
            duration = max(0.001, round(end_sec - start_sec, 3))
            text = seg.get("text", "").strip()
            summary = _truncate_summary(text)
            start_frame = int(round(start_sec * args.fps))
            end_frame = int(round(end_sec * args.fps))
            cues.append({
                "index": i,
                "start_sec": round(start_sec, 3),
                "end_sec": round(end_sec, 3),
                "duration_sec": duration,
                "text": text,
                "summary": summary,
                "start_frame": start_frame,
                "end_frame": end_frame,
                "duration_frames": max(1, end_frame - start_frame),
                "use_persona": False,  # per-segmentモードはデフォルトで人物一貫性オフ
            })
    elif args.cue_mode == "single":
        logging.info("Building a single image cue for entire video (fps=%d)", args.fps)
        last_end_sec = float(segments[-1]["end"]) if segments else 0.0
        last_end_sec = max(0.001, round(last_end_sec, 3))
        start_sec = 0.0
        end_sec = last_end_sec
        cues = [
            {
                "index": 1,
                "start_sec": round(start_sec, 3),
                "end_sec": round(end_sec, 3),
                "duration_sec": max(0.001, round(end_sec - start_sec, 3)),
                "text": "",
                "summary": "Static portrait layout (right portrait, left black). No in-image text.",
                "start_frame": 0,
                "end_frame": int(round(end_sec * args.fps)),
                "duration_frames": max(1, int(round(end_sec * args.fps))),
                "use_persona": False,
            }
        ]
    else:
        if use_cues_plan:
            # Single-task cue planning (THINK MODE friendly): segments -> planned sections -> cues.
            base_seconds = 30.0
            try:
                if (args.channel or "").upper() == "CH01":
                    base_seconds = 12.0
                elif channel_preset and channel_preset.config_model and getattr(channel_preset.config_model, "image_generation", None):
                    cfg_period = float(channel_preset.config_model.image_generation.base_period or 0)
                    if cfg_period > 0:
                        base_seconds = cfg_period
            except Exception:
                pass

            style_hint_parts = []
            if channel_preset:
                if channel_preset.style:
                    style_hint_parts.append(f"Style: {channel_preset.style}")
                if channel_preset.tone_profile:
                    style_hint_parts.append(f"Tone: {channel_preset.tone_profile}")
                if channel_preset.prompt_suffix:
                    style_hint_parts.append(f"Visual Guidelines: {channel_preset.prompt_suffix}")
            style_hint = "\n".join(style_hint_parts)

            logging.info("Building image cues via cues_plan (base_seconds=%.1f, fps=%d)", base_seconds, args.fps)
            plan_path = out_dir / "visual_cues_plan.json"
            force_plan = (os.getenv("SRT2IMAGES_FORCE_CUES_PLAN") or "").strip().lower() in {"1", "true", "yes", "on"}

            planned_sections = None
            if plan_path.exists() and not force_plan:
                try:
                    plan = load_visual_cues_plan(plan_path, expected_srt_path=srt_path)
                    if plan.status != "ready":
                        raise ValueError(f"status={plan.status} (fill sections then rerun)")
                    from ..cues_plan import PlannedSection as _PlannedSection

                    planned_sections = [
                        _PlannedSection(
                            start_segment=s.start_segment,
                            end_segment=s.end_segment,
                            summary=s.summary,
                            visual_focus=s.visual_focus,
                            emotional_tone=s.emotional_tone,
                            refined_prompt=getattr(s, "refined_prompt", "") or "",
                            persona_needed=bool(s.persona_needed),
                            role_tag=s.role_tag,
                            section_type=s.section_type,
                        )
                        for s in plan.sections
                    ]
                    logging.info("Loaded %d sections from %s", len(planned_sections), plan_path)
                except Exception as e:
                    raise SystemExit(
                        f"❌ visual_cues_plan.json invalid: {e}\n"
                        f"- path: {plan_path}\n"
                        f"- fix the file, or set SRT2IMAGES_FORCE_CUES_PLAN=1 to regenerate."
                    )

            if planned_sections is None:
                try:
                    planned_sections = plan_sections_via_router(
                        segments=segments,
                        channel_id=args.channel,
                        base_seconds=base_seconds,
                        style_hint=style_hint,
                    )
                    llm_task = {"task": "visual_image_cues_plan"}
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
                                "refined_prompt": getattr(s, "refined_prompt", "") or "",
                                "persona_needed": bool(s.persona_needed),
                                "role_tag": s.role_tag,
                                "section_type": s.section_type,
                            }
                            for s in planned_sections
                        ],
                        episode=episode_id,
                        style_hint=style_hint,
                        status="ready",
                        llm_task=llm_task,
                    )
                    write_visual_cues_plan(plan_path, plan_art)
                    logging.info("Wrote %s (status=ready)", plan_path)
                except SystemExit as e:
                    # THINK/AGENT mode: create a skeleton plan file for operators to fill.
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
                        logging.info("Wrote %s (status=pending)", plan_path)
                    raise
            cues = make_cues_from_sections(segments=segments, sections=planned_sections, fps=args.fps)
        else:
            logging.info("Building image cues (target ~%.2fs, crossfade %.2fs, fps=%d)", args.imgdur, args.crossfade, args.fps)
            # make_cues will initialize LLMContextAnalyzer, which now reads the Visual Bible we just generated.
            cues = make_cues(
                segments,
                target_imgdur=args.imgdur,
                fps=args.fps,
                channel_id=args.channel,
                visual_bible=visual_bible_data,
            )

    # 2.2) Contextual prompt refinement (LLM)
    # Prepare common style string
    common_style_parts = []
    if channel_preset:
        if channel_preset.style:
            common_style_parts.append(f"Style: {channel_preset.style}")
        if channel_preset.tone_profile:
            common_style_parts.append(f"Tone: {channel_preset.tone_profile}")
        if channel_preset.prompt_suffix:
            common_style_parts.append(f"Visual Guidelines: {channel_preset.prompt_suffix}")
    common_style_str = "\n".join(common_style_parts)

    if not use_cues_plan:
        if disable_text_llm:
            logging.info("Skipping PromptRefiner (SRT2IMAGES_DISABLE_TEXT_LLM=1)")
        else:
            try:
                refiner = PromptRefiner()
                cues = refiner.refine(
                    cues,
                    channel_id=args.channel,
                    window=1,
                    common_style=common_style_str,
                    persona=persona_text,  # Pass the persona text derived from Visual Bible
                )
            except Exception as e:
                logging.warning("Prompt refinement skipped due to error: %s", e)
    else:
        logging.info("Skipping PromptRefiner (cues_plan mode)")

    # 2.3) Attach role-based assets (channel-specific, non-invasive)
    router = RoleAssetRouter(video_pkg_root())
    router.apply(cues, channel_upper)

    # 2.4) Adjacent-cue diversity hints: avoid same pose/angle/object twice in a row
    def _normalize(text: str) -> set:
        import re
        toks = re.findall(r"[\\w一-龠ぁ-んァ-ンー]+", text.lower())
        return set(toks)

    def _add_diversity_notes(cues: List[Dict[str, Any]]) -> None:
        VARIATIONS = [
            "角度を変える（俯瞰/煽り/真横/背面）",
            "距離を変える（クローズアップ/ミドル/ロング）",
            "ポーズを変える（立つ/歩く/手を伸ばす/道具を扱う/振り向く）",
            "時間帯や天候の変化（朝/夕/夜/霧/雨上がり）",
            "前景や小物を変える（枝葉/灯籠/机/窓枠/手元の道具）",
            "視線を変える（カメラ目線/視線を外す/下を見る/遠くを見る）",
        ]
        for i, cue in enumerate(cues):
            base = cue.get("refined_prompt") or cue.get("summary") or cue.get("text") or ""
            cur_set = _normalize(base)
            prev_set = _normalize(cues[i - 1].get("refined_prompt") or cues[i - 1].get("summary") or cues[i - 1].get("text") or "") if i > 0 else set()
            sim = 0.0
            if cur_set or prev_set:
                sim = len(cur_set & prev_set) / max(1, len(cur_set | prev_set))
            if sim >= 0.65:
                # Pick two different variation hints
                hints = VARIATIONS[:]
                import random
                random.shuffle(hints)
                cue["diversity_note"] = "前後のカットと構図・距離・ポーズ・時間帯・前景を変える: " + " / ".join(hints[:2])

    if _env_truthy("SRT2IMAGES_ENABLE_HEURISTIC_DIVERSITY_NOTES"):
        try:
            _add_diversity_notes(cues)
        except Exception as e:
            logging.warning("Diversity hint generation skipped: %s", e)

    # 3) Build prompts per cue
    prompt_tpl_path = Path(args.prompt_template)
    if not prompt_tpl_path.exists():
        logging.warning("Prompt template not found at %s; using inline fallback", prompt_tpl_path)
        template_text = (
            "シーン説明: {summary}\nスタイル: {style}\n構図: 主被写体を中央/大きめ、可読性重視\n解像度: {size}\n備考: 高コントラスト/太め輪郭/情報量過多にしない\n"
        )
    else:
        template_text = prompt_tpl_path.read_text(encoding="utf-8")

    # Optional: create a 16:9 guide image and attach as input
    guide_path = None
    if getattr(args, 'use_aspect_guide', False):
        try:
            from PIL import Image
            guide_dir = out_dir / "guides"
            guide_dir.mkdir(parents=True, exist_ok=True)
            guide_path = guide_dir / "guide_1920x1080.png"
            if not guide_path.exists():
                Image.new("RGB", (size['width'], size['height']), (245, 245, 245)).save(guide_path)
        except Exception as e:
            logging.warning("Failed to create aspect guide: %s", e)

    def _truncate(text: str, limit: int = 240) -> str:
        t = " ".join(text.split())
        return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"

    # PromptRefinerのrole_hintsを取得（プロンプト構築に活用）
    refiner_hints = PromptRefiner().role_hints if hasattr(PromptRefiner, "role_hints") else {}
    buddhist_narrator_channels = {"CH12", "CH13", "CH14", "CH15", "CH16", "CH17"}
    if channel_upper in buddhist_narrator_channels:
        # CH12-17: monk narrator motif is valid; avoid generic hints that forbid monks/statues.
        refiner_hints = dict(refiner_hints)
        refiner_hints.update(
            {
                "viewer_address": "Talk directly to viewer; a calm Japanese monk narrator is acceptable if consistent. Avoid extra random people. Simple setting. No text.",
                "explanation": "Show the idea clearly; a consistent monk narrator OR symbolic objects/environment are both acceptable. Calm mood. No text.",
                "hook": "High-contrast, cinematic hook; monk narrator is acceptable if consistent. Avoid extra random people. No sitting unless stated. No text.",
            }
        )

    # In-image text tends to appear when raw script excerpts are included in prompts (esp. JP).
    # Default: DO NOT include script excerpt unless explicitly enabled.
    include_script_excerpt = (os.getenv("SRT2IMAGES_INCLUDE_SCRIPT_EXCERPT") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    english_only_prompt = (os.getenv("SRT2IMAGES_PROMPT_ENGLISH_ONLY") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    def _contains_japanese(text: str) -> bool:
        import re

        return bool(re.search(r"[一-龠ぁ-んァ-ン]", text or ""))

    def _sanitize_visual_focus_for_no_text(visual_focus: str) -> str:
        import re

        s = str(visual_focus or "").strip()
        if not s:
            return ""
        lower = s.lower()

        # Avoid prompting the model to render any text-like artifacts.
        text_words = (
            "text",
            "subtitle",
            "subtitles",
            "caption",
            "captions",
            "sign",
            "signage",
            "logo",
            "watermark",
            "letter",
            "letters",
            "number",
            "numbers",
            "word",
            "words",
            "write",
            "writing",
            "handwriting",
            "calligraphy",
        )

        def _has_word(word: str) -> bool:
            try:
                return bool(re.search(rf"\b{re.escape(word)}\b", lower))
            except Exception:
                return word in lower

        def _has_any(words: tuple[str, ...]) -> bool:
            return any(_has_word(w) for w in words)

        if _has_any(text_words):
            if _has_word("note"):
                return "Small blank note by pillow, soft moonlight, quiet room"
            if _has_any(("journal", "notebook", "paper", "page")):
                return "Open blank notebook page with a pen resting beside it, warm lantern light"
            return "Blank paper with a pen resting on it, warm lantern light"

        return s

    # Deterministic per-cue seeds:
    # - Increases variety for models that otherwise converge to similar compositions (notably FLUX schnell).
    # - Keeps regeneration reproducible across agents (same run_dir + cue index -> same seed).
    seed_mode = (os.getenv("SRT2IMAGES_CUE_SEED_MODE") or "").strip().lower()
    seed_overwrite = (os.getenv("SRT2IMAGES_CUE_SEED_OVERWRITE") or "").strip().lower() in {"1", "true", "yes", "on"}
    if seed_mode not in {"0", "false", "off", "no"}:
        import hashlib

        try:
            base_seed = int(args.seed or 0)
        except Exception:
            base_seed = 0

        def _stable_seed(label: str) -> int:
            # 31-bit positive integer seed
            h = hashlib.sha256(label.encode("utf-8")).hexdigest()[:8]
            v = int(h, 16)
            v = (v + base_seed) % 2147483647
            return v or 1

        if seed_mode in {"fixed"}:
            fixed = _stable_seed(out_dir.name)
            for cue in cues:
                if not seed_overwrite and str(cue.get("seed") or "").strip().isdigit():
                    continue
                cue["seed"] = fixed
        else:
            for pos, cue in enumerate(cues, start=1):
                if not seed_overwrite and str(cue.get("seed") or "").strip().isdigit():
                    continue
                raw_idx = cue.get("index")
                try:
                    idx = int(raw_idx)
                except Exception:
                    idx = pos
                cue["seed"] = _stable_seed(f"{out_dir.name}:{idx}")

    # Variation notes (heuristic; disabled by default):
    # apply light-but-deterministic shot variation for FLUX schnell cues
    # to avoid repeated "same lobby/storefront" compositions.
    if _env_truthy("SRT2IMAGES_ENABLE_HEURISTIC_DIVERSITY_NOTES"):
        try:
            import random

            def _is_flux_schnell(key: str | None) -> bool:
                mk = str(key or "").strip().lower()
                return mk in {"f-1", "img-flux-schnell-1", "fireworks_flux_1_schnell_fp8", "fireworks_flux_1_dev_fp8"}

            ANGLES = ["overhead", "eye-level", "low-angle", "3/4 angle", "side view"]
            DISTANCES = ["close-up", "medium shot", "wide shot"]
            LIGHTING = ["soft morning light", "warm dusk light", "cool twilight", "candle-like soft glow", "diffuse cloudy daylight"]

            for cue in cues:
                mk = str(cue.get("image_model_key") or forced_model_key or "").strip()
                if not _is_flux_schnell(mk):
                    continue
                if cue.get("use_persona") is True:
                    # Keep persona shots stable: vary only camera angle/distance.
                    pool = [f"Variation: {a}, {d}" for a in ANGLES for d in DISTANCES]
                else:
                    pool = [f"Variation: {a}, {d}, {l}" for a in ANGLES for d in DISTANCES for l in LIGHTING]
                seed_val = cue.get("seed")
                try:
                    rng = random.Random(int(seed_val))
                except Exception:
                    rng = random.Random(0)
                note = rng.choice(pool)
                existing = str(cue.get("diversity_note") or "").strip()
                cue["diversity_note"] = f"{existing}\n{note}".strip() if existing else note
        except Exception:
            # Fail-soft: prompts still work without variation notes.
            pass

    for cue in cues:
        parts = []
        
        # If we have a refined prompt, it's the master prompt.
        if cue.get("refined_prompt"):
            parts.append(cue["refined_prompt"])
            # We assume refined_prompt already integrates context, style, and persona.
            # No need to append loose text fragments.
        else:
            # === 正規ルート: llm_context_analyzerの出力を直接使用 ===
            # visual_focusが最も重要 - 画像の主題を直接記述
            if cue.get("visual_focus"):
                vf = _sanitize_visual_focus_for_no_text(str(cue.get("visual_focus") or ""))
                if vf:
                    cue["visual_focus"] = vf
                    parts.append(f"Visual Focus: {vf}")
            
            # summaryはシーン説明
            if cue.get("summary"):
                scene = str(cue["summary"])
                if not (english_only_prompt and _contains_japanese(scene)):
                    parts.append(f"Scene: {scene}")
            
            # emotional_toneは雰囲気指定
            if cue.get("emotional_tone"):
                parts.append(f"Tone: {cue['emotional_tone']}")
            
            # role_tagに基づくヒント追加（refinerのrole_hintsを活用）
            role_tag = cue.get("role_tag", "").lower()
            if role_tag and role_tag in refiner_hints:
                hint = str(refiner_hints[role_tag] or "").strip()
                if hint and not (english_only_prompt and _contains_japanese(hint)):
                    parts.append(f"Role Guidance: {hint}")
            elif cue.get("role_tag"):
                parts.append(f"Role: {cue['role_tag']}")
            
            # section_typeも参考情報として
            if cue.get("section_type"):
                parts.append(f"Section Type: {cue['section_type']}")
            
            # 台本抜粋は文字混入（字幕化）の原因になりやすいのでデフォルトOFF
            if include_script_excerpt and cue.get("text"):
                parts.append(f"Script excerpt: {_truncate(cue['text'], 120)}")
            
            # チャンネルプリセットの追加情報
            if channel_preset:
                if channel_preset.prompt_suffix:
                    suffix = str(channel_preset.prompt_suffix)
                    if not (english_only_prompt and _contains_japanese(suffix)):
                        parts.append(suffix)
                if channel_preset.character_note:
                    note = str(channel_preset.character_note)
                    if not (english_only_prompt and _contains_japanese(note)):
                        parts.append(note)

        # Common technical guardrails (always apply)
        if cue.get("diversity_note"):
            dn = str(cue["diversity_note"])
            if not (english_only_prompt and _contains_japanese(dn)):
                parts.append(dn)
            
        summary_for_prompt = " \n".join(parts)
        
        # Determine if we should prepend summary (Subject-First) for CH01
        is_ch01 = (args.channel or detected_channel or "").upper() == "CH01"

        # Prefer per-cue model_key when present (e.g., image_source_mix assigns it).
        # Otherwise fall back to the run-level forced key (preset/env) so prompt shaping can match the model.
        cue_model_key = str(cue.get("image_model_key") or "").strip() or (forced_model_key or None)
        if _looks_like_fireworks_flux_schnell(cue_model_key):
            rp = str(cue.get("refined_prompt") or "").strip()
            # NOTE: cue-mode=single is explicitly a deterministic 1-cue flow.
            # Allow FLUX schnell to run without per-cue refined prompts in this mode
            # (operators can still provide refined_prompt manually if desired).
            if not rp and args.cue_mode != "single":
                raise SystemExit(
                    "❌ FLUX schnell requires per-cue refined_prompt (SSOT).\n"
                    f"- Fix: fill {out_dir / 'visual_cues_plan.json'} sections[*].refined_prompt (THINK/AGENT), then rerun.\n"
                    "- Do NOT rely on local heuristics for prompt diversity."
                )
        gen_conf = None
        try:
            gen_conf = channel_preset.config_model.image_generation if channel_preset and channel_preset.config_model else None
        except Exception:
            gen_conf = None
        personless_default = bool(getattr(gen_conf, "personless_default", False))
        main_character = ""
        if personless_default:
            main_character = "None (personless scene; do not draw humans)."
        elif cue.get("use_persona") is True:
            main_character = "Use recurring main character/persona (keep identical face/clothes/age)."
        cue["prompt"] = build_prompt_for_image_model(
            template_text,
            model_key=cue_model_key,
            prepend_summary=is_ch01,
            summary=summary_for_prompt,
            visual_focus=str(cue.get("visual_focus") or ""),
            main_character=main_character,
            style=args.style,
            seed=cue.get("seed") if str(cue.get("seed") or "").strip().isdigit() else args.seed,
            size=f"{size['width']}x{size['height']}",
            negative=args.negative,
        )
        if guide_path:
            cue["input_images"] = [str(guide_path)]

    # 4) Write image_cues.json
    cues_json_path = out_dir / "image_cues.json"
    save_json(cues_json_path, {
        "schema": "ytm.image_cues.v1",
        "generated_at": utc_now_iso(),
        "source_srt": {"path": str(srt_path), "sha1": sha1_file(srt_path)},
        "cue_mode": args.cue_mode,
        "fps": args.fps,
        "size": size,
        "crossfade": args.crossfade,
        "imgdur": args.imgdur,
        "cues": cues,
    })
    logging.info("Wrote %s", cues_json_path)

    # 5) Generate images
    image_generator = get_image_generator(args)
    if image_generator:
        # 429対策: LLM文脈分析後に待機してAPI負荷を分散
        wait_before_images = int(os.getenv("SRT2IMAGES_WAIT_BEFORE_IMAGES", "30"))
        if wait_before_images > 0:
            logging.info("429対策: 画像生成前に%d秒待機中...", wait_before_images)
            time.sleep(wait_before_images)
        
        logging.info("Generating images via_mode=%s (concurrency=%d)", args.nanobanana, args.concurrency)
        # assign filenames
        for i, cue in enumerate(cues, start=1):
            cue["image_path"] = str(out_dir / "images" / f"{i:04d}.png")
        
        try:
            image_generator.generate_batch(
                cues=cues,
                concurrency=args.concurrency,
                force=args.force,
                width=size["width"],
                height=size["height"],
            )
        except QuotaExhaustedError as e:
            # === 明示的な失敗処理 ===
            fail_marker = out_dir / "RUN_FAILED_QUOTA.txt"
            fail_message = (
                f"🚨 Gemini APIクォータ制限により中断\n"
                f"エラー: {e}\n"
                f"成功画像数: {e.successful_count}\n"
                f"失敗回数: {e.failed_count}\n"
                f"タイムスタンプ: {datetime.now().isoformat()}\n"
                f"SRT: {args.srt}\n"
                f"出力ディレクトリ: {out_dir}\n"
            )
            fail_marker.write_text(fail_message, encoding="utf-8")
            logging.error("🚨 Gemini APIクォータ制限により中断: %s", e)
            logging.error("成功画像数: %d, 失敗回数: %d", e.successful_count, e.failed_count)
            logging.error("詳細は %s を参照", fail_marker)
            sys.exit(1)
    else:
        logging.info("Skipping image generation (mode=none)")

    # 6) Engine branching
    if args.engine == "none":
        logging.info("Engine=none; finished generating cues and images.")
    elif args.engine == "capcut":
        logging.info("Building CapCut draft...")
        draft_dir = build_capcut_draft(
            out_dir=out_dir,
            cues=cues,
            fps=args.fps,
            crossfade=args.crossfade,
            size=size,
        )
        logging.info("CapCut draft prepared at: %s", draft_dir)
    elif args.engine == "remotion":
        logging.info("Setting up Remotion project and render scaffolding...")
        # Subtitles: align to image cues (to make image and text match)
        cue_subs = [
            {"start": c["start_sec"], "end": c["end_sec"], "text": c.get("text") or c.get("summary", "")}
            for c in cues
        ]
        setup_and_render_remotion(
            out_dir=out_dir,
            size=size,
            fps=args.fps,
            crossfade=args.crossfade,
            cues=cues,
            subtitles=cue_subs,
            fit=args.fit,
            margin_px=args.margin,
        )
        logging.info("Remotion project scaffolded in %s/remotion", out_dir)
    else:
        logging.error("Unknown engine: %s", args.engine)
        sys.exit(2)

    logging.info("Done.")
