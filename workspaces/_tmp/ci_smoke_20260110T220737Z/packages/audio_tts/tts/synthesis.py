from __future__ import annotations

import wave
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import json

from .voicepeak_cli import synthesize_chunk, VoicepeakCLIError
from .voicevox_api import VoicevoxClient
from .routing import (
    load_routing_config,
    resolve_voicevox_speaker_id,
    voicepeak_defaults,
    resolve_eleven_voice,
    resolve_eleven_model,
    RoutingConfig,
)
from .reading_structs import KanaPatch
from .text_normalizer import normalize_text_for_tts


def apply_kana_patches(
    accent_phrases: Optional[List[Dict[str, object]]],
    patches: Optional[List[KanaPatch]],
) -> Optional[List[Dict[str, object]]]:
    """Return a patched deep copy of accent_phrases without mutating the original.

    Notes:
    - `accent_phrases` is expected to be the per-block list returned by VOICEVOX
      `/audio_query` (a list of phrase dicts containing `moras`).
    - `patches` should already be filtered for the current block; `block_id` on
      the data class is for higher-level routing and is ignored here.
    - If `correct_moras` is provided, it is preferred over `correct_kana` to
      support multi-character moras. Otherwise the function falls back to the
      1文字≈1モーラの暫定置換で `correct_kana` をバラして適用する。
    """

    if accent_phrases is None:
        return None

    patched = json.loads(json.dumps(accent_phrases))

    if not patches:
        return patched

    # Flatten all moras within the block for index-based patching
    flat_moras: List[Dict[str, object]] = []
    for phrase in patched:
        if isinstance(phrase, dict):
            flat_moras.extend(phrase.get("moras", []) or [])

    if not flat_moras:
        return patched

    for patch in patches:
        start, end = patch.mora_range
        if start < 0:
            continue

        end = min(end, len(flat_moras))
        if start >= end:
            continue

        replace_seq: List[str]
        if patch.correct_moras:
            replace_seq = list(patch.correct_moras)
        else:
            replace_seq = list(patch.correct_kana)

        for idx in range(start, end):
            seq_idx = idx - start
            if seq_idx < len(replace_seq):
                flat_moras[idx]["text"] = replace_seq[seq_idx]
                # Reset phoneme info to avoid mismatch (Voicevox might ignore this but safer to clear)
                flat_moras[idx]["consonant"] = None
                flat_moras[idx]["consonant_length"] = None
                flat_moras[idx]["vowel_length"] = None

    return patched


@dataclass
class VoicevoxResult:
    wav_path: Path
    sample_rate: int
    duration_sec: float
    chunk_paths: List[Path]                 # 全サブチャンク
    chunk_meta: List[Dict[str, object]]     # サブチャンク単位
    block_meta: List[Dict[str, object]]     # SRT/ポーズ用にブロック単位へ集約
    accent_phrases: Optional[object]
    kana: Optional[str]


@dataclass
class VoicepeakResult:
    wav_path: Path
    sample_rate: int
    duration_sec: float
    chunk_paths: List[Path]                 # 全サブチャンク
    chunk_meta: List[Dict[str, object]]     # サブチャンク単位
    block_meta: List[Dict[str, object]]     # ブロック単位
    narrator: Optional[str]
    speed: Optional[int]
    pitch: Optional[int]
    emotion: Optional[str]

@dataclass
class ElevenResult:
    wav_path: Path
    sample_rate: int
    duration_sec: float
    chunk_paths: Optional[List[Path]] = None
    chunk_meta: Optional[List[Dict[str, object]]] = None

def _write_wav(content: bytes, out_path: Path) -> Dict[str, float]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(content)
    with wave.open(str(out_path), "rb") as wf:
        params = wf.getparams()
        duration = params.nframes / float(params.framerate)
        return {"sample_rate": params.framerate, "duration_sec": duration}


# システム全体のポーズを優先するため、デフォルトは無音トリムをしない
TRIM_SILENCE_ENABLED = False


def _trim_wav_silence(wav_path: Path, threshold: int = 300, padding_ms: int = 30) -> None:
    """
    前後の無音を簡易的にトリムする。
    - threshold: audioop.rms のしきい値
    - padding_ms: トリム後に残す余裕時間
    """
    # audioop は Python3.13 で廃止予定のため、必要時のみ遅延 import し、
    # import 時の DeprecationWarning は抑制する（デフォルトでは TRIM 無効）。
    try:
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            import audioop as _audioop  # type: ignore
    except Exception:
        # audioop が無効/未導入な環境では何もせず返す
        return
    wav_path = Path(wav_path)
    if not wav_path.exists():
        return
    try:
        with wave.open(str(wav_path), "rb") as wf:
            params = wf.getparams()
            sampwidth = params.sampwidth
            nchannels = params.nchannels
            framerate = params.framerate
            frames = wf.readframes(params.nframes)
    except Exception:
        return

    frame_size = max(512, framerate // 20)  # 50ms相当を目安
    rms_threshold = max(50, threshold)

    total_frames = len(frames) // (sampwidth * nchannels)
    if total_frames == 0:
        return

    def _rms_at(idx: int) -> float:
        start = idx * sampwidth * nchannels
        end = start + frame_size * sampwidth * nchannels
        chunk = frames[start:end]
        if not chunk:
            return 0.0
        return _audioop.rms(chunk, sampwidth)

    # 先頭側
    start_idx = 0
    while start_idx < total_frames:
        if _rms_at(start_idx) > rms_threshold:
            break
        start_idx += frame_size
    # 末尾側
    end_idx = total_frames
    while end_idx > start_idx:
        if _rms_at(end_idx - frame_size) > rms_threshold:
            break
        end_idx -= frame_size

    pad_frames = int(framerate * padding_ms / 1000)
    start_idx = max(0, start_idx - pad_frames)
    end_idx = min(total_frames, end_idx + pad_frames)

    if start_idx <= 0 and end_idx >= total_frames:
        return  # トリム不要

    trimmed = frames[start_idx * sampwidth * nchannels : end_idx * sampwidth * nchannels]
    tmp = wav_path.with_suffix(".trim_tmp.wav")
    try:
        with wave.open(str(tmp), "wb") as wout:
            wout.setparams(params)
            wout.writeframes(trimmed)
        tmp.replace(wav_path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except Exception:
                pass


def _wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() / float(wf.getframerate())
    except Exception:
        return 0.0


def _concat_blocks_with_pauses(block_paths: List[Path], pauses: Optional[List[float]], out_path: Path) -> Dict[str, float]:
    """
    ブロックごとに結合し、pause指定があれば無音を挟む。
    pauses は len(block_paths) と同じか、最後が欠けていてもよい（最後は無音なし）。
    """
    if not block_paths:
        raise ValueError("block_paths is empty")
    block_paths = _sort_and_validate_block_paths(block_paths)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(block_paths[0]), "rb") as wf0:
        params = wf0.getparams()
        base_fmt = (params.nchannels, params.sampwidth, params.framerate, params.comptype, params.compname)
        frames = [wf0.readframes(wf0.getnframes())]
        total_frames = wf0.getnframes()
    for i, path in enumerate(block_paths[1:], start=1):
        # block i の前に block i-1 に対応するポーズを挿入する
        if pauses and i - 1 < len(pauses):
            pause_sec = float(pauses[i - 1])
            if pause_sec > 0:
                silent_frames = int(pause_sec * params.framerate)
                frames.append(b"\x00" * silent_frames * params.sampwidth * params.nchannels)
                total_frames += silent_frames
        with wave.open(str(path), "rb") as wf:
            p = wf.getparams()
            fmt = (p.nchannels, p.sampwidth, p.framerate, p.comptype, p.compname)
            if fmt != base_fmt:
                raise ValueError("WAV params mismatch")
            data = wf.readframes(wf.getnframes())
            total_frames += wf.getnframes()
            frames.append(data)
    with wave.open(str(out_path), "wb") as wout:
        wout.setparams(params)
        for data in frames:
            wout.writeframes(data)
    return {"sample_rate": params.framerate, "duration_sec": total_frames / float(params.framerate)}


def _sort_and_validate_block_paths(block_paths: List[Path]) -> List[Path]:
    """
    ブロックファイルを _block_(number) でソートし、連番を保証する。
    欠番や重複があれば例外を投げる。
    """
    indexed = []
    for p in block_paths:
        p = Path(p)
        m = re.search(r"_block_(\d+)", p.stem)
        if not m:
            raise ValueError(f"Invalid block filename (missing index): {p}")
        idx = int(m.group(1))
        indexed.append((idx, p))
    indexed.sort(key=lambda x: x[0])
    # 連番チェック
    for expected, (idx, _) in enumerate(indexed):
        if idx != expected:
            raise ValueError(f"Block index mismatch: expected {expected}, got {idx}")
    return [p for _, p in indexed]


def voicevox_synthesis(
    b_text: str,
    out_wav: Path,
    channel: str,
    cfg: Optional[RoutingConfig] = None,
    patches: Optional[List[KanaPatch]] = None,
) -> VoicevoxResult:
    cfg = cfg or load_routing_config()
    speaker_id = resolve_voicevox_speaker_id(channel, cfg)
    client = VoicevoxClient(engine_url=cfg.voicevox_url)
    b_text = normalize_text_for_tts(b_text)
    query = client.audio_query(b_text, speaker_id)
    if patches:
        patched = apply_kana_patches(query.get("accent_phrases"), patches)
        if patched is not None:
            query["accent_phrases"] = patched
    kana = str(query.get("kana") or "")
    audio = client.synthesis(query, speaker_id)
    meta = _write_wav(audio, out_wav)
    if TRIM_SILENCE_ENABLED:
        _trim_wav_silence(out_wav)
    return VoicevoxResult(
        wav_path=out_wav,
        sample_rate=int(meta["sample_rate"]),
        duration_sec=float(meta["duration_sec"]),
        accent_phrases=query.get("accent_phrases"),
        kana=kana,
        chunk_paths=[out_wav],
        chunk_meta=[{"index": 0, "text": b_text, "wav_path": str(out_wav), "duration_sec": meta["duration_sec"]}],
    )


def voicevox_synthesis_chunks(
    blocks: List[Dict[str, object]],
    out_wav: Path,
    channel: str,
    cfg: Optional[RoutingConfig] = None,
    pauses: Optional[List[float]] = None,
    patches_by_block: Optional[Dict[int, List[KanaPatch]]] = None,
) -> VoicevoxResult:
    def _split_for_voicevox(text: str, limit: int = 80) -> List[str]:
        """VOICEVOXのaudio_queryが500を返す場合に備え、文単位で再分割するユーティリティ。"""
        import re

        text = text.strip()
        if len(text) <= limit:
            return [text] if text else []
        # 句読点や改行で分割しつつ、limitを超える場合はさらに固定幅で刻む
        sentences = re.split(r"(?<=[。？！\?！\!])", text)
        chunks: List[str] = []
        buf = ""
        for sent in sentences:
            s = sent.strip()
            if not s:
                continue
            if len(buf) + len(s) <= limit:
                buf += s
            else:
                if buf:
                    chunks.append(buf)
                if len(s) <= limit:
                    buf = s
                else:
                    # 固定幅で強制分割
                    for i in range(0, len(s), max(40, limit // 2)):
                        part = s[i : i + max(40, limit // 2)]
                        chunks.append(part)
                    buf = ""
        if buf:
            chunks.append(buf)
        return [c for c in chunks if c]

    cfg = cfg or load_routing_config()
    speaker_id = resolve_voicevox_speaker_id(channel, cfg)
    client = VoicevoxClient(engine_url=cfg.voicevox_url)
    out_wav = Path(out_wav)
    base = out_wav.with_suffix("")
    chunk_dir = base.parent / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_dir = base.parent / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_dir = base.parent / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_paths: List[Path] = []  # サブチャンク
    chunk_meta: List[Dict[str, object]] = []
    block_meta: List[Dict[str, object]] = []
    block_paths: List[Path] = []
    # kana from first block audio_query (approx)
    kana = ""
    accent = None
    for blk in blocks:
        idx = int(blk.get("index", len(block_paths)))
        text = str(blk.get("text", ""))
        # Prefer b_text (reading script) if available, otherwise fallback to text
        tts_input = str(blk.get("b_text") or text)
        # Signal-based pause logic: '#' はポーズマーカーとして機能したが、
        # 音声合成エンジンには渡さない（"シャープ"と読まれるのを防ぐ）
        text_for_tts = tts_input.lstrip("#").strip()
        text_for_tts = normalize_text_for_tts(text_for_tts)
        if not text_for_tts:
             # マーカーのみの行だった場合は無音として処理（またはスキップ）したいが、synthesisでは空文字エラーになるため
             # ここでは空文字ならスキップ、あるいは空白にする。Voicevoxは空文字でエラーになる可能性がある。
             # 文脈上、完全に空ならスキップが安全。
             continue

        # [Restored & Hardened] Smart Caching
        # Only use existing WAV if its duration is sane relative to the text length.
        merged_candidate = chunk_dir / f"{base.name}_block_{idx:03}.wav"
        if merged_candidate.exists() and merged_candidate.stat().st_size > 0:
            dur = _wav_duration(merged_candidate)
            # Validation: Block reuse sanity check
            # Rule: Text < 12 chars should NOT have duration > 8.0s
            is_suspicious = (len(text_for_tts) < 12 and dur > 8.0)
            
            if not is_suspicious:
                # SAFE: Reuse existing audio
                block_paths.append(merged_candidate)
                block_meta.append({"index": idx, "text": text, "wav_path": str(merged_candidate), "duration_sec": dur})
                continue
            else:
                # UNSAFE: Suspicious duration detected. Force regeneration.
                # (Logs might be verbose, so maybe keeping it silent or print once per file?)
                pass

        sub_parts: List[Path] = []
        sub_meta: List[Dict[str, object]] = []
        try:
            q = client.audio_query(text_for_tts, speaker_id)
            if patches_by_block:
                patch_candidates = patches_by_block.get(idx)
                if patch_candidates:
                    patched = apply_kana_patches(q.get("accent_phrases"), patch_candidates)
                    if patched is not None:
                        q["accent_phrases"] = patched
            if not kana:
                kana = str(q.get("kana") or "")
                accent = q.get("accent_phrases")
            audio = client.synthesis(q, speaker_id)
            part = chunk_dir / f"{base.name}_part_{idx:03}.wav"
            meta = _write_wav(audio, part)
            if TRIM_SILENCE_ENABLED:
                _trim_wav_silence(part)
            sub_parts.append(part)
            sub_meta.append({"index": idx * 100, "text": text, "wav_path": str(part), "duration_sec": meta["duration_sec"]})
        except Exception:
            subtexts = _split_for_voicevox(text_for_tts)
            if not subtexts:
                raise
            for s_i, s_txt in enumerate(subtexts):
                s_txt = normalize_text_for_tts(s_txt)
                q_sub = client.audio_query(s_txt, speaker_id)
                if not kana:
                    kana = str(q_sub.get("kana") or "")
                    accent = q_sub.get("accent_phrases")
                audio_sub = client.synthesis(q_sub, speaker_id)
                part = chunk_dir / f"{base.name}_part_{idx:03}_{s_i:02}.wav"
                meta_sub = _write_wav(audio_sub, part)
                if TRIM_SILENCE_ENABLED:
                    _trim_wav_silence(part)
                sub_parts.append(part)
                sub_meta.append(
                    {"index": idx * 100 + s_i, "text": s_txt, "wav_path": str(part), "duration_sec": meta_sub["duration_sec"]}
                )
        # サブチャンクをまとめて1ブロックに統合
        merged = chunk_dir / f"{base.name}_block_{idx:03}.wav"
        merged_meta = _concat_wavs(sub_parts, merged)
        block_paths.append(merged)
        block_meta.append({"index": idx, "text": text, "wav_path": str(merged), "duration_sec": merged_meta["duration_sec"]})
        chunk_paths.extend(sub_parts)
        chunk_meta.extend(sub_meta)

    concat_meta = _concat_blocks_with_pauses(block_paths, pauses, out_wav)
    return VoicevoxResult(
        wav_path=out_wav,
        sample_rate=int(concat_meta["sample_rate"]),
        duration_sec=float(concat_meta["duration_sec"]),
        accent_phrases=accent,
        kana=kana,
        chunk_paths=chunk_paths,
        chunk_meta=chunk_meta,
        block_meta=block_meta,
    )


def _concat_wavs(chunk_paths: List[Path], out_path: Path) -> Dict[str, float]:
    if not chunk_paths:
        raise ValueError("chunk_paths is empty")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(chunk_paths[0]), "rb") as wf0:
        params = wf0.getparams()
        base_fmt = (params.nchannels, params.sampwidth, params.framerate, params.comptype, params.compname)
        frames = [wf0.readframes(wf0.getnframes())]
        total_frames = wf0.getnframes()
    for path in chunk_paths[1:]:
        with wave.open(str(path), "rb") as wf:
            p = wf.getparams()
            fmt = (p.nchannels, p.sampwidth, p.framerate, p.comptype, p.compname)
            if fmt != base_fmt:
                raise ValueError("WAV params mismatch")
            data = wf.readframes(wf.getnframes())
            total_frames += wf.getnframes()
            frames.append(data)
    with wave.open(str(out_path), "wb") as wout:
        wout.setparams(params)
        for data in frames:
            wout.writeframes(data)
    return {"sample_rate": params.framerate, "duration_sec": total_frames / float(params.framerate)}


def voicepeak_synthesis(
    b_text_chunks: List[Dict[str, object]],
    out_wav: Path,
    channel: str,
    cfg: Optional[RoutingConfig] = None,
    narrator: Optional[str] = None,
    speed: Optional[int] = None,
    pitch: Optional[int] = None,
    emotion: Optional[str] = None,
    chunk_limit: int = 120,
    pauses: Optional[List[float]] = None,
) -> VoicepeakResult:
    def _split_for_voicepeak(text: str, limit: int) -> List[str]:
        """Voicepeak CLI は長文や記号で不安定になることがあるため、句読点基準でさらに細かく分割する。"""
        import re

        text = text.strip()
        if len(text) <= limit:
            return [text] if text else []
        sentences = re.split(r"(?<=[。．.!！?？、,；;：:])", text)
        chunks: List[str] = []
        buf = ""
        for sent in sentences:
            s = sent.strip()
            if not s:
                continue
            if len(buf) + len(s) <= limit:
                buf += s
            else:
                if buf:
                    chunks.append(buf)
                if len(s) <= limit:
                    buf = s
                else:
                    step = max(40, limit // 2)
                    for i in range(0, len(s), step):
                        part = s[i : i + step].strip()
                        if part:
                            chunks.append(part)
                    buf = ""
        if buf:
            chunks.append(buf)
        return [c for c in chunks if c]

    cfg = cfg or load_routing_config()
    defaults = voicepeak_defaults(channel, cfg)
    chunk_paths: List[Path] = []  # サブチャンク
    chunk_meta: List[Dict[str, object]] = []
    block_meta: List[Dict[str, object]] = []
    block_paths: List[Path] = []
    out_wav = Path(out_wav)
    base = out_wav.with_suffix("")
    chunk_dir = out_wav.parent / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    for chunk in b_text_chunks:
        idx = int(chunk.get("index", len(chunk_paths)))
        text = str(chunk.get("text", ""))
        # Prefer b_text (reading script) if available, otherwise fallback to text
        tts_input = str(chunk.get("b_text") or text)
        text_for_tts = tts_input.lstrip("#").strip()
        text_for_tts = normalize_text_for_tts(text_for_tts)
        if not text_for_tts:
            continue
            
        sub_texts = _split_for_voicepeak(text_for_tts, chunk_limit)
        sub_parts: List[Path] = []
        sub_meta: List[Dict[str, object]] = []
        for sub_i, sub in enumerate(sub_texts):
            sub_idx = idx * 100 + sub_i
            chunk_path = chunk_dir / f"{base.name}_chunk_{sub_idx:03}.wav"
            synthesize_chunk(
                text=sub,
                out_wav=chunk_path,
                binary_path=defaults["binary_path"],
                narrator=narrator or defaults["narrator"],
                speed=speed or defaults["speed"],
                pitch=pitch or defaults["pitch"],
                emotion=emotion or defaults["emotion"],
            )
            if TRIM_SILENCE_ENABLED:
                _trim_wav_silence(chunk_path)
            sub_parts.append(chunk_path)
            try:
                with wave.open(str(chunk_path), "rb") as wf:
                    dur = wf.getnframes() / float(wf.getframerate())
                dur_val = dur
            except Exception:
                dur_val = 0.0
            sub_meta.append({"index": sub_idx, "text": sub, "wav_path": str(chunk_path), "duration_sec": dur_val})
        merged = chunk_dir / f"{base.name}_block_{idx:03}.wav"
        merged_meta = _concat_wavs(sub_parts, merged)
        block_paths.append(merged)
        block_meta.append({"index": idx, "text": text, "wav_path": str(merged), "duration_sec": merged_meta["duration_sec"]})
        chunk_paths.extend(sub_parts)
        chunk_meta.extend(sub_meta)

    meta = _concat_blocks_with_pauses(block_paths, pauses, out_wav)
    return VoicepeakResult(
        wav_path=out_wav,
        sample_rate=int(meta["sample_rate"]),
        duration_sec=float(meta["duration_sec"]),
        chunk_paths=chunk_paths,
        chunk_meta=chunk_meta,
        block_meta=block_meta,
        narrator=narrator or defaults["narrator"],
        speed=speed or defaults["speed"],
        pitch=pitch or defaults["pitch"],
        emotion=emotion or defaults["emotion"],
    )


def elevenlabs_synthesis(b_text: str, out_wav: Path, channel: str, cfg: Optional[RoutingConfig] = None) -> ElevenResult:
    cfg = cfg or load_routing_config()
    try:
        # Lazy import so VOICEVOX/VOICEPEAK-only runs do not require requests.
        from .elevenlabs_client import build_eleven_client  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "ElevenLabs synthesis was requested but dependencies are unavailable. "
            "Install 'requests' and configure ELEVEN_API_KEY, or use voicevox/voicepeak."
        ) from exc
    client = build_eleven_client(
        cfg.eleven_api_key_env, resolve_eleven_voice(channel, cfg), resolve_eleven_model(cfg)
    )
    out_wav = Path(out_wav)
    res = client.text_to_speech(b_text, out_wav, format="wav")
    return ElevenResult(
        wav_path=out_wav,
        sample_rate=int(res.get("sample_rate") or 24000),
        duration_sec=float(res.get("duration_sec") or 0.0),
    )


def elevenlabs_synthesis_chunks(
    blocks: List[Dict[str, object]],
    out_wav: Path,
    channel: str,
    cfg: Optional[RoutingConfig] = None,
    pauses: Optional[List[float]] = None,
) -> ElevenResult:
    cfg = cfg or load_routing_config()
    try:
        # Lazy import so VOICEVOX/VOICEPEAK-only runs do not require requests.
        from .elevenlabs_client import build_eleven_client  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "ElevenLabs synthesis was requested but dependencies are unavailable. "
            "Install 'requests' and configure ELEVEN_API_KEY, or use voicevox/voicepeak."
        ) from exc
    client = build_eleven_client(
        cfg.eleven_api_key_env, resolve_eleven_voice(channel, cfg), resolve_eleven_model(cfg)
    )
    out_wav = Path(out_wav)
    base = out_wav.with_suffix("")
    chunk_dir = base.parent / "chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    chunk_paths: List[Path] = []
    chunk_meta: List[Dict[str, object]] = []
    block_meta: List[Dict[str, object]] = []
    block_paths: List[Path] = []
    for blk in blocks:
        idx = int(blk.get("index", len(chunk_paths)))
        text = str(blk.get("text", ""))
        # Prefer b_text (reading script) if available, otherwise fallback to text
        tts_input = str(blk.get("b_text") or text)
        text_for_tts = tts_input.lstrip("#").strip()
        if not text_for_tts:
            continue
            
        part = chunk_dir / f"{base.name}_part_{idx:03}.wav"
        res = client.text_to_speech(text_for_tts, part, format="wav")
        dur = float(res.get("duration_sec") or _wav_duration(part))
        block_paths.append(part)
        block_meta.append({"index": idx, "text": text, "wav_path": str(part), "duration_sec": dur})
        chunk_paths.append(part)
        chunk_meta.append({"index": idx, "text": text, "wav_path": str(part), "duration_sec": dur})
    concat_meta = _concat_blocks_with_pauses(block_paths, pauses, out_wav)
    return ElevenResult(
        wav_path=out_wav,
        sample_rate=int(concat_meta["sample_rate"]),
        duration_sec=float(concat_meta["duration_sec"]),
        chunk_paths=chunk_paths,
        chunk_meta=chunk_meta,
    )
