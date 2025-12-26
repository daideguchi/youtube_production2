import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from config.channel_resolver import (
    ChannelPresetResolver,
    infer_channel_id_from_path,
)
from srt2images.srt_parser import parse_srt
from srt2images.cue_maker import make_cues
from srt2images.cues_plan import (
    is_think_or_agent_mode,
    make_cues_from_sections,
    plan_sections_heuristic,
    plan_sections_via_router,
)
from srt2images.llm_context_analyzer import LLMContextAnalyzer
from srt2images.prompt_builder import build_prompt_from_template
from srt2images.llm_prompt_refiner import PromptRefiner
from srt2images.role_asset_router import RoleAssetRouter
from srt2images.generators import get_image_generator
from srt2images.engines.remotion_engine import setup_and_render_remotion
from srt2images.engines.capcut_engine import build_capcut_draft
from srt2images.orchestration.utils import ensure_out_dirs, setup_logging, save_json, parse_size
from srt2images.nanobanana_client import QuotaExhaustedError
from srt2images.visual_bible import VisualBibleGenerator
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
    resolver = ChannelPresetResolver()
    parser_defaults = {
        "prompt_template": args.prompt_template,
        "style": args.style,
    }
    detected_channel = args.channel or infer_channel_id_from_path(args.srt)
    channel_preset = resolver.resolve(detected_channel)
    if channel_preset:
        args.channel = detected_channel
        if channel_preset.prompt_template and (
            args.prompt_template == parser_defaults["prompt_template"]
            or not args.prompt_template
        ):
            resolved_template = channel_preset.resolved_prompt_template()
            if resolved_template:
                logging.info(
                    "Applying channel preset prompt template (%s)",
                    resolved_template,
                )
                args.prompt_template = resolved_template
        if channel_preset.style and (
            args.style == parser_defaults["style"] or not args.style
        ):
            logging.info("Applying channel preset style (%s)", channel_preset.style)
            args.style = channel_preset.style
    else:
        args.channel = detected_channel

    channel_upper = (args.channel or detected_channel or "").upper() if (args.channel or detected_channel) else ""
    forced_model_key = None
    try:
        if channel_preset and channel_preset.config_model and getattr(channel_preset.config_model, "image_generation", None):
            mk = getattr(channel_preset.config_model.image_generation, "model_key", None)
            if isinstance(mk, str) and mk.strip():
                forced_model_key = mk.strip()
    except Exception:
        forced_model_key = None

    if forced_model_key:
        os.environ["IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN"] = forced_model_key
    else:
        os.environ.pop("IMAGE_CLIENT_FORCE_MODEL_KEY_VISUAL_IMAGE_GEN", None)

    logging.info(
        "[image_gen] channel=%s task=visual_image_gen model_key=%s",
        channel_upper or "unknown",
        forced_model_key or "(tier default)",
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
    # CH12: slower pacing (~25s per image) is required; prefer deterministic cues_plan.
    if channel_upper == "CH12":
        use_cues_plan = True
        # NOTE: plan implementation is controlled via SRT2IMAGES_CUES_PLAN_IMPL (default: router in api mode).

    # 1.5) Generate Visual Bible (Before cues)
    persona_text = ""
    visual_bible_data = None

    # CH02 is personless by default; do not generate/use Visual Bible persona unless explicitly needed.
    if channel_upper == "CH02":
        try:
            (out_dir / "persona_mode.txt").write_text("off\n", encoding="utf-8")
        except Exception:
            pass

    if args.cue_mode != "per_segment" and not use_cues_plan and channel_upper != "CH02":
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
            # LLM failover-to-think may raise SystemExit to stop the process for queued tasks.
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
                    from srt2images.cues_plan import PlannedSection as _PlannedSection

                    planned_sections = [
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
                    logging.info("Loaded %d sections from %s", len(planned_sections), plan_path)
                except Exception as e:
                    raise SystemExit(
                        f"❌ visual_cues_plan.json invalid: {e}\n"
                        f"- path: {plan_path}\n"
                        f"- fix the file, or set SRT2IMAGES_FORCE_CUES_PLAN=1 to regenerate."
                    )

            if planned_sections is None:
                try:
                    plan_impl = (os.getenv("SRT2IMAGES_CUES_PLAN_IMPL") or "").strip().lower()
                    use_heuristic = False
                    if plan_impl in {"heuristic", "local"}:
                        use_heuristic = True
                    elif is_think_or_agent_mode() and plan_impl not in {"router", "llm", "api"}:
                        # THINK/AGENT mode default: avoid queueing LLM tasks; plan locally.
                        use_heuristic = True

                    if use_heuristic:
                        planned_sections = plan_sections_heuristic(
                            segments=segments,
                            base_seconds=base_seconds,
                        )
                        llm_task = {
                            "task": "visual_image_cues_plan",
                            "note": "heuristic (no-LLM, think-mode default)",
                        }
                    else:
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
    refiner_hints = PromptRefiner().role_hints if hasattr(PromptRefiner, 'role_hints') else {}

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
                parts.append(f"Visual Focus: {cue['visual_focus']}")
            
            # summaryはシーン説明
            if cue.get("summary"):
                parts.append(f"Scene: {cue['summary']}")
            
            # emotional_toneは雰囲気指定
            if cue.get("emotional_tone"):
                parts.append(f"Tone: {cue['emotional_tone']}")
            
            # role_tagに基づくヒント追加（refinerのrole_hintsを活用）
            role_tag = cue.get("role_tag", "").lower()
            if role_tag and role_tag in refiner_hints:
                parts.append(f"Role Guidance: {refiner_hints[role_tag]}")
            elif cue.get("role_tag"):
                parts.append(f"Role: {cue['role_tag']}")
            
            # section_typeも参考情報として
            if cue.get("section_type"):
                parts.append(f"Section Type: {cue['section_type']}")
            
            # 台本抜粋は補助情報として短く
            if cue.get("text"):
                parts.append(f"Script excerpt: {_truncate(cue['text'], 120)}")
            
            # チャンネルプリセットの追加情報
            if channel_preset:
                if channel_preset.prompt_suffix:
                    parts.append(channel_preset.prompt_suffix)
                if channel_preset.character_note:
                    parts.append(channel_preset.character_note)

        # Common technical guardrails (always apply)
        if cue.get("diversity_note"):
            parts.append(cue["diversity_note"])
            
        summary_for_prompt = " \n".join(parts)
        
        # Determine if we should prepend summary (Subject-First) for CH01
        is_ch01 = (args.channel or detected_channel or "").upper() == "CH01"

        cue["prompt"] = build_prompt_from_template(
            template_text,
            prepend_summary=is_ch01,
            summary=summary_for_prompt,
            style=args.style,
            seed=args.seed,
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
