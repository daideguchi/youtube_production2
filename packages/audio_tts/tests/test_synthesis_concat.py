from pathlib import Path
import wave


def _make_silent_wav(path: Path, seconds: float, framerate: int = 16000):
    nframes = int(seconds * framerate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * nframes)


def test_concat_wavs():
    from audio_tts.tts.synthesis import _concat_wavs

    tmp = Path("tmp_test_concat")
    tmp.mkdir(exist_ok=True)
    w1 = tmp / "c1.wav"
    w2 = tmp / "c2.wav"
    _make_silent_wav(w1, 0.5)
    _make_silent_wav(w2, 0.25)
    out = tmp / "out.wav"

    meta = _concat_wavs([w1, w2], out)
    assert out.exists()
    assert 0.74 < meta["duration_sec"] < 0.76
    assert meta["sample_rate"] == 16000

    # cleanup
    for p in [w1, w2, out]:
        p.unlink(missing_ok=True)
    tmp.rmdir()
