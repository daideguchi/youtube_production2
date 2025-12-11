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
from .routing import load_routing_config, resolve_voicevox_speaker_id

# Need to load voice_config.json manually because routing.py doesn't handle it fully
def load_channel_voice_config(channel: str) -> Optional[Dict[str, Any]]:
    # Assume repo root relative path
    repo_root = Path(__file__).resolve().parents[2] # factory_commentary
    config_path = repo_root / "script_pipeline" / "audio" / "channels" / channel / "voice_config.json"
    
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
    segments = strict_segmentation(input_text)
    print(f"-> Generated {len(segments)} segments.")
    
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
        try:
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
            try:
                # strict_synthesis で使った accent_phrases はここでは持っていないため、
                # 再度 audio_query を取得して alignment する（prepass 時のみコスト許容）。
                query_for_log = vv_client.audio_query(s.reading or s.text, speaker_id) if prepass else None
                accent_phrases = query_for_log.get("accent_phrases") if query_for_log else None
                if accent_phrases:
                    aligned = align_moras_with_tokens(accent_phrases, ruby_tokens)
                    for rt, moras in aligned:
                        vv_token_map[rt.token_index] = "".join(moras)
            except Exception:
                vv_token_map = {}

            tok_entries = []
            for rt in ruby_tokens:
                tok_entries.append(
                    {
                        "token_index": rt.token_index,
                        "surface": rt.surface,
                        "pos": tokens[rt.token_index].get("pos", ""),
                        "mecab_kana": rt.reading_hira,
                        "voicevox_kana": vv_kana,
                        "voicevox_kana_norm": vv_token_map.get(rt.token_index, ""),
                    }
                )
            seg_entry["tokens"] = tok_entries
        except Exception:
            pass

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
