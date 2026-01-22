from pathlib import Path
from typing import List, Dict, Any, Optional
import time
import wave
import struct
import io
import os
from .strict_structure import AudioSegment
from .voicevox_api import VoicevoxClient
from .voicepeak_cli import synthesize_chunk
from .reading_structs import KanaPatch
from .synthesis import apply_kana_patches
from .text_normalizer import normalize_text_for_tts


def generate_silence(duration_sec: float, frame_rate: int, sample_width: int, channels: int) -> bytes:
    if duration_sec <= 0:
        return b""
    num_frames = int(duration_sec * frame_rate)
    # PCM silence is just zeros (supports any sample width / channel count).
    return b"\x00" * (num_frames * int(sample_width) * int(channels))

def strict_synthesis(
    segments: List[AudioSegment],
    output_wav: Path,
    engine: str,
    voice_config: Optional[Dict[str, Any]],
    voicevox_client: Optional[VoicevoxClient] = None,
    speaker_id: int = 0,
    target_indices: Optional[List[int]] = None,
    resume: bool = False,
    patches: Optional[Dict[int, List[KanaPatch]]] = None,
    channel: Optional[str] = None,
    voicepeak_overrides: Optional[Dict[str, Any]] = None,
) -> None:
    
    if engine not in ("voicevox", "voicepeak"):
        raise NotImplementedError(f"Strict synthesis does not support engine={engine!r}")

    if engine == "voicepeak":
        from .routing import load_routing_config, voicepeak_defaults

        cfg = load_routing_config()
        defaults = voicepeak_defaults(channel or "", cfg)
        engine_opts = (voice_config or {}).get("engine_options") if isinstance(voice_config, dict) else {}
        engine_opts = engine_opts if isinstance(engine_opts, dict) else {}
        overrides = voicepeak_overrides if isinstance(voicepeak_overrides, dict) else {}

        binary_path = str(engine_opts.get("binary_path") or defaults["binary_path"])
        narrator = str(overrides.get("narrator") or engine_opts.get("narrator") or defaults["narrator"])
        speed = overrides.get("speed") if overrides.get("speed") is not None else engine_opts.get("speed", defaults["speed"])
        pitch = overrides.get("pitch") if overrides.get("pitch") is not None else engine_opts.get("pitch", defaults["pitch"])
        emotion = overrides.get("emotion") if overrides.get("emotion") is not None else engine_opts.get("emotion", defaults["emotion"])

        # VoicepeakCLI uses int for speed/pitch; emotion can be empty.
        speed_i = int(speed) if speed is not None else int(defaults["speed"])
        pitch_i = int(pitch) if pitch is not None else int(defaults["pitch"])
        emotion_s = str(emotion or "")

        all_frames = bytearray()
        chunks_dir = output_wav.parent / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        base_stem = output_wav.stem

        output_fmt: Optional[tuple[int, int, int, str, str]] = None

        print(f"[SYNTHESIS] (voicepeak) narrator={narrator} speed={speed_i} pitch={pitch_i} emotion={emotion_s}", flush=True)
        print(f"[SYNTHESIS] Processing {len(segments)} segments...", flush=True)

        for i, seg in enumerate(segments):
            chunk_path = chunks_dir / f"{base_stem}_part_{i:03d}.wav"

            # Check if we should skip regeneration
            skip_regen = False
            if target_indices is not None:
                if i not in target_indices:
                    if chunk_path.exists():
                        skip_regen = True
                    else:
                        print(f"[WARN] Chunk {chunk_path.name} missing, forcing regeneration.", flush=True)
            elif resume and chunk_path.exists():
                skip_regen = True

            if not skip_regen:
                text_to_speak = normalize_text_for_tts((seg.reading or seg.text or "").strip())
                if text_to_speak:
                    synthesize_chunk(
                        text=text_to_speak,
                        out_wav=chunk_path,
                        binary_path=binary_path,
                        narrator=narrator,
                        speed=speed_i,
                        pitch=pitch_i,
                        emotion=emotion_s,
                    )
                else:
                    # Empty segment -> ensure chunk exists as silence placeholder (rare)
                    chunk_path.parent.mkdir(parents=True, exist_ok=True)
                    with wave.open(str(chunk_path), "wb") as w:
                        w.setnchannels(1)
                        w.setsampwidth(2)
                        w.setframerate(24000)
                        w.writeframes(b"")

            # Load chunk (either regenerated or reused)
            try:
                with wave.open(str(chunk_path), "rb") as w:
                    params = w.getparams()
                    fmt = (params.nchannels, params.sampwidth, params.framerate, params.comptype, params.compname)
                    frames = w.readframes(w.getnframes())
                    seg.duration_sec = w.getnframes() / w.getframerate()
            except Exception as e:
                raise RuntimeError(f"[ERROR] Failed to load voicepeak chunk {chunk_path}: {e}") from e

            if output_fmt is None:
                output_fmt = fmt
            elif fmt != output_fmt:
                raise RuntimeError(
                    f"[ERROR] voicepeak WAV params mismatch at seg {i}: {fmt} != {output_fmt} ({chunk_path})"
                )

            segment_frames = bytearray()
            segment_frames.extend(generate_silence(seg.pre_pause_sec, params.framerate, params.sampwidth, params.nchannels))
            segment_frames.extend(frames)
            segment_frames.extend(generate_silence(seg.post_pause_sec, params.framerate, params.sampwidth, params.nchannels))
            all_frames.extend(segment_frames)

            if (i + 1) % 10 == 0:
                status = "SKIP" if skip_regen else "GEN "
                print(f"  ... {i+1}/{len(segments)} {status}", flush=True)

        if output_fmt is None:
            raise RuntimeError("No audio generated (output_fmt not set)")

        out_channels, out_sampwidth, out_rate, _, _ = output_fmt
        with wave.open(str(output_wav), "wb") as wf:
            wf.setnchannels(out_channels)
            wf.setsampwidth(out_sampwidth)
            wf.setframerate(out_rate)
            wf.writeframes(all_frames)

        print(f"[SYNTHESIS] Written {len(all_frames)} bytes to {output_wav}", flush=True)
        return

    # -------------------------------------------------------------------------
    # VOICEVOX
    # -------------------------------------------------------------------------

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
            segment_frames.extend(generate_silence(seg.pre_pause_sec, FRAME_RATE, SAMPLE_WIDTH, CHANNELS))
            
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
                # Fallback to regen if load fails.
                skip_regen = False
         
        if not skip_regen:
            text_to_speak = normalize_text_for_tts(seg.reading or seg.text or "")

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
                        patched_phrases = apply_kana_patches(query.get("accent_phrases"), seg_patches)
                        if patched_phrases is not None:
                            query["accent_phrases"] = patched_phrases

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
            segment_frames.extend(generate_silence(seg.post_pause_sec, FRAME_RATE, SAMPLE_WIDTH, CHANNELS))
            
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


def generate_srt(
    segments: List[AudioSegment],
    output_srt: Path,
    *,
    channel: str = "",
    video_no: str = "",
    engine: str = "",
    voice_config: Optional[Dict[str, Any]] = None,
    voicevox_client: Optional[VoicevoxClient] = None,
    speaker_id: int = 0,
) -> None:
    """
    Generate SRT from segments with precise timing.
    """
    def format_time(total_seconds: float) -> str:
        hours = int(total_seconds // 3600)
        minutes = int((total_seconds % 3600) // 60)
        seconds = int(total_seconds % 60)
        milliseconds = int((total_seconds * 1000) % 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    def _env_int(key: str, default: int) -> int:
        try:
            return int(str(os.getenv(key, str(default))).strip())
        except Exception:
            return default

    def _env_float(key: str, default: float) -> float:
        try:
            return float(str(os.getenv(key, str(default))).strip())
        except Exception:
            return default

    # Subtitle cue splitting (for readability).
    # - Splits long sentences into smaller cues using punctuation-aware heuristics (no text edits).
    # - Keeps timings within each segment by distributing duration proportionally.
    ch = str(channel or "").upper().strip()
    try:
        vid_i = int(str(video_no or "").strip() or "0")
    except Exception:
        vid_i = 0

    default_srt_max_chars = 34
    # CH22: 023+ は CapCut 上で読みやすさ優先で、より細かく分割する。
    if ch == "CH22" and vid_i >= 23:
        default_srt_max_chars = 24
    # CH04: 目安 27字/行（読点で改行しやすくする）
    if ch == "CH04":
        default_srt_max_chars = 27
    # CH09: 1行16字まで（2行まで）を厳守するため、cue分割も短めに寄せる。
    if ch == "CH09":
        default_srt_max_chars = 16

    srt_max_chars = max(12, _env_int("SRT_CUE_MAX_CHARS", default_srt_max_chars))
    srt_min_cue_sec = max(0.0, _env_float("SRT_CUE_MIN_SEC", 0.8))

    _PUNCT_STRONG = set("。．.!！?？…")
    _PUNCT_WEAK = set("、，,;；:：")
    _PUNCT_CLOSERS = set("」』】）)〉》]")

    def _maybe_voicevox_weights(parts: list[str]) -> Optional[list[float]]:
        """
        For VOICEVOX, estimate per-part speech duration from audio_query phoneme lengths.
        This improves in-segment subtitle timing vs naive char-length weighting.
        """
        if str(engine or "").lower() != "voicevox":
            return None
        if not voicevox_client:
            return None
        if not parts:
            return None

        weights: list[float] = []
        for part in parts:
            part = str(part or "")
            if not part.strip():
                weights.append(0.0)
                continue
            try:
                text_to_query = normalize_text_for_tts(part.strip())
                q = voicevox_client.audio_query(text_to_query, int(speaker_id))
                # Mirror strict_synthesis VOICEVOX params when available.
                if isinstance(voice_config, dict):
                    if "speed_scale" in voice_config:
                        q["speedScale"] = voice_config.get("speed_scale", q.get("speedScale", 1.0))
                    if "pitch_scale" in voice_config:
                        q["pitchScale"] = voice_config.get("pitch_scale", q.get("pitchScale", 0.0))
                    if "intonation_scale" in voice_config:
                        q["intonationScale"] = voice_config.get("intonation_scale", q.get("intonationScale", 1.0))
                    if "volume_scale" in voice_config:
                        q["volumeScale"] = voice_config.get("volume_scale", q.get("volumeScale", 1.0))
                    if "pre_phoneme_length" in voice_config:
                        q["prePhonemeLength"] = voice_config.get("pre_phoneme_length", q.get("prePhonemeLength", 0.1))
                    if "post_phoneme_length" in voice_config:
                        q["postPhonemeLength"] = voice_config.get("post_phoneme_length", q.get("postPhonemeLength", 0.1))

                total = float(q.get("prePhonemeLength") or 0.0) + float(q.get("postPhonemeLength") or 0.0)
                for ap in q.get("accent_phrases") or []:
                    for mora in ap.get("moras") or []:
                        total += float(mora.get("consonant_length") or 0.0) + float(mora.get("vowel_length") or 0.0)
                    pm = ap.get("pause_mora")
                    if pm:
                        total += float(pm.get("consonant_length") or 0.0) + float(pm.get("vowel_length") or 0.0)
                speed = float(q.get("speedScale") or 1.0) or 1.0
                weights.append(max(0.0, total / speed))
            except Exception:
                return None

        if sum(weights) <= 0:
            return None
        return weights

    def _split_text_for_cues(text: str, max_chars: int) -> list[str]:
        if not text:
            return [text]
        if len(text) <= max_chars:
            return [text]
        if text.lstrip().startswith("#"):
            # Keep headings intact as a single cue (linebreak formatter can wrap).
            return [text]

        _OPEN_BRACKETS = set("（(「『【〈《[")
        _CLOSE_BRACKETS = set("）)」』】〉》]")
        _SMALL_KANA = set("ぁぃぅぇぉゃゅょっァィゥェォャュョッー")
        _BAD_START_CHARS = set(_PUNCT_STRONG) | set(_PUNCT_WEAK) | set(_PUNCT_CLOSERS) | _CLOSE_BRACKETS | _SMALL_KANA

        def _is_kanji(ch: str) -> bool:
            return "\u4e00" <= ch <= "\u9fff"

        def _is_hiragana(ch: str) -> bool:
            return "\u3041" <= ch <= "\u309f"

        # Keep cue length a *soft* target: allow up to 2-line budget to avoid unnatural splits
        # (e.g., starting a cue with 助詞 / cutting inside a word).
        line_max_lines = max(1, _env_int("SRT_LINEBREAK_MAX_LINES", 2))
        hard_max_chars = max(max_chars, max_chars * line_max_lines)
        target_chars = max_chars

        def _split_by_tokens() -> Optional[list[str]]:
            try:
                from audio_tts.tts.mecab_tokenizer import tokenize_with_mecab  # type: ignore
            except Exception:
                return None

            try:
                toks = tokenize_with_mecab(text)
            except Exception:
                return None

            if not toks:
                return None
            surfaces = [str(t.get("surface") or "") for t in toks]
            poss = [str(t.get("pos") or "") for t in toks]
            if "".join(surfaces) != text:
                return None

            lens = [len(s) for s in surfaces]
            cum: list[int] = [0]
            for l in lens:
                cum.append(cum[-1] + int(l))

            def _token_at(i: int) -> dict:
                return toks[i] if 0 <= i < len(toks) else {}

            def _is_bad_start_token(i: int) -> bool:
                tok = _token_at(i)
                surf = str(tok.get("surface") or "")
                if not surf:
                    return True
                pos = str(tok.get("pos") or "")
                if pos in {"助詞", "助動詞"}:
                    return True
                first = surf[0]
                if first in _BAD_START_CHARS:
                    return True
                # Avoid starting with a pure symbol token except open brackets.
                if pos == "記号" and first not in _OPEN_BRACKETS:
                    return True
                return False

            def _score_break(start_idx: int, break_idx: int) -> float:
                # break_idx: token index where we cut (left=toks[start:break], right=toks[break:])
                left_len = cum[break_idx] - cum[start_idx]
                if left_len <= 0:
                    return -1e9

                left_last = surfaces[break_idx - 1][-1] if break_idx - 1 >= 0 and surfaces[break_idx - 1] else ""
                score = 0.0

                # Length preference: keep near target (but allow larger if needed).
                score -= abs(left_len - target_chars) * 0.9
                if left_len < int(target_chars * 0.55):
                    score -= 40.0
                if left_len > int(target_chars * 1.6):
                    score -= (left_len - int(target_chars * 1.6)) * 2.0

                # Prefer punctuation boundaries.
                if left_last in _PUNCT_STRONG:
                    score += 45.0
                elif left_last in _PUNCT_WEAK:
                    score += 18.0
                elif left_last in _PUNCT_CLOSERS or left_last in _CLOSE_BRACKETS:
                    score += 10.0

                # Avoid ending on an opening bracket.
                if left_last in _OPEN_BRACKETS:
                    score -= 60.0

                # Avoid breaks that cause the next cue to start badly.
                if break_idx < len(toks):
                    next_surf = surfaces[break_idx] if break_idx < len(surfaces) else ""
                    # Never start a cue with a closing quote/bracket (it should belong to the previous cue).
                    if next_surf and (next_surf[0] in _CLOSE_BRACKETS or next_surf[0] in _PUNCT_CLOSERS):
                        return -1e9
                    if _is_bad_start_token(break_idx):
                        score -= 200.0

                # Avoid splitting inside words / compounds (Kanji/Kana boundary, Kanji compound).
                if break_idx < len(toks):
                    right_first = surfaces[break_idx][0] if surfaces[break_idx] else ""
                    if left_last and right_first:
                        if _is_kanji(left_last) and (_is_hiragana(right_first) or _is_kanji(right_first)):
                            score -= 120.0

                # Avoid ending on particles/auxiliaries (commonly indicates a split mid-phrase).
                last_pos = poss[break_idx - 1] if break_idx - 1 < len(poss) else ""
                if last_pos in {"助詞", "助動詞"}:
                    score -= 80.0

                return score

            parts: list[str] = []
            i = 0
            n = len(toks)
            while i < n:
                # Fast path: remaining fits into one cue budget.
                remain_len = cum[n] - cum[i]
                if remain_len <= hard_max_chars:
                    parts.append("".join(surfaces[i:n]))
                    break

                # Consider all breakpoints within the hard max.
                best_j: Optional[int] = None
                best_score = -1e12
                for j in range(i + 1, n + 1):
                    left_len = cum[j] - cum[i]
                    if left_len > hard_max_chars:
                        break
                    sc = _score_break(i, j)
                    if sc > best_score:
                        best_score = sc
                        best_j = j

                if best_j is None or best_j <= i:
                    # Degenerate tokenization (e.g., a single token longer than budget).
                    # Bail out to the char-based fallback to preserve text exactly.
                    return None

                parts.append("".join(surfaces[i:best_j]))
                i = best_j

            return [p for p in parts if p.strip()]

        token_parts = _split_by_tokens()
        if token_parts:
            return token_parts

        # Fallback: char-based (best-effort), but avoid starting next cue with punctuation.
        chunks: list[str] = []
        start = 0
        n = len(text)
        while start < n:
            remaining = n - start
            if remaining <= hard_max_chars:
                chunks.append(text[start:])
                break

            end_limit = min(n, start + hard_max_chars)
            break_pos: Optional[int] = None

            for i in range(end_limit - 1, start, -1):
                ch = text[i]
                if ch in _PUNCT_STRONG or ch in _PUNCT_CLOSERS or ch in _PUNCT_WEAK:
                    break_pos = i + 1
                    break

            if break_pos is None or break_pos <= start:
                break_pos = end_limit

            while break_pos < n and text[break_pos] in _BAD_START_CHARS:
                break_pos += 1

            chunks.append(text[start:break_pos])
            start = break_pos

        return [c for c in chunks if c.strip()]

    def _allocate_durations(
        total_sec: float, parts: list[str], min_sec: float, *, weights: Optional[list[float]] = None
    ) -> list[float]:
        if not parts:
            return []
        if total_sec <= 0:
            return [0.0 for _ in parts]

        if weights is not None and len(weights) == len(parts) and any(float(w or 0.0) > 0.0 for w in weights):
            wts = [max(1e-6, float(w or 0.0)) for w in weights]
        else:
            wts = [max(1.0, float(len(p.replace("\n", "")))) for p in parts]
        total_w = sum(wts) or float(len(wts))
        durations = [total_sec * (w / total_w) for w in wts]

        # Enforce a minimal readable cue duration when feasible.
        if min_sec > 0 and total_sec >= (min_sec * len(parts)):
            fixed = [False] * len(parts)
            remaining_total = total_sec
            remaining_w = 0
            for i, d in enumerate(durations):
                if d < min_sec:
                    durations[i] = min_sec
                    fixed[i] = True
                    remaining_total -= min_sec
                else:
                    remaining_w += wts[i]

            remaining_total = max(0.0, remaining_total)
            if remaining_w > 0:
                for i in range(len(parts)):
                    if fixed[i]:
                        continue
                    durations[i] = remaining_total * (wts[i] / remaining_w)

        # Force exact sum to avoid drift (adjust last).
        diff = total_sec - sum(durations)
        durations[-1] += diff
        if durations[-1] < 0:
            durations[-1] = 0.0
        return durations

    current_time = 0.0
    cues: list[dict[str, object]] = []

    for seg in segments:
        current_time += float(seg.pre_pause_sec or 0.0)
        start_time = current_time
        duration = float(seg.duration_sec or 0.0)
        end_time = start_time + duration

        parts = _split_text_for_cues(str(seg.text or ""), srt_max_chars)
        if len(parts) <= 1 or duration <= 0:
            cues.append({"start": start_time, "end": end_time, "text": parts[0] if parts else str(seg.text or "")})
        else:
            voicevox_w = _maybe_voicevox_weights(parts)
            durs = _allocate_durations(duration, parts, srt_min_cue_sec, weights=voicevox_w)
            t = start_time
            for part, d in zip(parts, durs):
                cues.append({"start": t, "end": t + d, "text": part})
                t += d
            # Ensure final cue ends exactly at the segment end (float/rounding guard).
            if cues:
                cues[-1]["end"] = end_time

        current_time = end_time + float(seg.post_pause_sec or 0.0)

    # Fix up cue text boundaries: never start a cue with punctuation/closing quotes.
    # This is safe because these characters are not spoken (timing remains unchanged).
    shiftable_prefix = set(_PUNCT_STRONG) | set(_PUNCT_WEAK) | set(_PUNCT_CLOSERS)
    i = 1
    while i < len(cues):
        cur_text = str(cues[i].get("text", "") or "")
        moved: list[str] = []
        while cur_text and cur_text[0] in shiftable_prefix:
            moved.append(cur_text[0])
            cur_text = cur_text[1:]
        if moved:
            prev_text = str(cues[i - 1].get("text", "") or "")
            cues[i - 1]["text"] = prev_text + "".join(moved)
            cues[i]["text"] = cur_text
        # Drop empty cues (can happen if a cue was only punctuation).
        if not str(cues[i].get("text", "") or "").strip():
            prev_end = float(cues[i - 1].get("end", 0.0))
            cur_end = float(cues[i].get("end", prev_end))
            cues[i - 1]["end"] = max(prev_end, cur_end)
            del cues[i]
            continue
        i += 1

    # Apply safe in-cue linebreak formatting (defaults to heuristic; no text changes).
    cue_entries: list[dict] = [{"index": i + 1, "text": str(cue.get("text", ""))} for i, cue in enumerate(cues)]
    try:
        from audio_tts.tts.llm_adapter import format_srt_lines  # type: ignore

        target_len = 27 if ch == "CH04" else 24
        if ch == "CH09":
            target_len = 16

        # Enforce strict channel contracts (avoid accidental env overrides).
        env_overrides: dict[str, str] = {}
        if ch == "CH09":
            env_overrides = {
                "SRT_LINEBREAK_MAX_LINES": "2",
                "SRT_LINEBREAK_MAX_CHARS_PER_LINE": str(target_len),
                "SRT_LINEBREAK_OVERFLOW_CHARS": "0",
                "SRT_LINEBREAK_MODE": "heuristic",
            }

        if env_overrides:
            saved = {k: os.environ.get(k) for k in env_overrides}
            os.environ.update(env_overrides)
            try:
                cue_entries = format_srt_lines(cue_entries, model="", api_key="", target_len=target_len)
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
        else:
            cue_entries = format_srt_lines(cue_entries, model="", api_key="", target_len=target_len)
    except Exception as e:
        print(f"[SRT] linebreak formatter failed (pass-through): {e}")

    entries: list[str] = []
    for i, (cue, ent) in enumerate(zip(cues, cue_entries), start=1):
        st = float(cue.get("start", 0.0))
        et = float(cue.get("end", st))
        text = str(ent.get("text", cue.get("text", "")) or "")
        entries.append(f"{i}\n{format_time(st)} --> {format_time(et)}\n{text}\n")

    output_srt.write_text("\n".join(entries), encoding="utf-8")
    print(f"[SRT] Written to {output_srt}")
