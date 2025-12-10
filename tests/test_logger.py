import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts.logger import save_tts_log  # noqa: E402


def test_save_tts_log(tmp_path: Path):
    out = tmp_path / "log.json"
    save_tts_log(
        out_path=out,
        channel="CH01",
        video_no="001",
        script_id="CH01-001",
        engine="voicevox",
        a_text="A",
        b_text="B",
        tokens=[{"surface": "A", "index": 0}],
        kana_engine={"raw": "RAW", "normalized": "RAW", "reading_source": "voicevox"},
        annotations={"token_annotations": []},
        b_text_build_log=[],
        audio_meta={"wav_path": "audio.wav", "sample_rate": 24000, "duration_sec": 1.0},
        engine_metadata={"accent_phrases": []},
        meta={"silence_tags": [], "warnings": []},
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["engine"] == "voicevox"
    assert data["audio"]["wav_path"] == "audio.wav"
