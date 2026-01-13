from pathlib import Path
from typing import Dict, Any, Optional, List
import json
import time

from .strict_structure import AudioSegment, PipelineResult
from .strict_segmenter import strict_segmentation
from .arbiter import resolve_readings_strict
from .strict_synthesizer import strict_synthesis, generate_srt
from .voicevox_api import VoicevoxClient
from .mecab_tokenizer import tokenize_with_mecab
from .reading_structs import RubyToken, align_moras_with_tokens
from .routing import load_default_voice_config, load_routing_config, resolve_voicevox_speaker_id

from .risk_utils import is_trivial_diff
from factory_common.paths import audio_pkg_root, script_pkg_root, video_root
from factory_common.text_sanitizer import strip_meta_from_script

def run_strict_pipeline(
    channel: str,
    video_no: str,
    input_text: str,
    output_wav: Path,
    output_log: Path,
    engine: str,
    voicepeak_config: Optional[Dict[str, Any]],
    artifact_root: Path,
    target_indices: Optional[List[int]] = None,
    resume: bool = False,
    prepass: bool = False,
    skip_tts_reading: bool = False,
) -> None:
    
    print(f"=== STRICT PIPELINE START ===")
    print(f"Channel: {channel}, Video: {video_no}, Engine: {engine}")
    
    cfg = load_routing_config()
    
    # Load detailed voice config (speed, pitch, etc.)
    voice_config = load_default_voice_config(channel)
    if voice_config:
        print(f"[CONFIG] Loaded voice config: {json.dumps(voice_config, ensure_ascii=False)}")
    else:
        print(f"[CONFIG] No specific voice config found. Using defaults.")

    # 0. Setup Engine Client
    vv_client = None
    speaker_id = 0
    if engine == "voicevox":
        vv_client = VoicevoxClient(engine_url=cfg.voicevox_url)
        # Use ID from voice_config if available, otherwise fallback to routing logic
        if voice_config and "voicevox_speaker_id" in voice_config:
            speaker_id = int(voice_config["voicevox_speaker_id"])
            print(f"[SETUP] Using Speaker ID from config: {speaker_id}")
        else:
            speaker_id = resolve_voicevox_speaker_id(channel, cfg)
            print(f"[SETUP] Using Speaker ID from routing: {speaker_id}")
    
    # 1. Segmentation
    print("[STEP 1] Segmentation & Pause Planning")
    sanitized = strip_meta_from_script(input_text)
    if sanitized.removed_counts:
        print(f"[SANITIZE] Removed meta tokens from input: {sanitized.removed_counts}")
        input_text = sanitized.text
    segments = strict_segmentation(input_text)
    print(f"-> Generated {len(segments)} segments.")
    
    # ローカル位置オーバーライド（contextual LLM の出力）を先読みしておく
    local_overrides: Dict[int, Dict[int, str]] = {}
    if channel and video_no:
        video_dir = video_root(channel, video_no)
        local_tok_path = (
            video_dir / "audio_prep" / "local_token_overrides.json"
        )
        if local_tok_path.exists():
            try:
                data = json.loads(local_tok_path.read_text(encoding="utf-8"))
                for item in data:
                    sid = int(item.get("section_id", -1))
                    tidx = int(item.get("token_index", -1))
                    reading = item.get("reading") or ""
                    if sid < 0 or tidx < 0 or not reading:
                        continue
                    local_overrides.setdefault(sid, {})[tidx] = reading
                print(f"[PREPASS] Loaded local_token_overrides.json ({len(local_overrides)} sections)")
            except Exception as e:
                print(f"[WARN] Failed to load local_token_overrides.json: {e}")
        # ローカル辞書（surface単位）もログ用に読み込む
        local_dict_path = video_dir / "audio_prep" / "local_reading_dict.json"
        local_dict: Dict[str, str] = {}
        if local_dict_path.exists():
            try:
                local_dict = json.loads(local_dict_path.read_text(encoding="utf-8"))
                print(f"[PREPASS] Loaded local_reading_dict.json ({len(local_dict)} entries)")
            except Exception as e:
                print(f"[WARN] Failed to load local_reading_dict.json: {e}")
    else:
        local_dict = {}
    # グローバル辞書もログ用に読み込む
    global_dict: Dict[str, str] = {}
    global_path = audio_pkg_root() / "configs" / "learning_dict.json"
    if global_path.exists():
        try:
            data = json.loads(global_path.read_text(encoding="utf-8"))
            global_dict = {k: v for k, v in data.items()}
        except Exception as e:
            print(f"[WARN] Failed to load learning_dict.json: {e}")

    # Partial Update Logic
    active_segments: List[AudioSegment] = []
    active_indices: Optional[List[int]] = None
    if target_indices is not None:
        # SSOT: partial regeneration is not part of the standard manual audit flow.
        # If used, keep it strictly safe and deterministic.
        if engine == "voicevox" and not skip_tts_reading:
            raise RuntimeError(
                "[PARTIAL] --indices is only supported with SKIP_TTS_READING=1 for VOICEVOX strict mode. "
                "Run a full regeneration instead (SSOT: OPS_TTS_MANUAL_READING_AUDIT)."
            )

        # Sanitize indices (preserve order, drop out-of-range/duplicates)
        seen: set[int] = set()
        sanitized_indices: List[int] = []
        for raw in target_indices:
            try:
                idx = int(raw)
            except Exception:
                continue
            if idx < 0 or idx >= len(segments):
                continue
            if idx in seen:
                continue
            seen.add(idx)
            sanitized_indices.append(idx)
        if not sanitized_indices:
            raise RuntimeError("[PARTIAL] --indices provided but no valid indices remain after sanitization.")

        active_indices = sanitized_indices
        print(f"[PARTIAL] Targeting indices: {active_indices}")

        if not output_log.exists():
            raise RuntimeError(
                f"[PARTIAL] Previous log not found: {output_log}. "
                "Partial regeneration requires an existing log.json from a successful full run."
            )

        def _write_report(name: str, payload: Dict[str, object]) -> Path:
            out_dir = output_log.parent
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / name
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return out_path

        # Load previous log to restore existing verdicts/readings (and validate)
        try:
            prev_data = json.loads(output_log.read_text(encoding="utf-8"))
        except Exception as e:
            raise RuntimeError(f"[PARTIAL] Failed to load previous log for partial update: {e}") from e

        prev_engine = str(prev_data.get("engine") or "").strip()
        if prev_engine and prev_engine != engine:
            report = _write_report(
                "partial_update_blocked__engine_mismatch.json",
                {
                    "channel": channel,
                    "video": video_no,
                    "engine_prev": prev_engine,
                    "engine_now": engine,
                    "generated_at": time.time(),
                },
            )
            raise RuntimeError(f"[PARTIAL] Previous log engine mismatch; refusing partial update. Report: {report}")

        prev_segs = prev_data.get("segments", [])
        if not isinstance(prev_segs, list):
            raise RuntimeError("[PARTIAL] Invalid previous log format: segments is not a list.")
        if len(prev_segs) != len(segments):
            report = _write_report(
                "partial_update_blocked__segment_count_mismatch.json",
                {
                    "channel": channel,
                    "video": video_no,
                    "generated_at": time.time(),
                    "prev_count": len(prev_segs),
                    "current_count": len(segments),
                },
            )
            raise RuntimeError(f"[PARTIAL] Segment count mismatch; run full regeneration. Report: {report}")

        # Enforce exact text match for ALL segments (chunk index safety).
        text_mismatches: List[Dict[str, object]] = []
        for i, seg in enumerate(segments):
            p = prev_segs[i] if i < len(prev_segs) else {}
            prev_text = p.get("text")
            if prev_text != seg.text:
                text_mismatches.append({"index": i, "prev_text": prev_text, "current_text": seg.text})
        if text_mismatches:
            report = _write_report(
                "partial_update_blocked__text_mismatch.json",
                {
                    "channel": channel,
                    "video": video_no,
                    "generated_at": time.time(),
                    "count": len(text_mismatches),
                    "mismatches": text_mismatches,
                },
            )
            raise RuntimeError(f"[PARTIAL] Text mismatch detected; refusing partial update. Report: {report}")

        # Require that the previous run was a safe manual path (VOICEVOX: dict_only_skip_llm).
        if engine == "voicevox":
            bad_verdicts: List[Dict[str, object]] = []
            for i, p in enumerate(prev_segs):
                verdict = p.get("verdict")
                if verdict != "dict_only_skip_llm":
                    bad_verdicts.append({"index": i, "verdict": verdict})
            if bad_verdicts:
                report = _write_report(
                    "partial_update_blocked__verdict_not_manual.json",
                    {
                        "channel": channel,
                        "video": video_no,
                        "generated_at": time.time(),
                        "count": len(bad_verdicts),
                        "bad_verdicts": bad_verdicts[:50],
                        "note": "VOICEVOX partial update requires a prior SKIP_TTS_READING=1 successful run.",
                    },
                )
                raise RuntimeError(f"[PARTIAL] Previous run is not manual skip_llm; refusing partial update. Report: {report}")

        # Ensure all non-target chunks exist so we never regenerate un-audited segments.
        chunks_dir = output_wav.parent / "chunks"
        base_stem = output_wav.stem
        missing_chunks: List[Dict[str, object]] = []
        for i in range(len(segments)):
            if i in seen:
                continue
            chunk_path = chunks_dir / f"{base_stem}_part_{i:03d}.wav"
            if not chunk_path.exists():
                missing_chunks.append({"index": i, "path": str(chunk_path)})
        if missing_chunks:
            report = _write_report(
                "partial_update_blocked__missing_chunks.json",
                {
                    "channel": channel,
                    "video": video_no,
                    "generated_at": time.time(),
                    "count": len(missing_chunks),
                    "missing": missing_chunks,
                },
            )
            raise RuntimeError(
                f"[PARTIAL] Missing non-target chunks; refusing partial update to avoid unintended regen. Report: {report}"
            )

        # Global safety: non-target segments must already be mismatch-free (guards against legacy logs).
        if engine == "voicevox":
            mismatches: List[Dict[str, object]] = []
            for i in range(len(segments)):
                if i in seen:
                    continue
                p = prev_segs[i] if i < len(prev_segs) else {}
                mecab_kana = str(p.get("mecab") or "")
                vv_kana = str(p.get("voicevox") or "")
                if not mecab_kana or not vv_kana:
                    mismatches.append(
                        {
                            "index": i,
                            "text": p.get("text") or segments[i].text,
                            "reading": p.get("reading"),
                            "mecab_kana": mecab_kana,
                            "voicevox_kana": vv_kana,
                            "reason": "missing_mecab_or_voicevox",
                        }
                    )
                    continue
                if not is_trivial_diff(mecab_kana, vv_kana):
                    mismatches.append(
                        {
                            "index": i,
                            "text": p.get("text") or segments[i].text,
                            "reading": p.get("reading"),
                            "mecab_kana": mecab_kana,
                            "voicevox_kana": vv_kana,
                        }
                    )
            if mismatches:
                report = _write_report(
                    "reading_mismatches__resume.json",
                    {
                        "channel": channel,
                        "video": video_no,
                        "tag": "resume",
                        "generated_at": time.time(),
                        "count": len(mismatches),
                        "mismatches": mismatches,
                    },
                )
                raise RuntimeError(
                    "[PARTIAL] Existing reading mismatches detected in non-target segments (fail-fast). "
                    f"Run a full regeneration. Report: {report}"
                )

        # Restore previous state (safe: texts already matched).
        for i, seg in enumerate(segments):
            p = prev_segs[i]
            seg.reading = p.get("reading")
            seg.arbiter_verdict = p.get("verdict")
            seg.mecab_reading = p.get("mecab")
            seg.voicevox_reading = p.get("voicevox")
            try:
                seg.duration_sec = float(p.get("duration") or 0.0)
            except Exception:
                seg.duration_sec = 0.0

        # Filter segments to process (reading resolution only for targets).
        for i in active_indices:
            active_segments.append(segments[i])
    else:
        active_segments = segments

    # 2. Reading Resolution (Arbiter)
    print(f"[STEP 2] Reading Resolution (AI Arbiter) - Processing {len(active_segments)} segments")
    arbiter_error: Optional[Exception] = None
    patches_by_block: Dict[int, List[Any]] = {}
    try:
        patches_by_block = resolve_readings_strict(
            segments=active_segments,
            engine=engine,
            voicevox_client=vv_client,
            speaker_id=speaker_id,
            channel=channel,
            video=video_no,
            skip_tts_reading=skip_tts_reading,
            segment_indices=active_indices,
        )
    except Exception as exc:
        # SSOT: even when fail-fast triggers (mismatch), keep a detailed prepass log for diagnosis.
        arbiter_error = exc
        for s in segments:
            if not s.arbiter_verdict or s.arbiter_verdict == "pending_auditor":
                s.arbiter_verdict = "arbiter_failed"
        print(f"[WARN] Arbiter failed (log will still be written): {exc}")
    
    if not prepass:
        if arbiter_error is not None:
            # Skip synthesis/SRT; we will raise after writing the log.
            print("[STOP] Arbiter failed; skipping synthesis/SRT.")
        else:
        # 3. Synthesis
            print("[STEP 3] Audio Synthesis")
            # Pass target_indices to synthesizer so it knows which chunks to regenerate vs reuse
            strict_synthesis(
                segments=segments, # Pass ALL segments
                output_wav=output_wav,
                engine=engine,
                voice_config=voice_config,
                voicevox_client=vv_client,
                speaker_id=speaker_id,
                target_indices=target_indices, # New arg
                resume=resume,
                patches=patches_by_block,
                channel=channel,
                voicepeak_overrides=voicepeak_config,
            )
        
        # 4. SRT Generation
            srt_path = output_wav.with_suffix(".srt")
            generate_srt(
                segments,
                srt_path,
                channel=channel,
                video_no=video_no,
                engine=engine,
                voice_config=voice_config,
                voicevox_client=vv_client,
                speaker_id=speaker_id,
            )
    else:
        print("[STEP 3] Prepass mode: skip synthesis/SRT. Log only.")
    
    # 5. Log Output
    # prepass用にトークン情報をできるだけ詳細に残す
    log_segments = []
    for idx, s in enumerate(segments):
        seg_entry: Dict[str, Any] = {
            "section_id": idx,
            "text": s.text,
            "reading": s.reading,
            "pre": s.pre_pause_sec,
            "post": s.post_pause_sec,
            "heading": s.is_heading,
            "duration": s.duration_sec,
            "verdict": s.arbiter_verdict,
            "mecab": s.mecab_reading,
            "voicevox": s.voicevox_reading,
        }
        # token-level info (best-effort)
        tokens = tokenize_with_mecab(s.text)
        ruby_tokens: List[RubyToken] = []
        for t in tokens:
            ruby_tokens.append(
                RubyToken(
                    surface=t.get("surface", ""),
                    reading_hira=t.get("reading_mecab") or t.get("surface") or "",
                    token_index=int(t.get("index", len(ruby_tokens))),
                    line_id=idx,
                )
            )
        vv_kana = s.voicevox_reading or ""
        vv_token_map: Dict[int, str] = {}
        if prepass and vv_client is not None:
            # prepass時のみ、ログ精度を上げるため再度 audio_query で accent_phrases を取得し alignment を試みる
            try:
                query_for_log = vv_client.audio_query(s.reading or s.text, speaker_id)
                accent_phrases = query_for_log.get("accent_phrases") if query_for_log else None
                if accent_phrases:
                    aligned = align_moras_with_tokens(accent_phrases, ruby_tokens)
                    for rt, moras in aligned:
                        vv_token_map[rt.token_index] = "".join(moras)
            except Exception:
                vv_token_map = {}

        tok_entries = []
        for rt in ruby_tokens:
            surface = rt.surface
            before_vv = vv_token_map.get(rt.token_index, vv_kana)
            final_reading = None
            final_source = None
            # 位置オーバーライド優先
            if idx in local_overrides and rt.token_index in local_overrides[idx]:
                final_reading = local_overrides[idx][rt.token_index]
                final_source = "local_token"
            elif surface in local_dict:
                final_reading = local_dict[surface]
                final_source = "local_dict"
            elif surface in global_dict:
                final_reading = global_dict[surface]
                final_source = "global_dict"

            tok_entries.append(
                {
                    "token_index": rt.token_index,
                    "surface": surface,
                    "pos": tokens[rt.token_index].get("pos", ""),
                    "mecab_kana": rt.reading_hira,
                    "voicevox_kana": vv_kana,
                    "voicevox_kana_norm": before_vv,
                    "final_reading": final_reading,
                    "final_reading_source": final_source,
                }
            )
        seg_entry["tokens"] = tok_entries

        log_segments.append(seg_entry)

    log_data = {
        "channel": channel,
        "video": video_no,
        "engine": engine,
        "timestamp": time.time(),
        "segments": log_segments,
    }
    output_log.write_text(json.dumps(log_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"=== PIPELINE FINISHED ===")
    if arbiter_error is not None:
        raise RuntimeError(str(arbiter_error)) from arbiter_error
