from pathlib import Path
from typing import List, Dict, Any, Optional
import time
import wave
import struct
import io
import re
from .strict_structure import AudioSegment
from .voicevox_api import VoicevoxClient
from .mecab_tokenizer import tokenize_with_mecab
from .reading_structs import KanaPatch
from .synthesis import apply_kana_patches

def generate_silence(duration_sec: float, frame_rate: int = 24000) -> bytes:
    if duration_sec <= 0:
        return b""
    num_samples = int(duration_sec * frame_rate)
    # 16-bit PCM silence is just zeros
    return struct.pack('<' + ('h' * num_samples), *([0] * num_samples))

def strict_synthesis(
    segments: List[AudioSegment],
    output_wav: Path,
    engine: str,
    voice_config: Optional[Dict[str, Any]],
    voicevox_client: Optional[VoicevoxClient] = None,
    speaker_id: int = 0,
    target_indices: Optional[List[int]] = None,
    resume: bool = False,
    patches: Optional[Dict[int, List[KanaPatch]]] = None
) -> None:
    
    if engine != "voicevox":
        raise NotImplementedError("Strict synthesis currently only supports Voicevox")

    client = voicevox_client
    if not client:
        from .routing import load_routing_config
        cfg = load_routing_config()
        client = VoicevoxClient(engine_url=cfg.voicevox_url)
    
    # Prepare Output Wave
    FRAME_RATE = 24000
    SAMPLE_WIDTH = 2
    CHANNELS = 1
    
    all_frames = bytearray()
    
    # Chunk directory setup
    chunks_dir = output_wav.parent / "chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    base_stem = output_wav.stem
    
    print(f"[SYNTHESIS] Processing {len(segments)} segments...")
    
    # Extract config values
    speed = 1.0
    pitch = 0.0
    intonation = 1.0
    volume = 1.0
    pre_phoneme = 0.1
    post_phoneme = 0.1
    
    if voice_config:
        speed = voice_config.get("speed_scale", 1.0)
        pitch = voice_config.get("pitch_scale", 0.0)
        intonation = voice_config.get("intonation_scale", 1.0)
        volume = voice_config.get("volume_scale", 1.0)
        pre_phoneme = voice_config.get("pre_phoneme_length", 0.1)
        post_phoneme = voice_config.get("post_phoneme_length", 0.1)

    print(f"[SYNTHESIS] Params: Speed={speed}, Pitch={pitch}, Intonation={intonation}")

    for i, seg in enumerate(segments):
        chunk_path = chunks_dir / f"{base_stem}_part_{i:03d}.wav"
        
        # Check if we should skip regeneration
        skip_regen = False
        if target_indices is not None:
            if i not in target_indices:
                if chunk_path.exists():
                    skip_regen = True
                else:
                    print(f"[WARN] Chunk {chunk_path.name} missing, forcing regeneration.")
        elif resume and chunk_path.exists():
            skip_regen = True
        
        segment_frames = bytearray()

        # 1. Pre-pause
        if seg.pre_pause_sec > 0:
            segment_frames.extend(generate_silence(seg.pre_pause_sec, FRAME_RATE))
            
        # 2. Synthesis or Load
        if skip_regen:
            try:
                with wave.open(str(chunk_path), 'rb') as w:
                    frames = w.readframes(w.getnframes())
                    segment_frames.extend(frames)
                    # Update duration in segment object for SRT
                    seg.duration_sec = w.getnframes() / w.getframerate()
            except Exception as e:
                 print(f"[ERROR] Failed to load chunk {chunk_path}: {e}")
                 # Fallback to regen if load fails?
                 skip_regen = False
        
        if not skip_regen:
            text_to_speak = seg.reading if seg.reading else seg.text
            # TTSに渡す前に中点（・）を削除（固有名詞の場合のみ）
            if "・" in text_to_speak:
                tokens = tokenize_with_mecab(text_to_speak)
                result_parts = []
                for idx_t, token in enumerate(tokens):
                    surface = token.get("surface", "")
                    pos = token.get("pos", "")
                    subpos = token.get("subpos", "")
                    
                    if surface == "・":
                        prev_token = tokens[idx_t - 1] if idx_t > 0 else None
                        next_token = tokens[idx_t + 1] if idx_t < len(tokens) - 1 else None
                        
                        prev_is_proper = False
                        next_is_proper = False
                        
                        if prev_token:
                            prev_pos = prev_token.get("pos", "")
                            prev_subpos = prev_token.get("subpos", "")
                            prev_is_proper = (
                                "固有名詞" in prev_subpos or 
                                "人名" in prev_subpos or 
                                "地名" in prev_subpos or
                                (prev_pos == "名詞" and prev_subpos in ["固有名詞", "一般"] and 
                                 any(ord(c) >= 0x30A0 and ord(c) <= 0x30FF for c in prev_token.get("surface", "")))
                            )
                        
                        if next_token:
                            next_pos = next_token.get("pos", "")
                            next_subpos = next_token.get("subpos", "")
                            next_is_proper = (
                                "固有名詞" in next_subpos or 
                                "人名" in next_subpos or 
                                "地名" in next_subpos or
                                (next_pos == "名詞" and next_subpos in ["固有名詞", "一般"] and 
                                 any(ord(c) >= 0x30A0 and ord(c) <= 0x30FF for c in next_token.get("surface", "")))
                            )
                        
                        if prev_is_proper or next_is_proper:
                            continue
                    
                    result_parts.append(surface)
                text_to_speak = "".join(result_parts)

            try:
                query = client.audio_query(text_to_speak, speaker_id)
                query["speedScale"] = speed
                query["pitchScale"] = pitch
                query["intonationScale"] = intonation
                query["volumeScale"] = volume
                query["prePhonemeLength"] = pre_phoneme
                query["postPhonemeLength"] = post_phoneme

                # Layer 4: Apply Kana Patches if available
                if patches and i in patches:
                    seg_patches = patches[i]
                    if seg_patches:
                        print(f"  [PATCH] Applying {len(seg_patches)} kana patches to seg {i}")
                        apply_kana_patches(query.get("accent_phrases", []), seg_patches)

                wav_data = client.synthesis(query, speaker_id)
                
                # Save speech part to chunk file
                with wave.open(io.BytesIO(wav_data), 'rb') as w:
                    frames = w.readframes(w.getnframes())
                    segment_frames.extend(frames)
                    seg.duration_sec = w.getnframes() / w.getframerate()
                    
                    # Save pure speech chunk
                    with wave.open(str(chunk_path), 'wb') as wc:
                        wc.setnchannels(CHANNELS)
                        wc.setsampwidth(SAMPLE_WIDTH)
                        wc.setframerate(FRAME_RATE)
                        wc.writeframes(frames)
                    
            except Exception as e:
                print(f"[ERROR] Synthesis failed for seg {i}: {e}")
                raise RuntimeError(f"Synthesis failed at segment {i}") from e
            
        # 3. Post-pause
        if seg.post_pause_sec > 0:
            segment_frames.extend(generate_silence(seg.post_pause_sec, FRAME_RATE))
            
        all_frames.extend(segment_frames)
        
        # Progress
        if (i+1) % 10 == 0:
             status = "SKIP" if skip_regen else "GEN "
             print(f"  ... {i+1}/{len(segments)} {status}")

    # Write Final Combined File
    with wave.open(str(output_wav), 'wb') as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(FRAME_RATE)
        wf.writeframes(all_frames)
        
    print(f"[SYNTHESIS] Written {len(all_frames)} bytes to {output_wav}")


def generate_srt(segments: List[AudioSegment], output_srt: Path) -> None:
    """
    Generate SRT from segments with precise timing.
    """
    def format_time(total_seconds: float) -> str:
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        milliseconds = int((total_seconds * 1000) % 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    current_time = 0.0
    entries = []
    
    for i, seg in enumerate(segments):
        # Pre-pause
        current_time += seg.pre_pause_sec
        
        start_time = current_time
        duration = seg.duration_sec
        end_time = start_time + duration
        
        # Text for SRT (Display Text)
        text = seg.text
        
        entries.append(f"{i+1}\n{format_time(start_time)} --> {format_time(end_time)}\n{text}\n")
        
        # Advance time (Content + Post-pause)
        current_time = end_time + seg.post_pause_sec

    output_srt.write_text("\n".join(entries), encoding="utf-8")
    print(f"[SRT] Written to {output_srt}")
