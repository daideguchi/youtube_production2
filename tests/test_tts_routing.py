import json
import os
import sys
from pathlib import Path

import pytest


# Ensure the package path is importable when running tests from repo root.
REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_ROOT = REPO_ROOT / "commentary_01_srtfile_v2"
if str(PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(PKG_ROOT))

from audio.tts.routing import (  # noqa: E402
    decide_engine,
    load_routing_config,
    resolve_voicevox_speaker_id,
)


def _make_config(tmp_path: Path) -> Path:
    payload = {
        "engine_default": "voicevox",
        "channel_override": {"CH01": "voicepeak"},
        "script_override": {"CH06-033": "voicevox"},
        "voicevox": {
            "url": "http://127.0.0.1:50021",
            "speaker_env": "TEST_VV_SPK",
            "fallback_speaker_id": 999,
            "character": "青山流星",
        },
        "voicepeak": {
            "binary_path": "/Applications/voicepeak.app/Contents/MacOS/voicepeak",
            "default_narrator": "Japanese Male 3",
            "default_speed": 100,
            "default_pitch": 0,
            "default_emotion": "",
            "reading_source": "mecab",
        },
    }
    path = tmp_path / "routing.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_decide_engine_priority(tmp_path: Path):
    cfg_path = _make_config(tmp_path)
    cfg = load_routing_config(cfg_path)

    # script override wins
    assert decide_engine("CH06", "033", cfg) == "voicevox"
    # channel override next
    assert decide_engine("CH01", "192", cfg) == "voicepeak"
    # fallback to default
    assert decide_engine("CH99", "001", cfg) == "voicevox"


def test_resolve_voicevox_speaker_id_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg_path = _make_config(tmp_path)
    cfg = load_routing_config(cfg_path)

    # fallback when env not set
    monkeypatch.delenv("TEST_VV_SPK", raising=False)
    assert resolve_voicevox_speaker_id(cfg) == 999

    # env takes precedence
    monkeypatch.setenv("TEST_VV_SPK", "42")
    assert resolve_voicevox_speaker_id(cfg) == 42

    # invalid env raises
    monkeypatch.setenv("TEST_VV_SPK", "not_int")
    with pytest.raises(ValueError):
        resolve_voicevox_speaker_id(cfg)
