from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import List
import struct
from contextlib import contextmanager


class VoicepeakCLIError(RuntimeError):
    pass


def _clamp_int(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(value)))


def _env_float(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@contextmanager
def _voicepeak_global_lock():
    """
    VOICEPEAK CLI on macOS can crash when multiple invocations run concurrently.
    Use a cross-process lock to serialize CLI calls.
    """
    if not _env_truthy("VOICEPEAK_CLI_GLOBAL_LOCK", True):
        yield
        return
    try:
        import fcntl  # type: ignore
    except Exception:
        yield
        return

    lock_path = Path(
        (os.getenv("VOICEPEAK_CLI_LOCK_PATH") or "").strip()
        or (Path(tempfile.gettempdir()) / "factory_voicepeak_cli.lock")
    )
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    f = lock_path.open("a", encoding="utf-8")
    try:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(f, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            f.close()
        except Exception:
            pass


def _normalize_narrator_name(name: str) -> str:
    """
    Voicepeak CLI on macOS is unstable when narrator contains non-ASCII (e.g. '女性2').
    Keep configs human-friendly, but map known JP aliases to CLI-safe names.
    """
    value = (name or "").strip()
    if not value:
        return value

    # Normalize common JP variants (full-width digits/spaces) so configs can use "男性３" etc.
    value = value.replace("　", " ")
    value = value.translate(
        str.maketrans(
            {
                "０": "0",
                "１": "1",
                "２": "2",
                "３": "3",
                "４": "4",
                "５": "5",
                "６": "6",
                "７": "7",
                "８": "8",
                "９": "9",
            }
        )
    )
    value = re.sub(r"\s+", " ", value).strip()

    # Pattern-based mapping (handles "男性 3" / "男性3" / "男性３" etc.)
    m = re.match(r"^(男性|女性)\s*([1-3])$", value)
    if m:
        kind, idx = m.group(1), m.group(2)
        if kind == "男性":
            return f"Japanese Male {idx}"
        return f"Japanese Female {idx}"

    alias = {
        "男性1": "Japanese Male 1",
        "男性2": "Japanese Male 2",
        "男性3": "Japanese Male 3",
        "女性1": "Japanese Female 1",
        "女性2": "Japanese Female 2",
        "女性3": "Japanese Female 3",
    }
    return alias.get(value, value)


def _scrub_voicepeak_output(text: object) -> str:
    """
    Drop known noisy lines from Voicepeak stdout/stderr.

    Note: The macOS crash dialog ("…が予期しない理由で終了しました") is a GUI popup and
    cannot be suppressed by stdout filtering; preventing the crash (narrator name normalization)
    is the primary fix.
    """
    if text is None:
        return ""
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8", errors="replace")
        except Exception:
            text = text.decode(errors="replace")
    elif not isinstance(text, str):
        text = str(text)
    if not text:
        return ""
    noisy_substrings = (
        "Voicepeakが予期しない理由で終了しました",
        "iconv_open is not supported",
    )
    kept: List[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if any(s in line for s in noisy_substrings):
            continue
        kept.append(raw)
    return "\n".join(kept).strip()


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
                # Optional: fail-fast to avoid silently producing broken audio.
                if _env_truthy("VOICEPEAK_CLI_NO_SILENCE_FALLBACK", default=False):
                    raise
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
    timeout_sec = _env_float("VOICEPEAK_CLI_TIMEOUT_SEC", 20.0)
    retry_count = max(0, _env_int("VOICEPEAK_CLI_RETRY_COUNT", 2))
    retry_sleep = max(0.0, _env_float("VOICEPEAK_CLI_RETRY_SLEEP_SEC", 0.2))
    # Small base cooldown helps reduce crash frequency across hundreds of invocations.
    cooldown = max(0.0, _env_float("VOICEPEAK_CLI_COOLDOWN_SEC", 0.2))

    # Defensive clamping (VOICEPEAK contract)
    speed = _clamp_int(speed, 50, 200)
    pitch = _clamp_int(pitch, -300, 300)

    tmp_text: Path | None = None
    try:
        # Using -t (text file) is more stable than passing UTF-8 text via argv (-s) on macOS.
        out_wav.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            suffix=".txt",
            prefix=f"{out_wav.stem}_voicepeak_",
            dir=str(out_wav.parent),
            delete=False,
        ) as f:
            f.write(line)
            tmp_text = Path(f.name)

        cmd = [
            str(bin_path),
            "voicepeak",
            "-t",
            str(tmp_text),
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

        last_err: VoicepeakCLIError | None = None
        for attempt in range(retry_count + 1):
            try:
                with _voicepeak_global_lock():
                    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
            except subprocess.TimeoutExpired as exc:
                last_err = VoicepeakCLIError(
                    "\n".join(
                        [
                            f"Voicepeak CLI timed out (>{timeout_sec:.1f}s)",
                            f"CMD: {' '.join(cmd)}",
                            f"STDOUT: {_scrub_voicepeak_output((exc.stdout or '').strip())}",
                            f"STDERR: {_scrub_voicepeak_output((exc.stderr or '').strip())}",
                        ]
                    )
                )
            else:
                if completed.returncode == 0 and out_wav.exists():
                    if cooldown > 0:
                        time.sleep(cooldown)
                    return
                last_err = VoicepeakCLIError(
                    "\n".join(
                        [
                            f"Voicepeak CLI failed (exit={completed.returncode})",
                            f"CMD: {' '.join(cmd)}",
                            f"STDOUT: {_scrub_voicepeak_output(completed.stdout.strip())}",
                            f"STDERR: {_scrub_voicepeak_output(completed.stderr.strip())}",
                        ]
                    )
                )

            if attempt < retry_count:
                # brief backoff to reduce crash/hang frequency across many invocations
                time.sleep(max(retry_sleep * (attempt + 1), cooldown))
                continue
            raise last_err or VoicepeakCLIError("Voicepeak CLI failed (unknown)")
    finally:
        try:
            if tmp_text and tmp_text.exists():
                tmp_text.unlink()
        except Exception:
            pass


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
