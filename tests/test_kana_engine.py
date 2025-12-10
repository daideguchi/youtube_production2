import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts import kana_engine  # noqa: E402


class DummyVVClient:
    def __init__(self, *args, **kwargs):
        pass

    def audio_query(self, text, speaker_id):
        return {"kana": f"DUMMY-{text}"}


def test_voicevox_kana_monkeypatch(monkeypatch):
    monkeypatch.setenv("AOYAMA_SPEAKER_ID", "123")
    monkeypatch.setattr(kana_engine, "VoicevoxClient", DummyVVClient)

    res = kana_engine.build_kana_engine(engine="voicevox", a_text="テスト", tokens=[], cfg=None)
    assert res["raw"] == "DUMMY-テスト"
    assert res["normalized"] == "DUMMY-テスト"  # ハイフンはnormalize対象外
    assert res["reading_source"] == "voicevox"


def test_voicepeak_mecab_concat():
    tokens = [
        {"reading_mecab": "テスト"},
        {"reading_mecab": "デス"},
    ]
    res = kana_engine.build_kana_engine(engine="voicepeak", a_text="dummy", tokens=tokens, reading_source="mecab")
    assert res["raw"] == "テストデス"
    assert res["normalized"] == "テストデス"
    assert res["reading_source"] == "mecab"


def test_normalize_kana_strips_symbols():
    assert kana_engine.normalize_kana("テ/ス'ト、 ") == "テスト"
