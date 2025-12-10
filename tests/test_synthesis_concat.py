import sys
import wave
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts.synthesis import _concat_wavs  # noqa: E402


def _make_silent_wav(path: Path, seconds: float, framerate: int = 16000):
    nframes = int(seconds * framerate)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(framerate)
        wf.writeframes(b"\x00\x00" * nframes)


def test_concat_wavs(tmp_path: Path):
    w1 = tmp_path / "c1.wav"
    w2 = tmp_path / "c2.wav"
    _make_silent_wav(w1, 0.5)
    _make_silent_wav(w2, 0.25)

    out = tmp_path / "out.wav"
    meta = _concat_wavs([w1, w2], out)

    assert out.exists()
    assert 0.74 < meta["duration_sec"] < 0.76
    assert meta["sample_rate"] == 16000
