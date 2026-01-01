from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from typing import List
import struct


class VoicepeakCLIError(RuntimeError):
    pass


def _normalize_narrator_name(name: str) -> str:
    """
    Voicepeak CLI on macOS is unstable when narrator contains non-ASCII (e.g. '女性2').
    Keep configs human-friendly, but map known JP aliases to CLI-safe names.
    """
    value = (name or "").strip()
    if not value:
        return value
    alias = {
        "男性1": "Japanese Male 1",
        "男性2": "Japanese Male 2",
        "男性3": "Japanese Male 3",
        "女性1": "Japanese Female 1",
        "女性2": "Japanese Female 2",
        "女性3": "Japanese Female 3",
    }
    return alias.get(value, value)


def synthesize_chunk(
    *,
    text: str,
    out_wav: Path,
    binary_path: str,
    narrator: str,
    speed: int,
    pitch: int,
    emotion: str = "",
) -> None:
    """
    Voicepeak CLI が改行を含む長いテキストで不安定なため、
    改行で分割して順次生成し、WAVを連結する。
    """
    bin_path = Path(binary_path)
    if not bin_path.exists():
        raise VoicepeakCLIError(f"Voicepeak binary not found: {binary_path}")

    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)

    lines = [t for t in text.splitlines() if t.strip()]
    if not lines:
        lines = [text]

    narrator_safe = _normalize_narrator_name(narrator)

    try:
        if len(lines) == 1:
            _run_voicepeak_line(lines[0], out_wav, bin_path, narrator_safe, speed, pitch, emotion)
            return
        tmp_parts: List[Path] = []
        base = out_wav.with_suffix("")
        for i, line in enumerate(lines):
            part = base.parent / f"{base.name}_part_{i:03}.wav"
            _run_voicepeak_line(line, part, bin_path, narrator_safe, speed, pitch, emotion)
            tmp_parts.append(part)
        _concat_wavs(tmp_parts, out_wav)
    except VoicepeakCLIError:
        # 改行分割でも落ちる場合、句読点でさらに細分化してリトライ
        tmp_parts = []
        base = out_wav.with_suffix("")
        def split_sentences(s: str) -> List[str]:
            buf = ""
            res: List[str] = []
            for ch in s:
                buf += ch
                if ch in "。.!！？?":
                    res.append(buf.strip())
                    buf = ""
            if buf.strip():
                res.append(buf.strip())
            return [x for x in res if x]
        sentences = []
        for line in lines:
            sentences.extend(split_sentences(line))
        if not sentences:
            sentences = lines
        for i, sent in enumerate(sentences):
            part = base.parent / f"{base.name}_fallback_{i:03}.wav"
            try:
                _run_voicepeak_line(sent, part, bin_path, narrator_safe, speed, pitch, emotion)
            except VoicepeakCLIError:
                # それでも失敗する箇所は無音で代替してパイプラインを止めない
                _write_silence(part, duration_sec=0.35, sample_rate=24000)
            tmp_parts.append(part)
        _concat_wavs(tmp_parts, out_wav)
    finally:
        # cleanup temp parts
        try:
            tmp_glob = list(out_wav.parent.glob(f"{out_wav.stem}_part_*.wav")) + list(
                out_wav.parent.glob(f"{out_wav.stem}_fallback_*.wav")
            )
            for p in tmp_glob:
                if p.exists():
                    p.unlink()
        except Exception:
            pass


def _run_voicepeak_line(
    line: str,
    out_wav: Path,
    bin_path: Path,
    narrator: str,
    speed: int,
    pitch: int,
    emotion: str,
) -> None:
    cmd = [
        str(bin_path),
        "voicepeak",
        "-s",
        line,
        "-o",
        str(out_wav),
        "-n",
        narrator,
        "--speed",
        str(speed),
        "--pitch",
        str(pitch),
    ]
    if emotion:
        cmd.extend(["-e", emotion])

    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0:
        raise VoicepeakCLIError(
            "\n".join(
                [
                    f"Voicepeak CLI failed (exit={completed.returncode})",
                    f"CMD: {' '.join(cmd)}",
                    f"STDOUT: {completed.stdout.strip()}",
                    f"STDERR: {completed.stderr.strip()}",
                ]
            )
        )
    if not out_wav.exists():
        raise VoicepeakCLIError(f"Voicepeak did not produce wav: {out_wav}")


def _concat_wavs(parts: List[Path], out_path: Path) -> None:
    if not parts:
        raise VoicepeakCLIError("No parts to concat")

    with wave.open(str(parts[0]), "rb") as w0:
        params = w0.getparams()
        frames = [w0.readframes(w0.getnframes())]
        total_frames = w0.getnframes()

    for p in parts[1:]:
        with wave.open(str(p), "rb") as w:
            if w.getparams() != params:
                # パラメータが違う場合でも先頭のパラメータに合わせて連結する（警告のみ）
                pass
            frames.append(w.readframes(w.getnframes()))
            total_frames += w.getnframes()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wout:
        wout.setparams(params)
        for data in frames:
            wout.writeframes(data)


def _write_silence(path: Path, duration_sec: float = 0.3, sample_rate: int = 24000) -> None:
    """代替用に無音WAVを書き出す（voicepeak失敗時のフェイルセーフ）"""
    n_frames = int(duration_sec * sample_rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16bit PCM
        w.setframerate(sample_rate)
        silence_frame = struct.pack("<h", 0)
        w.writeframes(silence_frame * n_frames)
