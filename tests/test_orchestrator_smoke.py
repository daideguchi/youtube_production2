import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

import audio.tts.orchestrator as orch  # noqa: E402


class DummyLLM:
    def __call__(self, payload):
        # echo write_mode original except first token to hiragana
        anns = []
        for t in payload["tokens"]:
            anns.append(
                {
                    "index": t["index"],
                    "surface": t.get("surface"),
                    "llm_reading_kana": t.get("reading_mecab") or t.get("surface"),
                    "write_mode": "hiragana" if t["index"] == 0 else "original",
                    "risk_level": 1,
                    "reason": "",
                }
            )
        return {"token_annotations": anns}


class DummyQA:
    def __call__(self, payload):
        return {"issues": []}


def test_orchestrator_smoke_voicevox(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # monkeypatch Voicevox synthesis to avoid network
    class DummyVVResult:
        def __init__(self):
            self.wav_path = tmp_path / "dummy.wav"
            self.sample_rate = 24000
            self.duration_sec = 1.0
            self.accent_phrases = []

    def dummy_synth(*args, **kwargs):
        out = tmp_path / "dummy.wav"
        out.write_bytes(b"RIFFxxxxWAVE")  # minimal marker; not a valid wav but unused
        return DummyVVResult()

    monkeypatch.setenv("AOYAMA_SPEAKER_ID", "1")
    monkeypatch.setattr(orch, "voicevox_synthesis", lambda *a, **k: dummy_synth())
    monkeypatch.setattr(orch, "tokenize_with_mecab", lambda text: [{"index": 0, "surface": text, "char_start": 0, "char_end": len(text), "reading_mecab": text}])
    monkeypatch.setattr(orch, "build_kana_engine", lambda **k: {"raw": "DUMMY", "normalized": "DUMMY", "reading_source": "voicevox"})
    monkeypatch.setattr(orch, "chunk_b_text", lambda x: [])

    res = orch.run_tts_pipeline(
        channel="CH99",
        video_no="001",
        script_id="CH99-001",
        a_text="テスト",
        output_audio_path=tmp_path / "out.wav",
        log_path=tmp_path / "log.json",
        llm_annotate_fn=DummyLLM(),
        qa_check_fn=DummyQA(),
        reading_source_override="voicevox",
    )

    assert res.b_text.startswith("て")
    assert res.engine == "voicevox"
    assert Path(res.audio_meta["wav_path"]).exists()
