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
import json
from .routing import load_routing_config, resolve_voicevox_speaker_id

from factory_common.paths import audio_pkg_root, script_pkg_root, video_root
from factory_common.text_sanitizer import strip_meta_from_script

# Need to load voice_config.json manually because routing.py doesn't handle it fully
def load_channel_voice_config(channel: str) -> Optional[Dict[str, Any]]:
    config_path = script_pkg_root() / "audio" / "channels" / channel / "voice_config.json"
    
    if not config_path.exists():
        print(f"[WARN] voice_config.json not found at {config_path}")
        return None
        
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
        # Extract default voice config
        key = data.get("default_voice_key")
        if key and "voices" in data and key in data["voices"]:
            return data["voices"][key]
        return None
    except Exception as e:
        print(f"[ERROR] Failed to load voice_config.json: {e}")
        return None

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
    voice_config = load_channel_voice_config(channel)
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
    active_segments = []
    if target_indices:
        print(f"[PARTIAL] Targeting indices: {target_indices}")
        # Load previous log to restore existing verdicts/readings
        if output_log.exists():
            try:
                prev_data = json.loads(output_log.read_text(encoding="utf-8"))
                prev_segs = prev_data.get("segments", [])
                
                # Restore previous state
                for i, seg in enumerate(segments):
                    if i < len(prev_segs):
                        p = prev_segs[i]
                        # Only restore if text matches (safety check)
                        if p.get("text") == seg.text:
                            seg.reading = p.get("reading")
                            seg.arbiter_verdict = p.get("verdict")
                            seg.mecab_reading = p.get("mecab")
                            seg.voicevox_reading = p.get("voicevox")
                            seg.duration_sec = p.get("duration", 0.0)
            except Exception as e:
                print(f"[WARN] Failed to load previous log for partial update: {e}")
        
        # Filter segments to process
        for i in target_indices:
            if 0 <= i < len(segments):
                active_segments.append(segments[i])
    else:
        active_segments = segments

    # 2. Reading Resolution (Arbiter)
    print(f"[STEP 2] Reading Resolution (AI Arbiter) - Processing {len(active_segments)} segments")
    patches_by_block = resolve_readings_strict(
        segments=active_segments,
        engine=engine,
        voicevox_client=vv_client,
        speaker_id=speaker_id,
        channel=channel,
        video=video_no,
        skip_tts_reading=skip_tts_reading,
    )
    
    if not prepass:
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
        generate_srt(segments, srt_path)
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
        if prepass:
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
