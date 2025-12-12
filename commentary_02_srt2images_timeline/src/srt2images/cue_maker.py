from __future__ import annotations
from typing import List, Dict, Optional
import math
import logging
import os


def _truncate_summary(text: str, limit: int = 150) -> str:
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1].rstrip() + "…"


def make_cues(segments: List[Dict], target_imgdur: float = 20.0, fps: int = 30, channel_id: Optional[str] = None) -> List[Dict]:
    """
    LLM文脈理解による自然なセクション分割
    
    従来の機械的20秒分割を廃止し、OpenRouter LLMがストーリーの文脈を理解して
    自然なセクション境界を決定する革新的システム
    
    Returns cues: [{start_sec, end_sec, duration_sec, text, summary, context_reason}]
    """
    cues: List[Dict] = []
    if not segments:
        return cues

    # IMPORTANT: Mechanical splitting is forbidden.
    # If you want to stop API LLM usage, use THINK MODE failover instead of degrading quality.
    if os.getenv("SRT2IMAGES_DISABLE_CONTEXT_LLM") == "1":
        raise RuntimeError(
            "SRT2IMAGES_DISABLE_CONTEXT_LLM=1 is set, but mechanical splitting fallback is forbidden. "
            "Unset this env var and rerun (or use THINK MODE failover via the agent queue)."
        )

    # 🚨 重要：LLM文脈理解システムを使用
    # 機械的20秒分割は廃止され、ストーリーベースの自然な分割を実行
    logging.info("🧠 LLM文脈理解システム使用: 自然なセクション分割を実行")
    return _make_cues_with_llm_context(segments, target_imgdur, fps, channel_id=channel_id)


def _make_cues_with_llm_context(segments: List[Dict], target_imgdur: float, fps: int, channel_id: Optional[str] = None) -> List[Dict]:
    """LLM文脈理解による自然なセクション分割"""
    from .llm_context_analyzer import LLMContextAnalyzer
    from config.channel_resolver import ChannelPresetResolver
    
    try:
        # Load base_seconds from channel config (SSOT)
        base_seconds = 30.0
        
        # CH01 override: force faster pace (12s) unless manually overridden
        if (channel_id or "").upper() == "CH01":
            base_seconds = 12.0
            logging.info("⚙️ CH01 detected: forcing base_seconds=%.1f for rapid pacing", base_seconds)
        elif channel_id:
            resolver = ChannelPresetResolver()
            preset = resolver.resolve(channel_id)
            if preset and preset.config_model and preset.config_model.image_generation:
                cfg_period = preset.config_model.image_generation.base_period
                if cfg_period > 0:
                    base_seconds = float(cfg_period)
                    logging.info("⚙️ Configured base_seconds for %s: %.1f", channel_id, base_seconds)

        # 総時間から適切なセクション数を計算
        total_duration = segments[-1]["end"] - segments[0]["start"]
        target_sections = max(10, math.ceil(total_duration / base_seconds))

        # Allow environment override for desired section count (e.g. 30-40 images requirement)
        env_override = os.getenv("SRT2IMAGES_TARGET_SECTIONS")
        if env_override:
            try:
                override_val = int(env_override)
                if override_val >= 5:
                    target_sections = override_val
            except ValueError:
                logging.warning("Invalid SRT2IMAGES_TARGET_SECTIONS=%s (must be int)", env_override)
        
        logging.info("📊 動画時間: %.1f分, 目標セクション数: %d", total_duration/60, target_sections)
        
        # LLM分析実行
        analyzer = LLMContextAnalyzer(channel_id=channel_id)
        section_breaks = analyzer.analyze_story_sections(segments, target_sections)
        
        cues = []
        for i, section in enumerate(section_breaks, start=1):
            slice_segments = segments[section.start_segment: section.end_segment + 1]
            if not slice_segments:
                continue
            cue = _create_context_cue(
                slice_segments,
                i,
                fps,
                context_reason=section.reason,
                emotional_tone=section.emotional_tone,
                summary_override=section.summary,
                visual_focus=section.visual_focus,
                section_type=section.section_type,
                persona_needed=section.persona_needed,
                role_tag=section.role_tag
            )
            cues.append(cue)

        # 🚨 CRITICAL: 連続性保証処理
        # 各セクション間に隙間がないように調整（重複なし・連続配置）
        # 注: CapCut APIは同一トラック上でのセグメント重複を禁止

        for i in range(len(cues)):
            if i < len(cues) - 1:
                # 次のセクションの開始点
                next_start = cues[i+1]['start_sec']

                # 現在のセクションの終点を次の開始点に合わせる（隙間なし、重複なし）
                cues[i]['end_sec'] = next_start
                cues[i]['duration_sec'] = cues[i]['end_sec'] - cues[i]['start_sec']

            # フレーム数を再計算
            cues[i]['start_frame'] = int(round(cues[i]['start_sec'] * fps))
            cues[i]['end_frame'] = int(round(cues[i]['end_sec'] * fps))
            cues[i]['duration_frames'] = cues[i]['end_frame'] - cues[i]['start_frame']

        logging.info("✅ LLM文脈分割完了: %d セクション生成（連続性保証・隙間ゼロ）", len(cues))
        return cues
        
    except Exception as e:
        logging.error("❌ LLM分析失敗: %s", e)
        raise


def _create_context_cue(
    segments: List[Dict],
    index: int,
    fps: int,
    context_reason: str = "",
    emotional_tone: str = "",
    summary_override: str | None = None,
    visual_focus: str | None = None,
    section_type: str | None = None,
    persona_needed: bool = False,
    role_tag: str | None = None,
) -> Dict:
    """文脈を考慮したcue作成"""
    if not segments:
        return {}
    
    start_sec = segments[0]["start"]
    end_sec = segments[-1]["end"]
    duration_sec = end_sec - start_sec
    
    # 全テキストを結合
    all_texts = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if text:
            all_texts.append(text)
    
    combined_text = " ".join(all_texts)
    summary = (summary_override or "").strip() or _truncate_summary(combined_text)

    cue = {
        "index": index,
        "start_sec": round(start_sec, 3),
        "end_sec": round(end_sec, 3),
        "duration_sec": round(duration_sec, 3),
        "text": combined_text,
        "summary": summary,
        "context_reason": context_reason,  # LLMが決定した分割理由
        "emotional_tone": emotional_tone,  # 感情的トーン
        "start_frame": int(round(start_sec * fps)),
        "end_frame": int(round(end_sec * fps)),
        "duration_frames": max(1, int(round(end_sec * fps)) - int(round(start_sec * fps)))
    }

    if visual_focus:
        cue["visual_focus"] = visual_focus.strip()
    if section_type:
        cue["section_type"] = section_type
    if role_tag:
        cue["role_tag"] = role_tag
    # use_persona: 物語/対話などキャラ一貫が必要な場合のみオン
    cue["use_persona"] = bool(persona_needed or (section_type in ("story", "dialogue")))

    return cue

