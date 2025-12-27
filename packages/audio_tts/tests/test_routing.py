import pytest

from factory_common.paths import audio_pkg_root

ROOT = audio_pkg_root()

from audio_tts.tts.routing import load_routing_config, decide_engine, resolve_voicevox_speaker_id


def test_decide_engine():
    cfg = load_routing_config(ROOT / "configs" / "routing.json")
    assert decide_engine("CH06", "033", cfg) == "voicevox"
    assert decide_engine("CH01", "192", cfg) == "voicepeak"
    assert decide_engine("CH99", "001", cfg) == "voicevox"


def test_resolve_voicevox_speaker_id_env_required(monkeypatch):
    cfg = load_routing_config(ROOT / "configs" / "routing.json")
    monkeypatch.delenv("AOYAMA_SPEAKER_ID", raising=False)
    with pytest.raises(ValueError):
        resolve_voicevox_speaker_id(cfg)

    monkeypatch.setenv("AOYAMA_SPEAKER_ID", "5")
    assert resolve_voicevox_speaker_id(cfg) == 5
